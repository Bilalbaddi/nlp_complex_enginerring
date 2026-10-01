"""Safe, predefined analytics functions the assistant may call (Groq tool calling). The LLM never writes SQL.

Every tool: validates its arguments, applies the caller's role scoping (ward officer / department head), and returns a
COMPACT dict (token budget). Unknown tools and bad arguments produce an {"error": ...} result, never an exception.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import User
from app.ingestion.normalize import CATEGORIES
from app.llm import rag
from app.services import cube as cubes
from app.services import emerging, forecasting, kpis, service_gaps
from app.services.cube import Cube

MAX_RESULT_CHARS = 3500
RANK_METRICS = [*kpis.METRICS, "gap_index"]


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Optional parameters are declared nullable: models often send null for arguments they do not need, and Groq
    rejects the whole call otherwise (HTTP 400 tool_use_failed)."""
    props: dict[str, Any] = {}
    for k, v in properties.items():
        if k in (required or []):
            props[k] = v
        else:
            props[k] = {**v, "type": [v["type"], "null"], **({"enum": [*v["enum"], None]} if "enum" in v else {})}
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": props, "required": required or []}}}


_CAT = {"type": "string", "enum": list(CATEGORIES), "description": "Complaint category / department"}
_MONTH = {"type": "string", "description": "Month as YYYY-MM"}
_WARD = {"type": "string", "description": "Ward name or code, e.g. 'Old City' or 'W08'"}
_METRIC = {"type": "string", "enum": RANK_METRICS}

SPECS: list[dict[str, Any]] = [
    _fn("get_kpis", "City, ward or category KPIs (resolution rate, SLA compliance, avg/median resolution hours, satisfaction, "
                    "complaints per 10k, repeat rate, efficiency, backlog) for a period, with change vs previous period and last year.",
        {"ward": _WARD, "category": _CAT, "months": {"type": "integer", "description": "period length in months (default 1)"}, "month": {**_MONTH, "description": "last month of the period (default newest)"}}),
    _fn("rank_wards", "Rank wards by a metric over a period; returns best 5 and worst 5. Use from_month/to_month for named periods "
                      "(e.g. the monsoon = June to September). metric 'gap_index' is the composite Service Gap Index (higher = worse).",
        {"metric": _METRIC, "from_month": _MONTH, "to_month": _MONTH, "category": _CAT}, ["metric"]),
    _fn("rank_departments", "Rank departments by a metric over a period (best 5 and worst 5).",
        {"metric": _METRIC, "from_month": _MONTH, "to_month": _MONTH}, ["metric"]),
    _fn("sla_breaches", "SLA breach counts and rates grouped by department, ward, category or month.",
        {"by": {"type": "string", "enum": ["department", "ward", "month"]}, "months": {"type": "integer"}, "category": _CAT, "ward": _WARD}, ["by"]),
    _fn("service_gap_index", "Service Gap Index per ward (0 best, 100 worst) with its components; returns the worst 5 wards.",
        {"months": {"type": "integer"}, "category": _CAT}),
    _fn("backlog", "Open complaint backlog and aging (<1d, 1-3d, 3-7d, 7-30d, 30d+), optionally by category or ward.",
        {"category": _CAT, "ward": _WARD}),
    _fn("emerging_issues", "Currently emerging civic issues (spikes vs normal): category, wards, trend, severity, summary.",
        {"category": _CAT}),
    _fn("forecast_volume", "Forecast complaint volume for the next months (with 95% interval) for the city, a category or a ward.",
        {"category": _CAT, "ward": _WARD, "horizon": {"type": "integer", "description": "1-6 months"}}),
    _fn("resource_forecast", "Forecast crew-days needed per department for the next months vs current capacity (shortfalls).",
        {"horizon": {"type": "integer"}}),
    _fn("ward_risk", "Infrastructure risk score per ward from forecasts, growth, service gaps and infrastructure stress; top 5.", {}),
    _fn("search_policy_documents", "Search the uploaded policy documents and reports; returns the most relevant excerpts with titles and pages.",
        {"query": {"type": "string"}}, ["query"]),
]


class Ctx:
    def __init__(self, db: Session, user: User) -> None:
        self.db, self.user = db, user
        self.cube: Cube = cubes.get_cube(db)

    def ward(self, v: Any) -> int | None:
        if not v:
            return None
        s = str(v).strip().lower()
        for w in self.cube.wards:
            if s in (w["code"].lower(), w["name"].lower()):
                return int(w["id"])
        for w in self.cube.wards:
            if s in w["name"].lower():
                return int(w["id"])
        raise ValueError(f"unknown ward {v!r}; valid: {', '.join(w['name'] for w in self.cube.wards)}")

    def scoped(self, args: dict[str, Any]) -> tuple[int | None, str | None]:
        return cubes.scope_filters(self.user, self.ward(args.get("ward")), _cat(args), self.cube)


