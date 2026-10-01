"""Phase 7: Groq provider (fake client), LLM service (cache/log/PII), tool registry, assistant loop, RAG and API.

No test calls the real Groq API: a scripted provider or a fake SDK client is used everywhere.
"""

import json
from collections.abc import Callable, Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from groq import APIStatusError, RateLimitError
from sqlalchemy import text

from app.db.models import UserRole
from app.db.session import SessionLocal
from app.llm import assistant, service, tools
from app.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall
from app.llm.groq_provider import GroqProvider
from tests.test_forecasting import _headers, daily  # noqa: F401  (daily is a fixture: one roads complaint a day)

API = "/api/v1"
MODEL = "openai/gpt-oss-120b"


@pytest.fixture(autouse=True)
def _reset_provider() -> Iterator[None]:
    service.set_provider(None)
    yield
    service.set_provider(None)


class Scripted(LLMProvider):
    """Returns queued responses and records every message list it receives."""

    name = "groq"

    def __init__(self, tool_script: list[Any] | None = None, chat_text: str = "final answer") -> None:
        self.tool_script = list(tool_script or [])
        self.chat_text = chat_text
        self.seen: list[list[dict[str, Any]]] = []

    def _r(self, text_: str = "", calls: list[ToolCall] | None = None) -> LLMResponse:
        return LLMResponse(text=text_, model=MODEL, provider="groq", prompt_tokens=10, completion_tokens=5, tool_calls=calls or [])

    def chat(self, messages: Sequence[dict[str, Any]], *, model: str | None = None, json_mode: bool = False, temperature: float | None = None,
             max_tokens: int | None = None) -> LLMResponse:
        self.seen.append(list(messages))
        if json_mode:
            return self._r(json.dumps({"function": "get_kpis", "args": {"months": 1}}))
        return self._r(self.chat_text)

    def chat_with_tools(self, messages: Sequence[dict[str, Any]], tools_: Sequence[dict[str, Any]], *, model: str | None = None,
                        temperature: float | None = None, max_tokens: int | None = None) -> LLMResponse:
        self.seen.append(list(messages))
        step = self.tool_script.pop(0) if self.tool_script else self._r(self.chat_text)
        if isinstance(step, Exception):
            raise step
        return step

    def stream(self, messages: Sequence[dict[str, Any]], **kw: Any) -> Iterator[str]:
        yield self.chat_text


def _fake_client(create: Callable[..., Any]) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _ok_response() -> Any:
    msg = SimpleNamespace(content="hi", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], model=MODEL, usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7))


# ---- provider ---------------------------------------------------------------------------------------------------


def test_groq_provider_retries_rate_limits_and_maps_usage() -> None:
    calls: list[dict[str, Any]] = []
    sleeps: list[float] = []

    def create(**kw: Any) -> Any:
        calls.append(kw)
        if len(calls) == 1:
            raise RateLimitError("slow down", response=httpx.Response(429, headers={"retry-after": "3"}, request=httpx.Request("POST", "http://x")), body=None)
        return _ok_response()

    p = GroqProvider(api_key="x", client=_fake_client(create), sleep=sleeps.append)
    r = p.complete("hello", system="be brief", model=MODEL, json_mode=True)
    assert r.text == "hi" and r.prompt_tokens == 5 and r.completion_tokens == 7 and r.provider == "groq"
    assert sleeps == [3.0] and len(calls) == 2  # honoured Retry-After
    kw = calls[-1]
    assert kw["model"] == MODEL and kw["reasoning_effort"] and kw["response_format"] == {"type": "json_object"}
    assert kw["messages"][0] == {"role": "system", "content": "be brief"} and "max_completion_tokens" in kw


