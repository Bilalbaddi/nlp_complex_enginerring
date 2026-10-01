"""Automated policy brief and what-if explanation. All numbers come from the analytics engine; Groq only writes the prose.

The facts sent to the model are a compact table (about 1.5k tokens). Results are cached per (as-of month, facts, model).
"""

import json
from typing import Any

from sqlalchemy.orm import Session

from app.llm import rag, service
from app.services import cube as cubes
from app.services import emerging, forecasting, kpis, service_gaps
from app.services.cube import Cube


def gather_facts(db: Session, cube: Cube) -> dict[str, Any]:
    s = kpis.summary(db, cube, ward_id=None, category=None, months=1, month=None)
    cur = s["current"]
    gi = service_gaps.gap_index(cube, months=12)["items"]
    dep = kpis.rankings(cube, by="department", metric="efficiency_score", months=3, month=None)["items"]
    em = emerging.feed(db, cube)["issues"][:4]
    res = forecasting.resources(db, cube, horizon=3, overrides={})
    back = service_gaps.backlog(db, cube, ward_id=None, category=None)
    fc_total = [forecasting.volume(cube, category=None, ward_id=None, horizon=3)]
    facts: dict[str, Any] = {
        "as_of": s["as_of"], "month": s["period"]["to"],
        "city_kpis": {k: cur[k] for k in ("complaints", "resolution_rate", "sla_compliance_pct", "avg_resolution_hours",
                                          "median_resolution_hours", "satisfaction", "open_backlog", "repeat_rate_pct")},
        "change_vs_previous_month_pct": {k: v for k, v in s["change_vs_previous_pct"].items() if k in ("complaints", "sla_compliance_pct", "resolution_rate", "satisfaction")},
        "change_vs_last_year_pct": {k: v for k, v in s["change_vs_last_year_pct"].items() if k in ("complaints", "sla_compliance_pct", "resolution_rate", "satisfaction")},
        "data_notes": [
            "change_* values are RELATIVE percentage changes (e.g. -26 means 26% lower), not percentage-point changes",
            "resolution_rate for the newest month looks low because recent complaints have not had time to be resolved yet (right-censoring); say so instead of calling it a collapse",
            "open_backlog is the count of unresolved complaints in the month; backlog.* is city-wide, all months",
        ],
        "weakest_wards_service_gap_index": [{"ward": i["name"], "sgi": i["sgi"]} for i in gi[:4]],
        "strongest_wards": [{"ward": i["name"], "sgi": i["sgi"]} for i in gi[-2:]],
        "department_efficiency_lowest": [{"dept": d["name"], "score": d["efficiency_score"], "sla_pct": d["sla_compliance_pct"]} for d in dep[-3:]],
        "department_efficiency_highest": [{"dept": d["name"], "score": d["efficiency_score"]} for d in dep[:2]],
        "emerging_issues": [{"category": i["category"], "wards": [w["name"] for w in i["wards"][:3]], "trend": i["trend"], "last_14_days": i["recent_complaints"]} for i in em],
        "backlog": {"open": back["total_open"], "overdue": back["total_overdue"], "30d_plus": back["by_age"][-1]["count"]},
        "forecast_next_3_months_city": [{"month": f["month"], "complaints": f["mean"]} for f in fc_total[0]["forecast"]],
        "crew_capacity_shortfalls": [{"dept": x["category"], "max_shortfall_crew_days": max(x["shortfall"]), "peak_utilisation_pct": max(u or 0 for u in x["utilisation_pct"])}
                                     for x in res["categories"] if max(x["shortfall"]) > 0][:4],
    }
    try:
        ex = rag.retrieve("service level agreement resolution time standards grievance redressal", 2)
        facts["policy_excerpts"] = [{"doc": e["title"], "page": e["page"], "text": e["content"][:300]} for e in ex]
    except Exception:  # policy context is optional
        facts["policy_excerpts"] = []
    return facts


def policy_brief(db: Session, cube: Cube, *, refresh: bool = False, user_id: int | None = None) -> dict[str, Any]:
    facts = gather_facts(db, cube)
    prompt = service.load_prompt("policy_brief").format(facts=json.dumps(facts, ensure_ascii=False, default=str))
    r = service.generate("policy_brief", prompt, max_tokens=1600, cache_ttl=0 if refresh else 24 * 3600, user_id=user_id)
    return {"month": facts["month"], "as_of": facts["as_of"], "markdown": r.text.strip(), "facts": facts, "label": service.label(r)}


def explain_what_if(result: dict[str, Any], *, user_id: int | None = None) -> dict[str, Any]:
    slim = {k: result[k] for k in ("category", "horizon_months", "inputs", "current", "delta", "assumptions")}
    slim["baseline_final"] = {k: result["baseline"][k] for k in ("final_backlog", "final_sla_compliance_pct", "capacity_per_month")}
    slim["scenario_final"] = {k: result["scenario"][k] for k in ("final_backlog", "final_sla_compliance_pct", "capacity_per_month")}
    prompt = service.load_prompt("what_if_explain").format(result=json.dumps(slim, ensure_ascii=False))
    r = service.generate("what_if_explain", prompt, kind="fast", max_tokens=500, user_id=user_id)
    return {"explanation": r.text.strip(), "label": service.label(r)}


def prewarm_cube(db: Session) -> Cube:
    return cubes.get_cube(db)
