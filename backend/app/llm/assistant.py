"""'Ask the data' assistant: Groq tool calling over the safe registry in tools.py, yielding events for Server-Sent Events.

Events (dicts): {"type": "tool", "name", "args"} per tool call, {"type": "token", "text"} for the answer,
{"type": "done", "tools_used", "label"} at the end, {"type": "error", "message"} on failure.
If the model/provider rejects tool calling (HTTP 400), a JSON-mode planner is used instead: the model returns
{"function": ..., "args": ...} which is validated against the registry before it is executed.
"""

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from app.core.config import get_settings
from app.core.pii import strip_pii
from app.db.models import User
from app.db.session import AutocommitSession
from app.ingestion.normalize import CATEGORIES
from app.llm import service, tools
from app.llm.base import LLMError, LLMResponse, Message

log = logging.getLogger(__name__)
MAX_ROUNDS = 4
HISTORY_TURNS = 6
CHUNK = 24  # characters per streamed token event


def _system(ctx: tools.Ctx) -> str:
    last = ctx.cube.latest_month
    first = ctx.cube.df["month"].min() if len(ctx.cube.df) else last
    return service.load_prompt("assistant_system").format(first_month=first, last_month=last, categories=", ".join(CATEGORIES))


def _stream_text(text: str) -> Iterator[dict[str, Any]]:
    for i in range(0, len(text), CHUNK):
        yield {"type": "token", "text": text[i : i + CHUNK]}


def _clean_history(history: list[dict[str, str]]) -> list[Message]:
    out: list[Message] = []
    for m in history[-HISTORY_TURNS * 2 :]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            out.append({"role": m["role"], "content": strip_pii(m["content"])[:1500]})
    return out


def _json_plan(provider: Any, msgs: list[Message], model: str | None, user_id: int | None) -> dict[str, Any] | None:
    names = ", ".join(f"{s['function']['name']}({', '.join(s['function']['parameters']['properties'])})" for s in tools.SPECS)
    plan_msgs = [*msgs, {"role": "user", "content": f'Pick ONE function for the last question. Functions: {names}. '
                         'Reply with JSON only: {"function": "<name>", "args": {...}}'}]
    r = provider.chat(plan_msgs, model=model, json_mode=True, max_tokens=300)
    service.log_call(r, "assistant_plan", cache_hit=False, user_id=user_id)
    try:
        plan = json.loads(r.text)
        return plan if plan.get("function") in tools.REGISTRY else None
    except (json.JSONDecodeError, AttributeError):
        return None


def run(question: str, history: list[dict[str, str]], user: User) -> Iterator[dict[str, Any]]:
    s = get_settings()
    provider = service.get_provider()
    model = service.model_for("main") if provider.name == "groq" else None
    used: list[dict[str, Any]] = []
    last: LLMResponse | None = None
    with AutocommitSession() as db:
        ctx = tools.Ctx(db, user)
        msgs: list[Message] = [{"role": "system", "content": _system(ctx)}, *_clean_history(history),
                               {"role": "user", "content": strip_pii(question)[:1500]}]
        try:
            for _ in range(MAX_ROUNDS):
                try:
                    last = provider.chat_with_tools(msgs, tools.SPECS, model=model, max_tokens=s.llm_max_tokens)
                except LLMError as exc:
                    if "400" not in str(exc):
                        raise
                    log.warning("tool calling rejected (%s); using JSON planner", exc)
                    plan = _json_plan(provider, msgs, model, user.id)
                    if plan is None:
                        raise
                    args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
                    yield {"type": "tool", "name": plan["function"], "args": args}
                    used.append({"name": plan["function"], "args": args})
                    msgs.append({"role": "user", "content": f"Tool result for {plan['function']}: {tools.execute(ctx, plan['function'], args)}\n"
                                 "Now answer the original question using only this data."})
                    last = provider.chat(msgs, model=model, max_tokens=s.llm_max_tokens)
                service.log_call(last, "assistant", cache_hit=False, user_id=user.id)
                if not last.tool_calls:
                    yield from _stream_text(last.text.strip() or "I could not produce an answer from the available data.")
                    break
                msgs.append({"role": "assistant", "content": last.text or None, "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in last.tool_calls]})
                for c in last.tool_calls:
                    yield {"type": "tool", "name": c.name, "args": c.arguments}
                    used.append({"name": c.name, "args": c.arguments})
                    msgs.append({"role": "tool", "tool_call_id": c.id, "content": tools.execute(ctx, c.name, c.arguments)})
            else:  # tool budget used up: force an answer from what we have
                msgs.append({"role": "user", "content": "Answer now using only the data already retrieved."})
                last = provider.chat(msgs, model=model, max_tokens=s.llm_max_tokens)
                service.log_call(last, "assistant", cache_hit=False, user_id=user.id)
                yield from _stream_text(last.text.strip())
        except LLMError as exc:
            log.warning("assistant LLM failure: %s", exc)
            service.log_call(LLMResponse("", model or "n/a", provider.name), "assistant", cache_hit=False, status="error", user_id=user.id)
            yield {"type": "error", "message": "The AI service is unavailable or rate limited right now. Please retry in a minute."}
            return
    label = service.label(last) if last else {"ai_generated": True}
    yield {"type": "done", "tools_used": used, "label": {**label, "sources": sorted({u["name"] for u in used}), "as_of": ctx.cube.asof.date().isoformat()},
           "time": round(time.time(), 0)}
