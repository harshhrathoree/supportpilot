"""LLM layer tests. A fake Google client is used, so they cost nothing and never hit the network."""

from types import SimpleNamespace

import pytest
from google.genai import errors
from pydantic import BaseModel

from supportpilot.llm import GeminiClient, LLMError, RateLimiter, Usage, is_retryable


class Answer(BaseModel):
    label: str


def api_error(code: int) -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": "x", "status": "X"}})


def ok_response(parsed, text="{}"):
    usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=20)
    return SimpleNamespace(parsed=parsed, text=text, usage_metadata=usage)


class FakeGoogle:
    """Stands in for genai.Client. `script` has one entry per call: an exception or a response."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # model name of each call
        self.models = self

    def generate_content(self, *, model, **kwargs):
        self.calls.append(model)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make_client(script):
    client = GeminiClient(
        api_key="test",
        model="main",
        fallback_model="backup",
        retry_wait_seconds=0,
        max_requests_per_minute=0,  # no limiting in tests
    )
    client._client = FakeGoogle(script)
    return client


def test_success_returns_parsed_object_and_usage():
    client = make_client([ok_response(Answer(label="late"))])
    result, usage = client.generate_structured(system="s", prompt="p", schema=Answer)
    assert result == Answer(label="late")
    assert (usage.model, usage.input_tokens, usage.output_tokens) == ("main", 100, 20)


def test_retries_a_503_then_succeeds_on_the_same_model():
    client = make_client([api_error(503), api_error(503), ok_response(Answer(label="x"))])
    result, _ = client.generate_structured(system="s", prompt="p", schema=Answer)
    assert result.label == "x"
    assert client._client.calls == ["main", "main", "main"]


def test_falls_back_to_second_model_after_retries_run_out():
    client = make_client([api_error(503)] * 3 + [ok_response(Answer(label="y"))])
    result, usage = client.generate_structured(system="s", prompt="p", schema=Answer)
    assert result.label == "y"
    assert usage.model == "backup"
    assert client._client.calls == ["main", "main", "main", "backup"]


def test_a_client_error_is_not_retried_and_does_not_fall_back():
    client = make_client([api_error(404)])
    with pytest.raises(LLMError):
        client.generate_structured(system="s", prompt="p", schema=Answer)
    assert client._client.calls == ["main"]


def test_both_models_failing_raises_llm_error():
    client = make_client([api_error(503)] * 6)
    with pytest.raises(LLMError, match="Both models failed"):
        client.generate_structured(system="s", prompt="p", schema=Answer)
    assert len(client._client.calls) == 6


def test_unparseable_answer_raises_llm_error():
    client = make_client([ok_response(None, text="not json")])
    with pytest.raises(LLMError, match="did not return valid Answer"):
        client.generate_structured(system="s", prompt="p", schema=Answer)


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.setattr("supportpilot.llm.settings.gemini_api_key", "")
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiClient()


def test_is_retryable_only_for_429_and_5xx():
    assert is_retryable(api_error(429))
    assert is_retryable(api_error(500))
    assert is_retryable(api_error(503))
    assert not is_retryable(api_error(400))
    assert not is_retryable(api_error(404))
    assert not is_retryable(ValueError("boom"))


def test_cost_is_computed_from_token_counts():
    usage = Usage(model="gemini-3.5-flash-lite", input_tokens=1_000_000, output_tokens=1_000_000)
    assert usage.cost_usd == pytest.approx(0.50)
    assert Usage(model="unknown-model", input_tokens=5, output_tokens=5).cost_usd == 0.0


class FakeTime:
    """A clock we control. sleep() just moves the clock forward, so tests take no real time."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_calls_evenly():
    t = FakeTime()
    limiter = RateLimiter(max_per_minute=12, clock=t.clock, sleep=t.sleep)  # 5s apart
    for _ in range(4):
        limiter.wait()
    assert t.sleeps == [pytest.approx(5.0)] * 3  # first call goes at once


def test_rate_limiter_does_not_sleep_if_enough_time_already_passed():
    t = FakeTime()
    limiter = RateLimiter(max_per_minute=12, clock=t.clock, sleep=t.sleep)
    limiter.wait()
    t.now += 30
    limiter.wait()
    assert t.sleeps == []


def test_rate_limiter_with_zero_is_disabled():
    t = FakeTime()
    limiter = RateLimiter(max_per_minute=0, clock=t.clock, sleep=t.sleep)
    for _ in range(10):
        limiter.wait()
    assert t.sleeps == []


def test_every_attempt_including_retries_waits_on_the_limiter():
    client = make_client([api_error(503), ok_response(Answer(label="x"))])
    waits = []
    client._limiter = SimpleNamespace(wait=lambda: waits.append(1))
    client.generate_structured(system="s", prompt="p", schema=Answer)
    assert len(waits) == 2