def _cat(a: dict[str, Any]) -> str | None:
    c = a.get("category")
    if c is None or c == "":
        return None
    if c not in CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
    return str(c)


def _int(a: dict[str, Any], k: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(a.get(k, default))))
    except (TypeError, ValueError):
        return default


def _month(a: dict[str, Any], k: str) -> str | None:
    v = a.get(k)
    if v is None or v == "":
        return None
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(v)):
        raise ValueError(f"{k} must be YYYY-MM")
    return str(v)


def _span(c: Ctx, a: dict[str, Any]) -> tuple[int, str]:
    end = _month(a, "to_month") or c.cube.latest_month
    start = _month(a, "from_month") or cubes.month_add(end, -11)
    months = (int(end[:4]) - int(start[:4])) * 12 + int(end[5:7]) - int(start[5:7]) + 1
    if months < 1:
        raise ValueError("from_month must not be after to_month")
    return min(months, 36), end


def _r(x: Any, n: int = 1) -> Any:
    return round(x, n) if isinstance(x, float) else x


def _kpi_short(d: dict[str, Any]) -> dict[str, Any]:
    keys = ["complaints", "resolution_rate", "sla_compliance_pct", "avg_resolution_hours", "median_resolution_hours", "satisfaction",
            "complaints_per_10k", "repeat_rate_pct", "efficiency_score", "open_backlog"]
    return {k: _r(d.get(k)) for k in keys if k in d}