def test_groq_provider_permanent_errors_are_not_retried_and_retries_are_bounded() -> None:
    sleeps: list[float] = []
    n = {"c": 0}

    def bad_request(**kw: Any) -> Any:
        n["c"] += 1
        raise APIStatusError("bad", response=httpx.Response(400, request=httpx.Request("POST", "http://x")), body=None)

    with pytest.raises(LLMError, match="400"):
        GroqProvider(api_key="x", client=_fake_client(bad_request), sleep=sleeps.append).chat([{"role": "user", "content": "x"}], model=MODEL)
    assert n["c"] == 1 and sleeps == []

    def always_429(**kw: Any) -> Any:
        raise RateLimitError("slow", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)

    with pytest.raises(LLMError, match="failed after"):
        GroqProvider(api_key="x", client=_fake_client(always_429), sleep=sleeps.append).chat([{"role": "user", "content": "x"}], model=MODEL)
    assert 0 < len(sleeps) <= 5


def test_groq_provider_parses_tool_calls() -> None:
    fn = SimpleNamespace(name="get_kpis", arguments='{"months": 3}')
    msg = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(id="c1", function=fn)])
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)], model=MODEL, usage=None)
    r = GroqProvider(api_key="x", client=_fake_client(lambda **kw: resp)).chat_with_tools([{"role": "user", "content": "q"}], tools.SPECS, model=MODEL)
    assert r.tool_calls == [ToolCall("c1", "get_kpis", {"months": 3})] and r.prompt_tokens == 0


# ---- service: PII, cache, logging ---------------------------------------------------------------------------------


def test_generate_strips_pii_caches_and_logs(city: None) -> None:
    p = Scripted(chat_text="ok")
    service.set_provider(p)
    first = service.generate("unit", "Call Rajesh on 9876543210 or mail a.b@x.org about the leak", kind="fast")
    again = service.generate("unit", "Call Rajesh on 9876543210 or mail a.b@x.org about the leak", kind="fast")
    sent = p.seen[0][-1]["content"]
    assert "9876543210" not in sent and "a.b@x.org" not in sent
    assert first.cached is False and again.cached is True and len(p.seen) == 1  # second call came from the cache
    with SessionLocal() as db:
        rows = db.execute(text("SELECT purpose, cache_hit, status FROM llm_call_logs ORDER BY id")).all()
    assert [(r[0], r[1], r[2]) for r in rows] == [("unit", False, "ok"), ("unit", True, "ok")]
    assert service.label(first)["ai_generated"] is True and service.label(first)["demo_mode"] is False


# ---- tools ------------------------------------------------------------------------------------------------------


def test_tools_validate_arguments_scope_and_bound_output(client: TestClient, make_user: Callable[..., Any], daily: int) -> None:
    officer = make_user(UserRole.WARD_OFFICER, ward_id=daily)
    with SessionLocal() as db:
        ctx = tools.Ctx(db, officer)
        assert "unknown tool" in json.loads(tools.execute(ctx, "drop_table", {}))["error"]
        assert "metric must be" in json.loads(tools.execute(ctx, "rank_wards", {"metric": "'; DROP TABLE users;--"}))["error"]
        assert "unknown ward" in json.loads(tools.execute(ctx, "get_kpis", {"ward": "Atlantis"}))["error"]
        assert "YYYY-MM" in json.loads(tools.execute(ctx, "rank_wards", {"metric": "complaints", "from_month": "June"}))["error"]
        assert "category must be" in json.loads(tools.execute(ctx, "get_kpis", {"category": "nonsense"}))["error"]
        ok = json.loads(tools.execute(ctx, "get_kpis", {"months": 12, "ward": None, "category": None}))  # nulls are accepted
        assert ok["current"]["complaints"] > 0 and ok["scope"]["ward_id"] == daily
        assert len(tools.execute(ctx, "search_policy_documents", {"query": "potholes"})) <= tools.MAX_RESULT_CHARS
        assert "error" in json.loads(tools.execute(ctx, "search_policy_documents", {"query": "x"}))
    names = {s["function"]["name"] for s in tools.SPECS}
    assert names == set(tools.REGISTRY)
    cat_prop = next(s for s in tools.SPECS if s["function"]["name"] == "get_kpis")["function"]["parameters"]["properties"]["category"]
    assert "null" in cat_prop["type"] and None in cat_prop["enum"]  # optional tool arguments may be null for Groq


