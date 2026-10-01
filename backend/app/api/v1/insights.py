"""Phase 7 LLM endpoints: streaming assistant (SSE), RAG answers, policy brief, what-if explanation, usage stats.

All LLM output carries a `label` (ai_generated, model, provider, cached, demo_mode). Calls are rate limited per user.
"""

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.api.v1.forecasts import WhatIfRequest, planner
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.ratelimit import rate_limit
from app.db.models import User, UserRole
from app.db.session import get_read_db
from app.llm import assistant, briefs, rag, service
from app.llm.base import LLMError
from app.schemas.common import Envelope, ok
from app.services import cube as cubes
from app.services import forecasting

router = APIRouter(tags=["llm"])
llm_limit = rate_limit("llm", lambda: get_settings().llm_rate_limit_per_minute)
analyst = require_roles(UserRole.ANALYST)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=1500)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


def _sse(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    try:
        for ev in events:
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
    except Exception:  # never leave the browser hanging on a half-open stream
        yield f"data: {json.dumps({'type': 'error', 'message': 'Unexpected error while answering.'})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/assistant/chat")
def chat_stream(body: ChatRequest, user: User = Depends(get_current_user), _: None = Depends(llm_limit)) -> StreamingResponse:
    """Server-Sent Events stream: `tool` events, `token` events (answer text), then `done` (data sources + AI label)."""
    events = assistant.run(body.message, [m.model_dump() for m in body.history], user)
    return StreamingResponse(_sse(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/assistant/ask", response_model=Envelope[dict[str, Any]])
def ask(body: ChatRequest, user: User = Depends(get_current_user), _: None = Depends(llm_limit)) -> dict[str, object]:
    """Same as /assistant/chat but returns one JSON answer (no streaming)."""
    answer, tools_used, label, error = "", [], {}, None
    for ev in assistant.run(body.message, [m.model_dump() for m in body.history], user):
        if ev["type"] == "token":
            answer += ev["text"]
        elif ev["type"] == "done":
            tools_used, label = ev["tools_used"], ev["label"]
        elif ev["type"] == "error":
            error = ev["message"]
    if error:
        raise ApiError(503, "llm_unavailable", error)
    return ok({"answer": answer, "tools_used": tools_used, "label": label})


@router.post("/insights/ask-documents", response_model=Envelope[dict[str, Any]])
def ask_documents(body: QuestionRequest, user: User = Depends(get_current_user), _: None = Depends(llm_limit)) -> dict[str, object]:
    """RAG: answer from the policy documents with numbered source citations."""
    try:
        return ok(rag.answer(body.question, user_id=user.id))
    except LLMError as exc:
        raise ApiError(503, "llm_unavailable", str(exc)) from exc


@router.post("/insights/documents/embed", response_model=Envelope[dict[str, int]])
def embed_documents(_: User = Depends(analyst)) -> dict[str, object]:
    """Embed document chunks that have no vector yet (Hugging Face API; up to 200 per call)."""
    return ok(rag.embed_pending())


@router.get("/insights/brief", response_model=Envelope[dict[str, Any]])
def policy_brief(
    refresh: bool = Query(False, description="Regenerate instead of using the cached brief"),
    user: User = Depends(planner), _: None = Depends(llm_limit), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    """Monthly executive policy brief (AI-written from analytics-engine numbers). Cached for 24 h."""
    try:
        return ok(briefs.policy_brief(db, cubes.get_cube(db), refresh=refresh, user_id=user.id))
    except LLMError as exc:
        raise ApiError(503, "llm_unavailable", str(exc)) from exc


@router.post("/scenarios/explain", response_model=Envelope[dict[str, Any]])
def explain_scenario(body: WhatIfRequest, user: User = Depends(planner), _: None = Depends(llm_limit),
                     db: Session = Depends(get_read_db)) -> dict[str, object]:
    """Run the what-if model (Python numbers) and have the LLM explain the result."""
    result = forecasting.what_if(db, cubes.get_cube(db), category=body.category, crew_change_pct=body.crew_change_pct,
                                 demand_change_pct=body.demand_change_pct, horizon=body.horizon_months)
    if "error" in result:
        raise ApiError(422, "insufficient_data", result["error"])
    try:
        return ok({"result": result, **briefs.explain_what_if(result, user_id=user.id)})
    except LLMError as exc:
        raise ApiError(503, "llm_unavailable", str(exc)) from exc


@router.get("/llm/status", response_model=Envelope[dict[str, Any]])
def llm_status(_: User = Depends(get_current_user)) -> dict[str, object]:
    s = get_settings()
    return ok({"provider": "mock" if s.llm_demo_mode else s.llm_provider, "demo_mode": s.llm_demo_mode,
               "model": s.llm_model or None, "fast_model": s.llm_fast_model or None, "rate_limit_per_minute": s.llm_rate_limit_per_minute})


@router.get("/llm/usage", response_model=Envelope[dict[str, Any]])
def llm_usage(days: int = Query(7, ge=1, le=90), _: User = Depends(analyst), db: Session = Depends(get_read_db)) -> dict[str, object]:
    """LLM monitoring: calls, tokens, latency and cache hit rate (from llm_call_logs)."""
    p = {"d": days}
    tot = db.execute(text(
        "SELECT count(*), COALESCE(sum(prompt_tokens),0), COALESCE(sum(completion_tokens),0), COALESCE(avg(latency_ms) FILTER (WHERE NOT cache_hit),0), "
        "count(*) FILTER (WHERE cache_hit), count(*) FILTER (WHERE status <> 'ok') FROM llm_call_logs "
        "WHERE created_at > now() - make_interval(days => :d)"), p).one()
    by = db.execute(text(
        "SELECT purpose, model, count(*), COALESCE(sum(prompt_tokens + completion_tokens),0), COALESCE(avg(latency_ms) FILTER (WHERE NOT cache_hit),0), "
        "count(*) FILTER (WHERE cache_hit) FROM llm_call_logs WHERE created_at > now() - make_interval(days => :d) "
        "GROUP BY 1, 2 ORDER BY 3 DESC"), p).all()
    daily = db.execute(text(
        "SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD'), count(*), COALESCE(sum(prompt_tokens + completion_tokens),0) FROM llm_call_logs "
        "WHERE created_at > now() - make_interval(days => :d) GROUP BY 1 ORDER BY 1"), p).all()
    n = int(tot[0])
    return ok({
        "days": days, "calls": n, "prompt_tokens": int(tot[1]), "completion_tokens": int(tot[2]), "avg_latency_ms": round(float(tot[3])),
        "cache_hit_rate_pct": round(100 * tot[4] / n, 1) if n else 0.0, "errors": int(tot[5]),
        "by_purpose": [{"purpose": r[0], "model": r[1], "calls": int(r[2]), "tokens": int(r[3]), "avg_latency_ms": round(float(r[4])), "cache_hits": int(r[5])} for r in by],
        "daily": [{"day": r[0], "calls": int(r[1]), "tokens": int(r[2])} for r in daily],
    })
