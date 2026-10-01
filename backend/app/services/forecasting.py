"""Forecast service: volume forecasts, resource needs, infrastructure risk and the what-if scenario model.

Resource needs:   crew_days = forecast_complaints x CREW_DAYS_PER_COMPLAINT[category]   (ratios are configurable per request)
Capacity:         staff_count (latest city-wide service_performance row) x WORKING_DAYS x FIELD_SHARE
Infrastructure risk (per ward, 0-100; each component min-max scaled across wards):
    0.30 * forecast_load   forecast complaints per 10k residents over the next 3 months
  + 0.25 * growth          mean monthly complaints, last 6 months vs the 6 before
  + 0.25 * gap_index       Service Gap Index (see service_gaps.py)
  + 0.20 * infra_stress    roads + drainage + water complaints per road-km, last 12 months

What-if (pure Python; the LLM only explains the result):
    capacity_now   = mean complaints resolved per month over the last 6 months (category, city-wide)
    capacity'      = capacity_now * (1 + crew_change) ** ELASTICITY          (diminishing returns, ELASTICITY = 0.8)
    backlog_(t+1)  = max(0, backlog_t + demand_t * (1 + demand_change) - capacity')
    utilisation    = demand / capacity;   sla' = clip(sla_now + SLA_SENSITIVITY * 100 * (util_now - util'), 0, 100)
    backlog_0 is today's open complaints for the category; demand_t is the forecast volume.
"""

import json
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ml import forecast as fc
from app.services import cache, service_gaps
from app.services.cube import Cube, month_add

CREW_DAYS_PER_COMPLAINT = {  # assumptions (crew-days of field work per complaint); override per request
    "water_supply": 2.0, "drainage": 3.0, "roads": 3.5, "garbage": 0.8, "streetlights": 1.0, "encroachment": 1.5,
    "public_health": 1.8, "electricity": 1.5, "parks": 1.2, "noise": 0.5,
}
WORKING_DAYS = 22
FIELD_SHARE = 0.6
ELASTICITY = 0.8
SLA_SENSITIVITY = 0.5
TTL = 6 * 3600


def monthly_series(cube: Cube, *, category: str | None = None, ward_id: int | None = None) -> pd.Series:
    """Complete months only: a partial first/last month would look like a collapse in demand."""
    df = cube.filt(ward_id=ward_id, category=category)
    s = df.groupby("month")["n"].sum().astype(float)
    if s.empty:
        return s
    idx = []
    m = s.index.min()
    while m <= s.index.max():
        idx.append(m)
        m = month_add(m, 1)
    s = s.reindex(idx, fill_value=0.0)
    if len(s) > 3 and s.iloc[0] < 0.4 * s.iloc[1:4].median():
        s = s.iloc[1:]
    if len(s) > 3 and s.iloc[-1] < 0.4 * s.iloc[-4:-1].median():
        s = s.iloc[:-1]
    return s


def _volume_key(cube: Cube, category: str | None, ward_id: int | None, horizon: int) -> str:
    return cache.make_key("forecast:vol", cube.latest_month, category, ward_id, horizon)


def volume(cube: Cube, *, category: str | None, ward_id: int | None, horizon: int) -> dict[str, Any]:
    key = _volume_key(cube, category, ward_id, horizon)

    def run() -> dict[str, Any]:
        s = monthly_series(cube, category=category, ward_id=ward_id)
        if len(s) < 14:
            return {"history": [], "forecast": [], "model": None, "backtest": None}
        f = fc.forecast(s.to_numpy(), horizon)
        last = s.index[-1]
        months = [month_add(last, i + 1) for i in range(horizon)]
        return {
            "history": [{"month": m, "complaints": int(v)} for m, v in s.iloc[-24:].items()],
            "forecast": [{"month": m, "mean": a, "lo": lo, "hi": hi} for m, a, lo, hi in zip(months, f.mean, f.lo, f.hi)],
            "model": f.model, "backtest": f.backtest,
            "method": "rolling-origin backtest picks the best of naive / seasonal naive / damped ETS (+season)",
        }

    val, _ = cache.get_or_set(key, TTL, run)
    return {"category": category, "ward_id": ward_id, "horizon": horizon, **val}


