"""KPI engine. Everything is derived from the cached analytics cube (see services/cube.py) except the median.

Definitions (period = a run of whole months):
  resolution_rate        resolved / received
  sla_compliance_pct     100 * (1 - breached / received); breached = resolved late or still open past its SLA
  avg_resolution_hours   mean resolution time of resolved complaints
  median_resolution_hours  SQL percentile_cont(0.5) over resolved complaints (cached)
  satisfaction           mean citizen rating (1-5) of feedback submitted in the period
  complaints_per_10k     received / population * 10,000 (selected ward, or the whole city)
  repeat_rate_pct        100 * complaints flagged as near-duplicates / received
  efficiency_score       0-100: 40% SLA compliance + 30% resolution rate + 20% speed + 10% satisfaction,
                         where speed = min(1, mean SLA hours / mean resolution hours) over resolved complaints
"""

from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import cache
from app.services.cube import Cube, month_add

METRICS = ["complaints", "resolution_rate", "sla_compliance_pct", "avg_resolution_hours", "satisfaction",
           "complaints_per_10k", "repeat_rate_pct", "efficiency_score", "open_backlog"]
HIGHER_IS_BETTER = {"resolution_rate": True, "sla_compliance_pct": True, "satisfaction": True, "efficiency_score": True,
                    "avg_resolution_hours": False, "complaints_per_10k": False, "repeat_rate_pct": False,
                    "complaints": False, "open_backlog": False}


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x), n)


def aggregate(df: pd.DataFrame, fb: pd.DataFrame, population: float) -> dict[str, Any]:
    n = float(df["n"].sum())
    if n == 0:
        return {m: None for m in METRICS} | {"complaints": 0}
    resolved = float(df["resolved"].sum())
    breached = float(df["breached"].sum())
    res_rate = resolved / n
    sla = 100 * (1 - breached / n)
    avg_h = float(df["res_hours"].sum()) / resolved if resolved else None
    sat = float(fb["rating_sum"].sum()) / float(fb["n"].sum()) if len(fb) and fb["n"].sum() else None
    speed = min(1.0, float(df["sla_hours_resolved"].sum()) / float(df["res_hours"].sum())) if resolved and df["res_hours"].sum() else 0.0
    eff = 0.4 * sla + 30 * res_rate + 20 * speed + (10 * sat / 5 if sat is not None else 10 * 0.6)
    return {
        "complaints": int(n), "resolution_rate": _r(100 * res_rate), "sla_compliance_pct": _r(sla),
        "avg_resolution_hours": _r(avg_h), "satisfaction": _r(sat), "complaints_per_10k": _r(n / population * 10000),
        "repeat_rate_pct": _r(100 * float(df["repeats"].sum()) / n), "efficiency_score": _r(eff),
        "open_backlog": int(df["open"].sum()),
    }


def _population(cube: Cube, ward_id: int | None) -> float:
    return float(cube.ward_by_id[ward_id]["population"]) if ward_id in cube.ward_by_id else cube.population


def _median(db: Session, ward_id: int | None, category: str | None, d_from: str, d_to: str) -> float | None:
    key = cache.make_key("analytics:median", ward_id, category, d_from, d_to)

    def run() -> float | None:
        v = db.scalar(
            text(
                "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours) FROM complaints "
                "WHERE resolved_at IS NOT NULL AND submitted_at >= CAST(:a AS date) "
                "AND submitted_at < (CAST(:b AS date) + interval '1 month') "
                "AND (CAST(:w AS int) IS NULL OR ward_id = :w) AND (CAST(:c AS text) IS NULL OR category = :c)"
            ),
            {"a": d_from + "-01", "b": d_to + "-01", "w": ward_id, "c": category},
        )
        return None if v is None else round(float(v), 2)

    val, _ = cache.get_or_set(key, 3600, run)
    return val


def period_kpis(cube: Cube, ward_id: int | None, category: str | None, d_from: str, d_to: str) -> dict[str, Any]:
    df = cube.filt(ward_id=ward_id, category=category, date_from=d_from, date_to=d_to)
    fb = cube.filt(ward_id=ward_id, category=category, date_from=d_from, date_to=d_to, fb=True)
    return aggregate(df, fb, _population(cube, ward_id))


def _delta(cur: dict[str, Any], prev: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for m in METRICS:
        a, b = cur.get(m), prev.get(m)
        out[m] = None if a is None or b in (None, 0) else _r((a - b) / abs(b) * 100)
    return out


def summary(db: Session, cube: Cube, *, ward_id: int | None, category: str | None, months: int, month: str | None) -> dict[str, Any]:
    end = month or cube.latest_month
    start = month_add(end, -(months - 1))
    cur = period_kpis(cube, ward_id, category, start, end)
    cur["median_resolution_hours"] = _median(db, ward_id, category, start, end)
    prev_s, prev_e = month_add(start, -months), month_add(end, -months)
    yoy_s, yoy_e = month_add(start, -12), month_add(end, -12)
    prev, yoy = period_kpis(cube, ward_id, category, prev_s, prev_e), period_kpis(cube, ward_id, category, yoy_s, yoy_e)
    return {
        "as_of": cube.asof.date().isoformat(), "period": {"from": start, "to": end, "months": months},
        "current": cur, "previous_period": prev, "same_period_last_year": yoy,
        "change_vs_previous_pct": _delta(cur, prev), "change_vs_last_year_pct": _delta(cur, yoy),
        "higher_is_better": HIGHER_IS_BETTER,
    }


def trend(cube: Cube, *, ward_id: int | None, category: str | None, date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    end = (date_to or cube.latest_month)[:7]
    start = (date_from or month_add(end, -23))[:7]
    df = cube.filt(ward_id=ward_id, category=category, date_from=start, date_to=end)
    fb = cube.filt(ward_id=ward_id, category=category, date_from=start, date_to=end, fb=True)
    pop = _population(cube, ward_id)
    out = []
    m = start
    while m <= end:
        row = aggregate(df[df["month"] == m], fb[fb["month"] == m], pop)
        out.append({"month": m, **row})
        m = month_add(m, 1)
    return out


def rankings(cube: Cube, *, by: str, metric: str, months: int, month: str | None, category: str | None = None,
             ward_id: int | None = None) -> dict[str, Any]:
    end = month or cube.latest_month
    start = month_add(end, -(months - 1))
    df = cube.filt(ward_id=ward_id, category=category, date_from=start, date_to=end)
    fb = cube.filt(ward_id=ward_id, category=category, date_from=start, date_to=end, fb=True)
    items = []
    if by == "ward":
        for w in cube.wards:
            row = aggregate(df[df["ward_id"] == w["id"]], fb[fb["ward_id"] == w["id"]], float(w["population"]) or 1.0)
            items.append({"id": w["id"], "code": w["code"], "name": w["name"], **row})
    else:
        for d in cube.depts:
            if not d["category"]:
                continue
            row = aggregate(df[df["category"] == d["category"]], fb[fb["category"] == d["category"]], cube.population)
            items.append({"id": d["id"], "code": d["code"], "name": d["name"], "category": d["category"], **row})
    best_high = HIGHER_IS_BETTER.get(metric, True)
    items = [i for i in items if i.get(metric) is not None]
    items.sort(key=lambda i: i[metric], reverse=best_high)
    for k, i in enumerate(items, 1):
        i["rank"] = k
    return {"by": by, "metric": metric, "higher_is_better": best_high, "period": {"from": start, "to": end}, "items": items}