# ---- assistant --------------------------------------------------------------------------------------------------


def _drain(question: str, user: Any, history: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    return list(assistant.run(question, history or [], user))


def test_assistant_calls_tool_then_answers_and_strips_pii(make_user: Callable[..., Any], daily: int) -> None:
    call = ToolCall("t1", "rank_wards", {"metric": "complaints", "from_month": "2026-06", "to_month": "2026-06"})
    p = Scripted([LLMResponse("", MODEL, "groq", 10, 5, tool_calls=[call])], chat_text="Ward X is worst.")
    service.set_provider(p)
    events = _drain("Which ward is worst? Call me on 9876543210", make_user(UserRole.COMMISSIONER))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "tool" and events[0]["name"] == "rank_wards" and kinds[-1] == "done" and "token" in kinds
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Ward X is worst."
    done = events[-1]
    assert done["tools_used"][0]["name"] == "rank_wards" and done["label"]["ai_generated"] and done["label"]["sources"] == ["rank_wards"]
    second_round = p.seen[1]
    assert second_round[-1]["role"] == "tool" and "best_5" in second_round[-1]["content"]
    assert "9876543210" not in json.dumps(p.seen[0])  # PII never reaches the provider
    assert p.seen[0][0]["role"] == "system" and "2026-06" in p.seen[0][0]["content"]


def test_assistant_falls_back_to_json_planner_when_tool_calling_is_rejected(make_user: Callable[..., Any], daily: int) -> None:
    p = Scripted([LLMError("Groq HTTP 400: tool_use_failed")], chat_text="From the data: 30 complaints.")
    service.set_provider(p)
    events = _drain("How many complaints?", make_user(UserRole.COMMISSIONER))
    assert [e["type"] for e in events][0] == "tool" and events[0]["name"] == "get_kpis" and events[-1]["type"] == "done"
    assert "30 complaints" in "".join(e.get("text", "") for e in events)


def test_assistant_reports_llm_outage_as_error_event(make_user: Callable[..., Any], daily: int) -> None:
    service.set_provider(Scripted([LLMError("Groq request failed after 5 attempts")]))
    events = _drain("anything", make_user(UserRole.COMMISSIONER))
    assert events[-1]["type"] == "error" and not any(e["type"] == "done" for e in events)


def test_assistant_tool_budget_is_bounded(make_user: Callable[..., Any], daily: int) -> None:
    loop = [LLMResponse("", MODEL, "groq", tool_calls=[ToolCall(f"c{i}", "get_kpis", {})]) for i in range(10)]
    p = Scripted(loop, chat_text="giving up politely")
    service.set_provider(p)
    events = _drain("loop forever", make_user(UserRole.COMMISSIONER))
    assert sum(e["type"] == "tool" for e in events) == assistant.MAX_ROUNDS and events[-1]["type"] == "done"


# ---- API (mock provider) ----------------------------------------------------------------------------------------


def test_chat_stream_endpoint_uses_sse_and_demo_mode(client: TestClient, make_user: Callable[..., Any], daily: int) -> None:
    h = _headers(client, make_user, UserRole.COMMISSIONER)
    r = client.post(f"{API}/assistant/chat", headers=h, json={"message": "show me the kpis please", "history": []})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
    assert r.text.rstrip().endswith("data: [DONE]") and events[0]["type"] == "tool" and events[-1]["type"] == "done"
    assert events[-1]["label"]["demo_mode"] is True and any(e["type"] == "token" for e in events)
    one = client.post(f"{API}/assistant/ask", headers=h, json={"message": "show me the kpis please"}).json()["data"]
    assert one["answer"] and one["tools_used"][0]["name"] == "get_kpis" and one["label"]["demo_mode"] is True
    assert client.post(f"{API}/assistant/chat", json={"message": "hello there"}).status_code == 401
    assert client.post(f"{API}/assistant/chat", headers=h, json={"message": "x"}).status_code == 422
    assert client.post(f"{API}/assistant/chat", headers=h, json={"message": "hi there", "history": [{"role": "system", "content": "x"}]}).status_code == 422


def test_rate_limit_per_user(client: TestClient, make_user: Callable[..., Any], daily: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config, ratelimit

    monkeypatch.setattr(config.get_settings(), "rate_limit_enabled", True)
    monkeypatch.setattr(config.get_settings(), "llm_rate_limit_per_minute", 2)
    h = _headers(client, make_user, UserRole.ANALYST)
    codes = [client.post(f"{API}/assistant/ask", headers=h, json={"message": "show me the kpis"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    ratelimit.reset()


def test_documents_rag_cites_sources_with_keyword_fallback(client: TestClient, make_user: Callable[..., Any], city: None) -> None:
    h = _headers(client, make_user, UserRole.ANALYST)
    empty = client.post(f"{API}/insights/ask-documents", headers=h, json={"question": "What is the pothole repair target?"}).json()["data"]
    assert empty["sources"] == [] and "could not find" in empty["answer"]
    with SessionLocal() as db:
        did = db.execute(text("INSERT INTO documents (title, doc_type, num_chunks) VALUES ('Road SOP', 'policy', 2) RETURNING id")).scalar()
        db.execute(text("INSERT INTO document_chunks (document_id, chunk_index, page, content) VALUES "
                        "(:d, 0, 1, 'Potholes must be repaired within 120 hours of the complaint being registered.'), "
                        "(:d, 1, 2, 'Street lights are inspected every month by the electricity department.')"), {"d": did})
        db.commit()
    r = client.post(f"{API}/insights/ask-documents", headers=h, json={"question": "What is the pothole repair target?"}).json()["data"]
    assert r["sources"][0]["title"] == "Road SOP" and r["sources"][0]["retrieval"] == "keyword" and r["sources"][0]["page"] == 1
    assert "120 hours" in r["sources"][0]["snippet"] and r["label"]["ai_generated"] is True
    assert len([s for s in r["sources"] if "Street lights" in s["snippet"]]) == 0  # irrelevant chunk is not cited


def test_brief_scenario_explanation_and_usage_stats(client: TestClient, make_user: Callable[..., Any], daily: int) -> None:
    head = _headers(client, make_user, UserRole.COMMISSIONER)
    b = client.get(f"{API}/insights/brief", headers=head).json()["data"]
    assert b["month"] == "2026-06" and b["markdown"] and b["label"]["demo_mode"] is True and "city_kpis" in b["facts"]
    assert "data_notes" in b["facts"]
    ex = client.post(f"{API}/scenarios/explain", headers=head, json={"category": "roads", "crew_change_pct": 20}).json()["data"]
    assert ex["explanation"] and ex["result"]["scenario"]["months"] and ex["label"]["ai_generated"]
    officer = _headers(client, make_user, UserRole.WARD_OFFICER, email="wo@test.org")
    assert client.get(f"{API}/insights/brief", headers=officer).status_code == 403
    analyst = _headers(client, make_user, UserRole.ANALYST, email="an@test.org")
    u = client.get(f"{API}/llm/usage", headers=analyst).json()["data"]
    assert u["calls"] >= 2 and {x["purpose"] for x in u["by_purpose"]} >= {"policy_brief", "what_if_explain"} and 0 <= u["cache_hit_rate_pct"] <= 100
    assert client.get(f"{API}/llm/usage", headers=officer).status_code == 403
    st = client.get(f"{API}/llm/status", headers=officer).json()["data"]
    assert st["demo_mode"] is True and st["provider"] == "mock"
