"""Policy store tests. A fake embedder replaces Gemini, so they are free and offline."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from supportpilot import models
from supportpilot.config import settings
from supportpilot.db import Base
from supportpilot.policies import (
    POLICIES_DIR,
    PolicyDoc,
    content_hash,
    cosine_similarity,
    load_policies,
    read_policy_files,
    search_policies,
)

VOCAB = ["late", "refund", "missing", "item", "cold", "photo", "rider", "payment", "app", "crash"]


def vectorize(text: str) -> list[float]:
    """A toy embedding: how many times each vocabulary word appears. Padded to the real size."""
    words = text.lower().split()
    counts = [float(words.count(w)) for w in VOCAB]
    return counts + [0.0] * (settings.embedding_dims - len(counts))


class FakeEmbedder:
    def __init__(self):
        self.document_batches: list[list[str]] = []
        self.queries: list[str] = []

    def embed_documents(self, texts):
        self.document_batches.append(list(texts))
        return [vectorize(t) for t in texts]

    def embed_query(self, text):
        self.queries.append(text)
        return vectorize(text)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


DOCS = [
    PolicyDoc("late", "Late delivery", "# Late delivery\nlate late refund"),
    PolicyDoc("missing", "Missing item", "# Missing item\nmissing item missing refund photo"),
    PolicyDoc("app", "App help", "# App help\napp crash app"),
]


def test_first_load_adds_everything_in_one_embedding_call(db):
    embedder = FakeEmbedder()
    stats = load_policies(db, embedder, DOCS)
    assert stats == {"added": 3, "updated": 0, "unchanged": 0, "removed": 0}
    assert len(embedder.document_batches) == 1
    assert len(db.scalars(select(models.PolicyChunk)).all()) == 3


def test_loading_again_does_not_call_the_embedder(db):
    load_policies(db, FakeEmbedder(), DOCS)
    embedder = FakeEmbedder()
    stats = load_policies(db, embedder, DOCS)
    assert stats == {"added": 0, "updated": 0, "unchanged": 3, "removed": 0}
    assert embedder.document_batches == []


def test_only_a_changed_document_is_embedded_again(db):
    load_policies(db, FakeEmbedder(), DOCS)
    edited = [DOCS[0], PolicyDoc("missing", "Missing item", "# Missing item\nnew text"), DOCS[2]]
    embedder = FakeEmbedder()
    stats = load_policies(db, embedder, edited)
    assert stats == {"added": 0, "updated": 1, "unchanged": 2, "removed": 0}
    assert embedder.document_batches == [["# Missing item\nnew text"]]
    chunk = db.scalar(select(models.PolicyChunk).where(models.PolicyChunk.slug == "missing"))
    assert chunk.content == "# Missing item\nnew text"
    assert chunk.content_hash == content_hash("# Missing item\nnew text")


def test_a_deleted_policy_file_is_removed_from_the_database(db):
    load_policies(db, FakeEmbedder(), DOCS)
    stats = load_policies(db, FakeEmbedder(), DOCS[:2])
    assert stats["removed"] == 1
    slugs = {c.slug for c in db.scalars(select(models.PolicyChunk))}
    assert slugs == {"late", "missing"}


def test_search_ranks_by_meaning_and_returns_the_best_first(db):
    embedder = FakeEmbedder()
    load_policies(db, embedder, DOCS)
    hits = search_policies(db, embedder, "my item is missing please refund", top_k=2)
    assert [h.slug for h in hits] == ["missing", "late"]
    assert hits[0].score > hits[1].score
    assert embedder.queries == ["my item is missing please refund"]


def test_search_respects_top_k_and_min_score(db):
    embedder = FakeEmbedder()
    load_policies(db, embedder, DOCS)
    assert len(search_policies(db, embedder, "late refund", top_k=1)) == 1
    hits = search_policies(db, embedder, "app crash", top_k=3, min_score=0.5)
    assert [h.slug for h in hits] == ["app"]


def test_cosine_similarity_basics():
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([0, 0], [1, 1]) == 0.0


def test_the_real_policy_files_are_well_formed():
    docs = read_policy_files()
    assert len(docs) == 16
    assert len({d.slug for d in docs}) == 16
    for doc in docs:
        assert doc.content.startswith("# ")
        assert doc.title
    assert POLICIES_DIR.is_dir()


def test_a_policy_file_without_a_title_is_rejected(tmp_path):
    (tmp_path / "bad.md").write_text("no title here")
    with pytest.raises(ValueError, match="Title"):
        read_policy_files(tmp_path)
