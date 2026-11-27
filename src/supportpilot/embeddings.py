"""Turn text into embeddings (lists of numbers) with Gemini.

Texts with similar meaning get similar numbers, so "my food was cold" lands near the cold-food
policy even though they share few words. That is how policy search works.

Documents and search queries use different `task_type` values. Google trains the model so a
question and the passage that answers it land close together when each is labelled correctly.
"""

from typing import Protocol

from google import genai
from google.genai import types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from supportpilot.config import settings
from supportpilot.llm import RateLimiter, is_retryable


class Embedder(Protocol):
    """What the rest of the app depends on. Tests pass in a fake with the same two methods."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class GeminiEmbedder:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        key = api_key or settings.gemini_api_key
        if not key:
            raise ValueError("GEMINI_API_KEY is empty. Put your key in .env")
        self._client = genai.Client(api_key=key)
        self._model = model or settings.embedding_model
        # Embedding calls have their own quota, but we reuse the same polite spacing.
        self._limiter = RateLimiter(settings.llm_max_requests_per_minute)

    @retry(
        retry=retry_if_exception(is_retryable),  # same rule as the LLM client: 429 and 5xx only
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        self._limiter.wait()
        response = self._client.models.embed_content(
            model=self._model,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type, output_dimensionality=settings.embedding_dims
            ),
        )
        vectors = [list(e.values) for e in response.embeddings or []]
        if len(vectors) != len(texts):
            raise ValueError(f"Asked for {len(texts)} embeddings, got {len(vectors)}")
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]
