"""Groq implementation of LLMProvider. The API key is read from settings (backend only).

Rate limits: HTTP 429 is retried with the server's retry-after (or exponential backoff). Models are never hardcoded:
they come from LLM_MODEL / LLM_FAST_MODEL.
"""

import json
import logging
import time
from collections.abc import Iterator, Sequence
from typing import Any

from groq import APIConnectionError, APIStatusError, Groq, RateLimitError

from app.core.config import get_settings
from app.llm.base import LLMError, LLMProvider, LLMResponse, Message, ToolCall

log = logging.getLogger(__name__)


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str | None = None, *, client: Any = None, sleep: Any = time.sleep) -> None:
        s = get_settings()
        self.s = s
        self.client = client or Groq(api_key=api_key or s.groq_api_key, max_retries=0, timeout=s.llm_timeout_s)
        self._sleep = sleep

    def _kwargs(self, model: str | None, temperature: float | None, max_tokens: int | None) -> dict[str, Any]:
        m = model or self.s.llm_model
        kw: dict[str, Any] = {
            "model": m, "temperature": self.s.llm_temperature if temperature is None else temperature,
            "max_completion_tokens": max_tokens or self.s.llm_max_tokens,
        }
        if "gpt-oss" in m:  # reasoning models: keep thinking short so latency and token use stay low
            kw["reasoning_effort"] = self.s.llm_reasoning_effort
        return kw

    def _call(self, fn: Any, **kw: Any) -> Any:
        last = ""
        for attempt in range(self.s.llm_max_retries + 1):
            try:
                return fn(**kw)
            except RateLimitError as exc:
                retry_after = None
                try:
                    retry_after = float(exc.response.headers.get("retry-after", ""))
                except (ValueError, AttributeError, TypeError):
                    pass
                delay = min(retry_after if retry_after else 2.0 ** (attempt + 1), 60.0)
                last = f"429 rate limited: {exc}"
            except APIConnectionError as exc:
                delay, last = min(2.0**attempt, 20.0), f"connection error: {exc}"
            except APIStatusError as exc:
                if exc.status_code >= 500:
                    delay, last = min(2.0**attempt, 20.0), f"HTTP {exc.status_code}"
                else:
                    raise LLMError(f"Groq HTTP {exc.status_code}: {exc.message}") from exc
            log.warning("Groq %s (attempt %d/%d), waiting %.0fs", last, attempt + 1, self.s.llm_max_retries + 1, delay)
            self._sleep(delay)
        raise LLMError(f"Groq request failed after {self.s.llm_max_retries + 1} attempts: {last}")

    @staticmethod
    def _response(r: Any, started: float, provider: str) -> LLMResponse:
        msg = r.choices[0].message
        calls = [
            ToolCall(c.id, c.function.name, _loads(c.function.arguments)) for c in (getattr(msg, "tool_calls", None) or [])
        ]
        u = getattr(r, "usage", None)
        return LLMResponse(
            text=msg.content or "", model=r.model, provider=provider, prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(u, "completion_tokens", 0) or 0, latency_ms=int((time.monotonic() - started) * 1000),
            tool_calls=calls,
        )

    def chat(
        self, messages: Sequence[Message], *, model: str | None = None, json_mode: bool = False,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> LLMResponse:
        kw = self._kwargs(model, temperature, max_tokens)
        if json_mode:
            kw["response_format"] = {"type": "json_object"}
        t = time.monotonic()
        r = self._call(self.client.chat.completions.create, messages=list(messages), **kw)
        return self._response(r, t, self.name)

    def chat_with_tools(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]], *, model: str | None = None,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> LLMResponse:
        kw = self._kwargs(model, temperature, max_tokens)
        t = time.monotonic()
        r = self._call(self.client.chat.completions.create, messages=list(messages), tools=list(tools), tool_choice="auto", **kw)
        return self._response(r, t, self.name)

    def stream(
        self, messages: Sequence[Message], *, model: str | None = None, temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        kw = self._kwargs(model, temperature, max_tokens)
        stream = self._call(self.client.chat.completions.create, messages=list(messages), stream=True, **kw)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def _loads(s: str | None) -> dict[str, Any]:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}