def backtest_all(cube: Cube) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        rows = []
        for label, cat in [("All categories", None), *[(d["name"], d["category"]) for d in cube.depts if d["category"]]]:
            s = monthly_series(cube, category=cat)
            sc = fc.backtest(s.to_numpy()) if len(s) >= 14 else {}
            best = min(sc, key=lambda m: sc[m]["mae"]) if sc else None
            rows.append({"series": label, "category": cat, "mean_monthly": round(float(s.tail(12).mean()), 1) if len(s) else 0,
                         "models": sc, "best": best, "best_mae": sc[best]["mae"] if best else None,
                         "best_mape_pct": sc[best]["mape_pct"] if best else None})
        mapes = [r["best_mape_pct"] for r in rows if r["best_mape_pct"] is not None]
        return {"origins_months_back": list(fc.ORIGINS), "horizon_months": fc.BACKTEST_H, "series": rows,
                "avg_best_mape_pct": round(float(np.mean(mapes)), 1) if mapes else None}

    val, _ = cache.get_or_set(cache.make_key("forecast:bt", cube.latest_month), TTL, run)
    return val


def _staff(db: Session) -> dict[str, int]:
    rows = db.execute(text(
        "SELECT d.category, sp.staff_count FROM service_performance sp JOIN departments d ON d.id = sp.department_id "
        "WHERE sp.ward_id IS NULL AND sp.period = (SELECT max(period) FROM service_performance WHERE ward_id IS NULL) "
        "AND d.category IS NOT NULL")).all()
    return {r[0]: int(r[1] or 0) for r in rows}


