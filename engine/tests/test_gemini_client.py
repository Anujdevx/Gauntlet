"""Gemini client behaviour against a mock transport. No network, no key."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from oa_engine.llm import GeminiClient, LLMError, LLMOutputError


class Answer(BaseModel):
    value: int


def ok(text: str, *, thought: str | None = None) -> httpx.Response:
    parts = [{"text": thought, "thought": True}] if thought else []
    parts.append({"text": text})
    return httpx.Response(
        200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": parts}}]}
    )


def client(handler, models="m1,m2", **kw) -> GeminiClient:  # type: ignore[no-untyped-def]
    return GeminiClient(
        api_key="SECRET-KEY",
        model=models,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        **kw,
    )


def ask(c: GeminiClient) -> Answer:
    return c.generate(system="s", prompt="p", schema=Answer)


def test_returns_validated_object_and_skips_thought_parts() -> None:
    c = client(lambda r: ok('{"value": 7}', thought="hmm"))
    assert ask(c).value == 7


def test_key_travels_in_a_header_never_in_the_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok('{"value": 1}')

    ask(client(handler))
    assert "SECRET-KEY" not in str(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == "SECRET-KEY"


def test_schema_is_sent_for_structured_output() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return ok('{"value": 1}')

    ask(client(handler))
    config = bodies[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"  # type: ignore[index]
    assert "value" in config["responseJsonSchema"]["properties"]  # type: ignore[index]


def test_overloaded_model_falls_through_to_the_next() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "m1" in request.url.path:
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return ok('{"value": 2}')

    assert ask(client(handler)).value == 2
    assert sum("m1" in c for c in calls) == 3  # initial try + 2 retries
    assert any("m2" in c for c in calls)


def test_quota_error_skips_to_next_model_without_retrying() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "m1" in request.url.path:
            return httpx.Response(429, json={"error": {"message": "quota"}})
        return ok('{"value": 3}')

    assert ask(client(handler)).value == 3
    assert sum("m1" in c for c in calls) == 1


def test_all_models_down_raises_llm_error() -> None:
    c = client(lambda r: httpx.Response(503, json={"error": {"message": "busy"}}))
    with pytest.raises(LLMError, match="every model is unavailable"):
        ask(c)


def test_client_error_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    with pytest.raises(LLMError, match="400"):
        ask(client(handler))
    assert len(calls) == 1


def test_wrong_shape_is_an_output_error_the_pipeline_can_retry() -> None:
    with pytest.raises(LLMOutputError):
        ask(client(lambda r: ok('{"value": "not an int"}')))


def test_truncated_generation_is_an_output_error() -> None:
    truncated = httpx.Response(
        200, json={"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}
    )
    with pytest.raises(LLMOutputError, match="MAX_TOKENS"):
        ask(client(lambda r: truncated))


def test_empty_key_is_refused_at_construction() -> None:
    with pytest.raises(LLMError, match="empty"):
        GeminiClient(api_key="", model="m")