def t_get_kpis(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    ward, cat = c.scoped(a)
    s = kpis.summary(c.db, c.cube, ward_id=ward, category=cat, months=_int(a, "months", 1, 1, 36), month=_month(a, "month"))
    return {"as_of": s["as_of"], "period": s["period"], "scope": {"ward_id": ward, "category": cat}, "current": _kpi_short(s["current"]),
            "change_vs_previous_period_pct": {k: v for k, v in s["change_vs_previous_pct"].items() if v is not None},
            "change_vs_last_year_pct": {k: v for k, v in s["change_vs_last_year_pct"].items() if v is not None}}


def _rank(items: list[dict[str, Any]], metric: str, keep: tuple[str, ...]) -> dict[str, Any]:
    slim = [{**{k: i.get(k) for k in keep}, metric: _r(i.get(metric)), "rank": i["rank"]} for i in items]
    return {"best_5": slim[:5], "worst_5": slim[-5:][::-1], "n": len(slim)}


def t_rank_wards(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    metric = str(a.get("metric", ""))
    if metric not in RANK_METRICS:
        raise ValueError(f"metric must be one of {', '.join(RANK_METRICS)}")
    months, end = _span(c, a)
    cat = _cat(a)
    _, cat = cubes.scope_filters(c.user, None, cat, c.cube)
    if metric == "gap_index":
        g = service_gaps.gap_index(c.cube, months=months, category=cat)
        items = [{"name": i["name"], "code": i["code"], "gap_index": i["sgi"], "rank": i["rank"]} for i in g["items"]]
        return {"period": g["period"], "higher_is_worse": True, "worst_5": items[:5], "best_5": items[-5:][::-1]}
    r = kpis.rankings(c.cube, by="ward", metric=metric, months=months, month=end, category=cat)
    return {"period": r["period"], "higher_is_better": r["higher_is_better"], **_rank(r["items"], metric, ("name", "code"))}


def t_rank_departments(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    metric = str(a.get("metric", ""))
    if metric not in kpis.METRICS:
        raise ValueError(f"metric must be one of {', '.join(kpis.METRICS)}")
    months, end = _span(c, a)
    r = kpis.rankings(c.cube, by="department", metric=metric, months=months, month=end)
    return {"period": r["period"], "higher_is_better": r["higher_is_better"], **_rank(r["items"], metric, ("name", "category"))}


def t_sla_breaches(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    ward, cat = c.scoped(a)
    by = str(a.get("by", "department"))
    if by not in ("department", "ward", "month"):
        raise ValueError("by must be department, ward or month")
    rows = service_gaps.sla_breakdown(c.cube, by=by, months=_int(a, "months", 12, 1, 36), ward_id=ward, category=cat)
    return {"by": by, "rows": [{"name": r.get("name") or r["key"], "complaints": r["complaints"], "breached": r["breached"],
                                "breach_rate_pct": r["breach_rate_pct"]} for r in rows[:12]]}


def t_service_gap_index(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    _, cat = cubes.scope_filters(c.user, None, _cat(a), c.cube)
    g = service_gaps.gap_index(c.cube, months=_int(a, "months", 12, 1, 36), category=cat)
    return {"period": g["period"], "weights": g["weights"], "worst_5": [
        {"name": i["name"], "sgi": i["sgi"], "breach_rate": _r(i["breach_rate"] * 100), "backlog_share_pct": _r(i["backlog_share"] * 100),
         "satisfaction": i["satisfaction"]} for i in g["items"][:5]]}


def t_backlog(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    ward, cat = c.scoped(a)
    b = service_gaps.backlog(c.db, c.cube, ward_id=ward, category=cat)
    return {"total_open": b["total_open"], "total_overdue": b["total_overdue"], "by_age": b["by_age"],
            "top_departments": b["by_department"][:5], "top_wards": b["by_ward"][:5]}


def t_emerging_issues(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    ward, cat = c.scoped({"category": a.get("category")})
    f = emerging.feed(c.db, c.cube, category=cat, ward_id=ward)
    return {"as_of": f["as_of"], "issues": [{"category": i["category"], "trend": i["trend"], "severity": i["severity"],
                                              "recent_14d": i["recent_complaints"], "wards": [w["name"] for w in i["wards"][:4]],
                                              "summary": i["summary"].get("text", "")[:300]} for i in f["issues"][:5]]}


def t_forecast_volume(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    ward, cat = c.scoped(a)
    v = forecasting.volume(c.cube, category=cat, ward_id=ward, horizon=_int(a, "horizon", 3, 1, 6))
    return {"scope": {"category": cat, "ward_id": ward}, "model": v["model"], "backtest_mape_pct": (v["backtest"] or {}).get("mape_pct"),
            "history_last_6": v["history"][-6:], "forecast": v["forecast"]}


def t_resource_forecast(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    r = forecasting.resources(c.db, c.cube, horizon=_int(a, "horizon", 3, 1, 6), overrides={})
    return {"months": r["months"], "total_crew_days": r["total_crew_days"], "departments": [
        {"category": x["category"], "crew_days_needed": x["crew_days_needed"], "capacity_per_month": x["capacity_crew_days_per_month"],
         "max_shortfall": max(x["shortfall"])} for x in r["categories"][:8]]}


def t_ward_risk(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    r = forecasting.risk(c.cube)
    return {"top_5": [{"name": i["name"], "risk_score": i["risk_score"], "level": i["level"], "forecast_next_3_months": i["forecast_next_3_months"],
                       "gap_index": _r(i["gap_index"])} for i in r["items"][:5]]}


def t_search_policy_documents(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
    q = str(a.get("query", "")).strip()
    if len(q) < 3:
        raise ValueError("query is too short")
    return {"excerpts": [{"title": x["title"], "page": x["page"], "text": x["content"][:700]} for x in rag.retrieve(q, 3)]}


REGISTRY: dict[str, Callable[[Ctx, dict[str, Any]], dict[str, Any]]] = {
    "get_kpis": t_get_kpis, "rank_wards": t_rank_wards, "rank_departments": t_rank_departments, "sla_breaches": t_sla_breaches,
    "service_gap_index": t_service_gap_index, "backlog": t_backlog, "emerging_issues": t_emerging_issues,
    "forecast_volume": t_forecast_volume, "resource_forecast": t_resource_forecast, "ward_risk": t_ward_risk,
    "search_policy_documents": t_search_policy_documents,
}


def execute(ctx: Ctx, name: str, args: dict[str, Any]) -> str:
    """Run a tool by name; always returns a JSON string no longer than MAX_RESULT_CHARS."""
    fn = REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name!r}", "available": sorted(REGISTRY)})
    try:
        out = json.dumps(fn(ctx, args if isinstance(args, dict) else {}), default=str, ensure_ascii=False)
    except (ValueError, KeyError, TypeError) as exc:
        out = json.dumps({"error": str(exc)})
    except Exception as exc:  # a tool must never take the chat down
        out = json.dumps({"error": f"{type(exc).__name__}: tool failed"})
    return out if len(out) <= MAX_RESULT_CHARS else out[: MAX_RESULT_CHARS - 40] + '... [truncated]"}'
