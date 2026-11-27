"""Policy store: load policy files into the database, and search them by meaning.

Run with:  uv run python -m supportpilot.policies
Loading is safe to repeat. Only files whose text changed are embedded again.
"""

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from supportpilot.embeddings import Embedder
from supportpilot.models import PolicyChunk

POLICIES_DIR = Path(__file__).resolve().parents[2] / "policies"


@dataclass
class PolicyDoc:
    slug: str
    title: str
    content: str


@dataclass
class PolicyHit:
    slug: str
    title: str
    content: str
    score: float  # cosine similarity: 1 = same meaning, 0 = unrelated


def read_policy_files(folder: Path = POLICIES_DIR) -> list[PolicyDoc]:
    docs = []
    for path in sorted(folder.glob("*.md")):
        text = path.read_text().strip()
        first_line = text.splitlines()[0]
        if not first_line.startswith("# "):
            raise ValueError(f"{path.name} must start with a '# Title' line")
        docs.append(PolicyDoc(slug=path.stem, title=first_line[2:].strip(), content=text))
    return docs


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_policies(session: Session, embedder: Embedder, docs: list[PolicyDoc]) -> dict[str, int]:
    """Make the database match `docs`. Returns how many were added, updated, unchanged, removed."""
    existing = {c.slug: c for c in session.scalars(select(PolicyChunk))}
    wanted = {d.slug for d in docs}
    stats = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0}

    changed = [
        d
        for d in docs
        if d.slug not in existing or existing[d.slug].content_hash != content_hash(d.content)
    ]
    # One call embeds every changed document at once.
    vectors = embedder.embed_documents([d.content for d in changed]) if changed else []
    new_vectors = {d.slug: v for d, v in zip(changed, vectors, strict=True)}

    for doc in docs:
        if doc.slug not in new_vectors:
            stats["unchanged"] += 1
            continue
        chunk = existing.get(doc.slug)
        if chunk is None:
            chunk = PolicyChunk(slug=doc.slug)
            session.add(chunk)
            stats["added"] += 1
        else:
            stats["updated"] += 1
        chunk.title = doc.title
        chunk.content = doc.content
        chunk.content_hash = content_hash(doc.content)
        chunk.embedding = new_vectors[doc.slug]

    gone = [slug for slug in existing if slug not in wanted]
    if gone:
        session.execute(delete(PolicyChunk).where(PolicyChunk.slug.in_(gone)))
        stats["removed"] = len(gone)
    session.flush()
    return stats


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def search_policies(
    session: Session, embedder: Embedder, query: str, *, top_k: int = 3, min_score: float = 0.0
) -> list[PolicyHit]:
    """Return the policies closest in meaning to `query`, best first.

    On Postgres the database does the ranking with pgvector's cosine distance. On SQLite (used
    in tests) there is no vector operator, so the same ranking is done in Python.
    """
    query_vec = embedder.embed_query(query)

    if session.get_bind().dialect.name == "postgresql":
        distance = PolicyChunk.embedding.cosine_distance(query_vec)
        rows = session.execute(
            select(PolicyChunk, distance.label("distance")).order_by(distance).limit(top_k)
        ).all()
        scored = [(chunk, 1 - dist) for chunk, dist in rows]
    else:
        chunks = session.scalars(select(PolicyChunk)).all()
        scored = [(c, cosine_similarity(query_vec, c.embedding)) for c in chunks]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        scored = scored[:top_k]

    return [
        PolicyHit(c.slug, c.title, c.content, round(score, 4))
        for c, score in scored
        if score >= min_score
    ]


def main() -> None:
    from supportpilot.db import get_session
    from supportpilot.embeddings import GeminiEmbedder

    docs = read_policy_files()
    with get_session() as session:
        stats = load_policies(session, GeminiEmbedder(), docs)
        session.commit()
    print(f"Policies loaded from {POLICIES_DIR}: {stats}")


if __name__ == "__main__":
    main()