def parse_overrides(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {k: float(v) for k, v in d.items() if k in CREW_DAYS_PER_COMPLAINT and float(v) > 0}
    except (ValueError, TypeError, AttributeError):
        return {}


def resources(db: Session, cube: Cube, *, horizon: int, overrides: dict[str, float]) -> dict[str, Any]:
    ratios = {**CREW_DAYS_PER_COMPLAINT, **overrides}
    cache.prefetch([_volume_key(cube, d["category"], None, horizon) for d in cube.depts if d["category"]] + ["forecast:staff:v1"])
    staff = cache.get_or_set("forecast:staff:v1", TTL, lambda: _staff(db))[0]
    cats = []
    totals = np.zeros(horizon)
    months: list[str] = []
    for d in cube.depts:
        cat = d["category"]
        if not cat:
            continue
        v = volume(cube, category=cat, ward_id=None, horizon=horizon)
        if not v["forecast"]:
            continue
        months = [f["month"] for f in v["forecast"]]
        need = [round(f["mean"] * ratios[cat], 1) for f in v["forecast"]]
        need_hi = [round(f["hi"] * ratios[cat], 1) for f in v["forecast"]]
        cap = staff.get(cat, 0) * WORKING_DAYS * FIELD_SHARE
        totals += np.array(need)
        cats.append({"category": cat, "department": d["name"], "crew_days_per_complaint": ratios[cat], "forecast_complaints": [f["mean"] for f in v["forecast"]],
                     "crew_days_needed": need, "crew_days_needed_high": need_hi, "capacity_crew_days_per_month": round(cap, 1),
                     "staff": staff.get(cat), "shortfall": [round(max(0.0, n - cap), 1) for n in need],
                     "utilisation_pct": [round(100 * n / cap, 1) if cap else None for n in need]})
    cats.sort(key=lambda c: -max(c["crew_days_needed"]))
    return {"months": months, "categories": cats, "total_crew_days": [round(float(t), 1) for t in totals],
            "assumptions": {"working_days_per_month": WORKING_DAYS, "field_share_of_staff": FIELD_SHARE, "default_ratios": CREW_DAYS_PER_COMPLAINT,
                            "overrides_applied": overrides}}


def _scale(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    return (s - lo) / (hi - lo) if hi > lo else s * 0


def risk(cube: Cube) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        rows = []
        gi = {i["ward_id"]: i["sgi"] for i in service_gaps.gap_index(cube, months=12)["items"]}
        df12 = cube.filt(date_from=month_add(cube.latest_month, -11))
        for w in cube.wards:
            s = monthly_series(cube, ward_id=w["id"])
            if len(s) < 14:
                continue
            f = fc.forecast(s.to_numpy(), 3)
            load = sum(f.mean) / max(w["population"], 1) * 10000
            growth = float(s.tail(6).mean() / max(s.iloc[-12:-6].mean(), 1.0))
            infra = df12[(df12["ward_id"] == w["id"]) & df12["category"].isin(["roads", "drainage", "water_supply"])]["n"].sum() / max(w["road_km"], 1.0)
            rows.append({"ward_id": w["id"], "code": w["code"], "name": w["name"], "forecast_next_3_months": round(sum(f.mean), 1),
                         "forecast_load_per_10k": load, "growth": growth, "gap_index": gi.get(w["id"], 0.0), "infra_stress": float(infra)})
        d = pd.DataFrame(rows)
        d["risk_score"] = (100 * (0.30 * _scale(d["forecast_load_per_10k"]) + 0.25 * _scale(d["growth"]) + 0.25 * _scale(d["gap_index"])
                                  + 0.20 * _scale(d["infra_stress"]))).round(1)
        d["level"] = pd.cut(d["risk_score"], [-1, 33, 66, 101], labels=["low", "medium", "high"]).astype(str)
        d = d.sort_values("risk_score", ascending=False)
        items = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in d.to_dict("records")]
        return {"weights": {"forecast_load": 0.30, "growth": 0.25, "gap_index": 0.25, "infra_stress": 0.20}, "items": items}

    val, _ = cache.get_or_set(cache.make_key("forecast:risk", cube.latest_month), TTL, run)
    return val


def what_if(db: Session, cube: Cube, *, category: str, crew_change_pct: float, demand_change_pct: float, horizon: int) -> dict[str, Any]:
    v = volume(cube, category=category, ward_id=None, horizon=horizon)
    if not v["forecast"]:
        return {"error": "not enough history to forecast this category"}
    cur = cube.filt(category=category, date_from=month_add(cube.latest_month, -5))
    resolved_pm = float(cur["resolved"].sum()) / max(cur["month"].nunique(), 1)
    n_cur = float(cur["n"].sum())
    sla_now = 100 * (1 - float(cur["breached"].sum()) / n_cur) if n_cur else 0.0
    demand_now = n_cur / max(cur["month"].nunique(), 1)
    capacity = max(resolved_pm, 1.0)
    backlog0 = float(cube.df[cube.df["category"] == category]["open"].sum())
    util_now = demand_now / capacity

    def simulate(crew: float, dem: float) -> dict[str, Any]:
        cap = capacity * (1 + crew / 100) ** ELASTICITY
        b, series = backlog0, []
        for f in v["forecast"]:
            d = f["mean"] * (1 + dem / 100)
            b = max(0.0, b + d - cap)
            util = d / cap
            sla = float(np.clip(sla_now + SLA_SENSITIVITY * 100 * (util_now - util), 0, 100))
            series.append({"month": f["month"], "demand": round(d, 1), "capacity": round(cap, 1), "backlog": round(b), "utilisation_pct": round(100 * util, 1),
                           "sla_compliance_pct": round(sla, 1)})
        return {"capacity_per_month": round(cap, 1), "months": series, "final_backlog": series[-1]["backlog"],
                "final_sla_compliance_pct": series[-1]["sla_compliance_pct"]}

    base, scen = simulate(0, 0), simulate(crew_change_pct, demand_change_pct)
    return {
        "category": category, "horizon_months": horizon, "inputs": {"crew_change_pct": crew_change_pct, "demand_change_pct": demand_change_pct},
        "current": {"open_backlog": round(backlog0), "capacity_per_month": round(capacity, 1), "demand_per_month": round(demand_now, 1),
                    "sla_compliance_pct": round(sla_now, 1)},
        "baseline": base, "scenario": scen,
        "delta": {"final_backlog": scen["final_backlog"] - base["final_backlog"],
                  "final_sla_compliance_pct": round(scen["final_sla_compliance_pct"] - base["final_sla_compliance_pct"], 1)},
        "assumptions": {"elasticity": ELASTICITY, "sla_sensitivity": SLA_SENSITIVITY,
                        "model": "capacity = recent monthly resolutions x (1+crew change)^0.8; backlog accumulates demand minus capacity"},
    }
