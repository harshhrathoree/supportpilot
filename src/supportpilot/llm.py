"""The LLM layer: one small interface so the rest of the app never imports Google's SDK.

Other modules call `LLMClient.generate_structured(...)` and get back a validated Pydantic
object plus a record of what the call cost. To use a different provider later, we write
another class with the same method and change one line where the client is created.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from supportpilot.config import settings

T = TypeVar("T", bound=BaseModel)

# Approximate USD price per 1M tokens: (input, output). Used only for cost tracking.
# Update these if Google changes the pricing. Unknown models are counted as free.
PRICING_PER_M_TOKENS = {
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.10, 0.40),
}


@dataclass
class Usage:
    """What one LLM call consumed."""

    model: str
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float:
        price_in, price_out = PRICING_PER_M_TOKENS.get(self.model, (0.0, 0.0))
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000


class LLMError(Exception):
    """Raised when the model could not give us a usable answer."""


class RateLimiter:
    """Spaces calls at least 60/max_per_minute seconds apart, across all threads.

    Google's free tier allows about 15 requests per minute per model. Retrying after a
    429 is too late, because the limit only resets after a minute. Spacing calls out
    in advance avoids the error in the first place.
    """

    def __init__(
        self,
        max_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._interval = 60.0 / max_per_minute if max_per_minute > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next_allowed)
            self._next_allowed = start + self._interval
        if start > now:  # sleep outside the lock so other threads can reserve their slots
            self._sleep(start - now)


class LLMClient(Protocol):
    """What the rest of the app depends on. Any provider that has this method will do."""

    def generate_structured(
        self, *, system: str, prompt: str, schema: type[T]
    ) -> tuple[T, Usage]: ...


def is_retryable(exc: BaseException) -> bool:
    """Retry only problems that can go away by themselves.

    429 = rate limited, 5xx = Google is overloaded. A 400 or 404 (our mistake) will never
    succeed on retry, so it fails immediately.
    """
    return isinstance(exc, errors.APIError) and (exc.code == 429 or (exc.code or 0) >= 500)


class GeminiClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        retry_wait_seconds: float = 1.0,
        max_requests_per_minute: int | None = None,
    ):
        key = api_key or settings.gemini_api_key
        if not key:
            raise LLMError("GEMINI_API_KEY is empty. Put your key in .env")
        self._client = genai.Client(api_key=key)
        self.model = model or settings.llm_model
        self.fallback_model = fallback_model or settings.llm_fallback_model
        self._retry_wait = retry_wait_seconds
        self._limiter = RateLimiter(
            settings.llm_max_requests_per_minute
            if max_requests_per_minute is None
            else max_requests_per_minute
        )

    def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> tuple[T, Usage]:
        """Ask the model a question and get the answer back as an instance of `schema`.

        Tries the main model (with retries), then the fallback model.
        """
        try:
            return self._call(self.model, system, prompt, schema)
        except errors.APIError as primary_error:
            if not is_retryable(primary_error) or self.fallback_model == self.model:
                raise LLMError(f"{self.model} failed: {primary_error}") from primary_error
        try:
            return self._call(self.fallback_model, system, prompt, schema)
        except errors.APIError as fallback_error:
            raise LLMError(f"Both models failed. Last error: {fallback_error}") from fallback_error

    def _call(self, model: str, system: str, prompt: str, schema: type[T]) -> tuple[T, Usage]:
        @retry(
            retry=retry_if_exception(is_retryable),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=self._retry_wait, max=10),
            reraise=True,  # after the last attempt, raise the real error instead of a wrapper
        )
        def send() -> types.GenerateContentResponse:
            self._limiter.wait()  # every attempt, including retries, counts toward the limit
            return self._client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0,  # no randomness: same input gives (almost) the same answer
                ),
            )

        response = send()
        parsed = response.parsed
        if not isinstance(parsed, schema):
            raise LLMError(f"Model did not return valid {schema.__name__}: {response.text!r}")
        usage = response.usage_metadata
        return parsed, Usage(
            model=model,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
        )
