"""Entry point used by the rest of the app: provider selection, PII stripping, caching and call logging."""

import logging
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.pii import strip_pii
from app.db.models import LLMCallLog
from app.db.session import AutocommitSession
from app.llm.base import LLMError, LLMProvider, LLMResponse
from app.llm.mock_provider import MockLLMProvider
from app.services import cache

log = logging.getLogger(__name__)
PROMPT_DIR = Path(__file__).parent / "prompts"
_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Groq when configured, otherwise the mock (the UI shows a demo-mode banner via /health)."""
    global _provider
    if _provider is None:
        s = get_settings()
        if s.llm_demo_mode:
            _provider = MockLLMProvider()
        else:
            from app.llm.groq_provider import GroqProvider

            _provider = GroqProvider()
    return _provider


def set_provider(p: LLMProvider | None) -> None:
    """Tests inject a mock/fake client here."""
    global _provider
    _provider = p


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")


def model_for(kind: str) -> str:
    s = get_settings()
    if kind == "fast":
        return s.llm_fast_model or s.llm_model
    return s.llm_model


def log_call(r: LLMResponse, purpose: str, *, cache_hit: bool, status: str = "ok", user_id: int | None = None) -> None:
    try:
        with AutocommitSession() as db:
            db.add(LLMCallLog(provider=r.provider, model=r.model, purpose=purpose, prompt_tokens=r.prompt_tokens,
                              completion_tokens=r.completion_tokens, latency_ms=r.latency_ms, cache_hit=cache_hit,
                              status=status, user_id=user_id))
            db.commit()
    except Exception:  # logging must never break a user request
        log.exception("could not log LLM call")


def generate(
    purpose: str, prompt: str, *, system: str | None = None, kind: str = "main", json_mode: bool = False,
    max_tokens: int | None = None, cache_ttl: int | None = None, user_id: int | None = None,
    sanitize: bool = True,
) -> LLMResponse:
    """One cached, logged completion. The cache key is a hash of (purpose, model, system, prompt)."""
    s = get_settings()
    provider = get_provider()
    model = model_for(kind) if provider.name == "groq" else "mock-demo"
    if sanitize:
        prompt = strip_pii(prompt)
    ttl = s.llm_cache_ttl_seconds if cache_ttl is None else cache_ttl
    key = cache.make_key("llm", purpose, model, system, prompt, json_mode)
    if ttl > 0:
        hit = cache.get(key)
        if hit is not None:
            r = LLMResponse(**{**hit, "cached": True, "tool_calls": []})
            log_call(r, purpose, cache_hit=True, user_id=user_id)
            return r
    try:
        r = provider.complete(prompt, system=system, model=model if provider.name == "groq" else None, json_mode=json_mode,
                              max_tokens=max_tokens)
    except LLMError:
        log_call(LLMResponse("", model, provider.name), purpose, cache_hit=False, status="error", user_id=user_id)
        raise
    log_call(r, purpose, cache_hit=False, user_id=user_id)
    if ttl > 0:
        cache.set(key, {k: v for k, v in r.__dict__.items() if k not in ("tool_calls", "cached")}, ttl)
    return r


def label(r: LLMResponse) -> dict[str, Any]:
    """Provenance shown next to every AI-generated output."""
    return {"ai_generated": True, "model": r.model, "provider": r.provider, "cached": r.cached,
            "demo_mode": r.provider == "mock"}
