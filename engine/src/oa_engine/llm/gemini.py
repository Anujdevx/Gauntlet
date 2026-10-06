"""Minimal Gemini client returning schema-validated Pydantic objects.

Deliberately thin: one POST, structured JSON out. An SDK would add a dependency for
a single endpoint, and the one thing that matters here -- never putting the API key
in a URL where proxies and access logs record it -- is a header, not a library.

Failures split in two, because they need different handling:

* **Transport / quota** (429, 5xx, timeouts): retried with exponential backoff, then
  raised as :class:`LLMError`.
* **Bad output** (invalid JSON, schema mismatch): raised as :class:`LLMError` with the
  validation message, which the pipeline feeds back to the model on the next attempt.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

__all__ = ["GeminiClient", "LLMError", "LLMOutputError"]

T = TypeVar("T", bound=BaseModel)

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_OK = 200
_QUOTA = 429
_RETRYABLE = {_QUOTA, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """The model could not be reached or produced nothing (transport, quota, block)."""


class LLMOutputError(LLMError):
    """The model answered, but not in the requested shape. Worth retrying with feedback."""


class GeminiClient:
    """Structured-output client for the Gemini ``generateContent`` API."""

    def __init__(
        self,
        *,
        api_key: SecretStr | str,
        model: str | Sequence[str],
        timeout_s: float = 180.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key:
            raise LLMError("GEMINI_API_KEY is empty")
        self._key = key
        # A comma-separated string or a list. Free-tier flash models are routinely
        # saturated (503) at different times, so a short fallback chain is the
        # difference between a run that finishes and one that stalls on one model.
        names = model.split(",") if isinstance(model, str) else list(model)
        self.models = [name.strip() for name in names if name.strip()]
        if not self.models:
            raise LLMError("no Gemini model configured")
        self.model = self.models[0]
        self._max_retries = max_retries
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        temperature: float = 0.7,
    ) -> T:
        """Ask the model for one object matching ``schema``.

        Raises:
            LLMError: Transport failure after retries, a blocked or truncated
                response, or output that is not valid JSON for ``schema``.
        """
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema.model_json_schema(),
            },
        }
        text = self._post(body)
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMOutputError(f"output did not match the schema: {exc}") from exc

    def _post(self, body: dict[str, Any]) -> str:
        """POST to each model in turn; fall through only on overload or quota."""
        failures: list[str] = []
        for model in self.models:
            try:
                return self._post_one(model, body)
            except _OverloadedError as exc:
                failures.append(f"{model}: {exc}")
        raise LLMError("every model is unavailable: " + "; ".join(failures))

    def _post_one(self, model: str, body: dict[str, Any]) -> str:
        url = _ENDPOINT.format(model=model)
        # Header, not `?key=`: query strings land in logs, proxies and tracebacks.
        headers = {"x-goog-api-key": self._key, "content-type": "application/json"}
        last = "no attempt made"
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.post(url, headers=headers, content=json.dumps(body))
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}"
            else:
                if response.status_code == _OK:
                    return _extract_text(response.json())
                last = f"HTTP {response.status_code}: {_error_message(response)}"
                if response.status_code not in _RETRYABLE:
                    raise LLMError(last)
                if response.status_code == _QUOTA:
                    # Quota will not clear in seconds; do not burn retries on it.
                    raise _OverloadedError(last)
            if attempt < self._max_retries:
                self._sleep(min(2.0 * 2**attempt, 30.0))
        raise _OverloadedError(last)


class _OverloadedError(Exception):
    """This model is saturated or out of quota; another may still answer."""


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError):
        return response.text[:300]


def _extract_text(payload: dict[str, Any]) -> str:
    """Join the answer parts, skipping the model's visible 'thought' parts."""
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback", {})
        raise LLMError(f"no candidates returned (promptFeedback={feedback})")
    candidate = candidates[0]
    reason = candidate.get("finishReason", "STOP")
    if reason not in ("STOP", None):
        raise LLMOutputError(f"generation ended early: finishReason={reason}")
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise LLMOutputError("empty response")
    return text
