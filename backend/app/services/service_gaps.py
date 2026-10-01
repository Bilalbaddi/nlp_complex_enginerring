"""Service gap analysis: SLA breaches, resolution-time distribution, backlog aging, repeat hotspots, Service Gap Index.

Service Gap Index (SGI), per ward over a window (default 12 months), 0 (best) to 100 (worst):

    SGI = 100 * ( 0.35 * breach_rate  + 0.20 * delay_ratio + 0.20 * backlog_share
                + 0.15 * repeat_rate  + 0.10 * dissatisfaction )

Each component is min-max scaled ACROSS WARDS to 0..1 (so the index ranks wards relative to each other):
    breach_rate     breached / received            (late or still-open-past-SLA)
    delay_ratio     mean resolution hours / mean SLA hours of resolved complaints
    backlog_share   open complaints / received
    repeat_rate     near-duplicate complaints / received
    dissatisfaction (5 - mean citizen rating) / 4
Weights are in WEIGHTS below and documented in docs/SERVICE_GAP_INDEX.md.
"""

from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import cache
from app.services.cube import Cube, month_add

WEIGHTS = {"breach_rate": 0.35, "delay_ratio": 0.20, "backlog_share": 0.20, "repeat_rate": 0.15, "dissatisfaction": 0.10}


def _window(cube: Cube, months: int) -> tuple[str, str]:
    end = cube.latest_month
    return month_add(end, -(months - 1)), end


def sla_breakdown(cube: Cube, *, by: str, months: int, ward_id: int | None, category: str | None) -> list[dict[str, Any]]:
    a, b = _window(cube, months)
    df = cube.filt(ward_id=ward_id, category=category, date_from=a, date_to=b)
    key = {"ward": "ward_id", "department": "category", "category": "category", "month": "month"}[by]
    out = []
    for k, g in df.groupby(key):
        n, br = float(g["n"].sum()), float(g["breached"].sum())
        res = float(g["resolved"].sum())
        item: dict[str, Any] = {"key": k, "complaints": int(n), "breached": int(br), "breach_rate_pct": round(100 * br / n, 2),
                                "avg_resolution_hours": round(float(g["res_hours"].sum()) / res, 2) if res else None,
                                "open": int(g["open"].sum())}
        if by == "ward":
            w = cube.ward_by_id.get(int(k))
            item.update(code=w["code"] if w else None, name=w["name"] if w else None)
        elif by in ("department", "category"):
            d = cube.dept_by_cat.get(str(k))
            item.update(name=d["name"] if d else str(k), code=d["code"] if d else None, category=str(k))
        out.append(item)
    return sorted(out, key=lambda i: -i["breach_rate_pct"]) if by != "month" else sorted(out, key=lambda i: i["key"])


_EDGES = [0, 6, 12, 24, 48, 72, 120, 168, 336, 720]
_DIST_SQL = text(
    "SELECT width_bucket(resolution_hours, CAST(:edges AS float8[])) AS b, count(*) FROM complaints "
    "WHERE resolved_at IS NOT NULL AND (CAST(:c AS text) IS NULL OR category = :c) AND (CAST(:w AS int) IS NULL OR ward_id = :w) "
    "AND submitted_at >= CAST(:a AS date) GROUP BY 1 ORDER BY 1"
)
_PCT_SQL = text(
    "SELECT percentile_cont(ARRAY[0.5,0.75,0.9,0.95]) WITHIN GROUP (ORDER BY resolution_hours) FROM complaints "
    "WHERE resolved_at IS NOT NULL AND (CAST(:c AS text) IS NULL OR category = :c) AND (CAST(:w AS int) IS NULL OR ward_id = :w) "
    "AND submitted_at >= CAST(:a AS date)"
)


def distribution(db: Session, cube: Cube, *, months: int, ward_id: int | None, category: str | None) -> dict[str, Any]:
    a, _ = _window(cube, months)
    p = {"c": category, "w": ward_id, "a": a + "-01"}

    def run() -> dict[str, Any]:
        counts = {int(b): int(n) for b, n in db.execute(_DIST_SQL, {**p, "edges": [float(e) for e in _EDGES]}).all()}
        pct = db.scalar(_PCT_SQL, p) or [None] * 4
        labels = [f"{lo}-{hi}h" for lo, hi in zip(_EDGES, _EDGES[1:])] + [f"{_EDGES[-1]}h+"]
        return {
            "buckets": [{"label": labels[i], "count": counts.get(i + 1, 0)} for i in range(len(labels))],
            "percentiles_hours": {k: (round(float(v), 1) if v is not None else None) for k, v in zip(["p50", "p75", "p90", "p95"], pct)},
        }

    val, _ = cache.get_or_set(cache.make_key("analytics:dist", p), 3600, run)
    return {"window_from": a, **val}


_BACKLOG_SQL = text(
    """
    WITH asof AS (SELECT max(submitted_at) AS t FROM complaints)
    SELECT category, ward_id,
      CASE WHEN age_h < 24 THEN '<1d' WHEN age_h < 72 THEN '1-3d' WHEN age_h < 168 THEN '3-7d'
           WHEN age_h < 720 THEN '7-30d' ELSE '30d+' END AS bucket,
      count(*) AS n, count(*) FILTER (WHERE age_h > sla_hours) AS overdue
    FROM (SELECT c.category, c.ward_id, c.sla_hours, extract(epoch FROM (a.t - c.submitted_at)) / 3600 AS age_h
          FROM complaints c, asof a WHERE c.resolved_at IS NULL AND c.status NOT IN ('rejected')) x
    GROUP BY 1, 2, 3
    """
)
BACKLOG_ORDER = ["<1d", "1-3d", "3-7d", "7-30d", "30d+"]


def backlog(db: Session, cube: Cube, *, ward_id: int | None, category: str | None) -> dict[str, Any]:
    rows, _ = cache.get_or_set("analytics:backlog:v1", 1800, lambda: [list(r) for r in db.execute(_BACKLOG_SQL).all()])
    df = pd.DataFrame(rows, columns=["category", "ward_id", "bucket", "n", "overdue"])
    if ward_id is not None:
        df = df[df["ward_id"] == ward_id]
    if category:
        df = df[df["category"] == category]
    by_age = df.groupby("bucket")[["n", "overdue"]].sum()
    by_dept = df.groupby("category")[["n", "overdue"]].sum().sort_values("n", ascending=False)
    by_ward = df.groupby("ward_id")[["n", "overdue"]].sum().sort_values("n", ascending=False)
    return {
        "total_open": int(df["n"].sum()), "total_overdue": int(df["overdue"].sum()),
        "by_age": [{"bucket": b, "count": int(by_age.loc[b, "n"]) if b in by_age.index else 0,
                    "overdue": int(by_age.loc[b, "overdue"]) if b in by_age.index else 0} for b in BACKLOG_ORDER],
        "by_department": [{"category": c, "name": (cube.dept_by_cat.get(c) or {}).get("name", c), "open": int(r.n), "overdue": int(r.overdue)}
                          for c, r in by_dept.iterrows()],
        "by_ward": [{"ward_id": int(w), "name": (cube.ward_by_id.get(int(w)) or {}).get("name"), "open": int(r.n), "overdue": int(r.overdue)}
                    for w, r in by_ward.iterrows()],
    }


_REPEAT_SQL = text(
    """
    WITH asof AS (SELECT max(submitted_at) AS t FROM complaints)
    SELECT c.category, c.ward_id, ST_Y(g) AS lat, ST_X(g) AS lon, n, last_at FROM (
      SELECT category, ward_id, ST_SnapToGrid(location, 0.0025) AS g, count(*) AS n, max(submitted_at) AS last_at
      FROM complaints, asof
      WHERE location IS NOT NULL AND submitted_at >= asof.t - make_interval(days => :days)
        AND (CAST(:w AS int) IS NULL OR ward_id = :w) AND (CAST(:c AS text) IS NULL OR category = :c)
      GROUP BY 1, 2, 3 HAVING count(*) >= :min_n) c
    ORDER BY n DESC LIMIT :lim
    """
)


def repeat_hotspots(db: Session, cube: Cube, *, days: int, min_n: int, ward_id: int | None, category: str | None, limit: int = 30) -> list[dict[str, Any]]:
    p = {"days": days, "min_n": min_n, "w": ward_id, "c": category, "lim": limit}
    rows, _ = cache.get_or_set(cache.make_key("analytics:repeat", p), 1800, lambda: [
        [r[0], r[1], r[2], r[3], int(r[4]), r[5].isoformat()] for r in db.execute(_REPEAT_SQL, p).all()])
    return [{"category": r[0], "ward_id": r[1], "ward": (cube.ward_by_id.get(r[1]) or {}).get("name"), "lat": r[2], "lon": r[3],
             "complaints": r[4], "last_at": r[5], "cell_size_m": 275} for r in rows]


def _scale(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    return (s - lo) / (hi - lo) if hi > lo else s * 0


def gap_index(cube: Cube, *, months: int = 12, category: str | None = None) -> dict[str, Any]:
    a, b = _window(cube, months)
    df = cube.filt(category=category, date_from=a, date_to=b)
    fb = cube.filt(category=category, date_from=a, date_to=b, fb=True)
    rows = []
    for w in cube.wards:
        g, f = df[df["ward_id"] == w["id"]], fb[fb["ward_id"] == w["id"]]
        n = float(g["n"].sum())
        if n == 0:
            continue
        res = float(g["resolved"].sum())
        sat = float(f["rating_sum"].sum()) / float(f["n"].sum()) if len(f) and f["n"].sum() else 3.0
        rows.append({
            "ward_id": w["id"], "code": w["code"], "name": w["name"], "complaints": int(n),
            "breach_rate": float(g["breached"].sum()) / n,
            "delay_ratio": float(g["res_hours"].sum()) / float(g["sla_hours_resolved"].sum()) if g["sla_hours_resolved"].sum() else 0.0,
            "backlog_share": float(g["open"].sum()) / n, "repeat_rate": float(g["repeats"].sum()) / n,
            "dissatisfaction": (5 - sat) / 4, "satisfaction": round(sat, 2),
        })
    d = pd.DataFrame(rows)
    sgi = sum(WEIGHTS[k] * _scale(d[k]) for k in WEIGHTS) * 100
    d["sgi"] = sgi.round(1)
    d["rank"] = d["sgi"].rank(ascending=False, method="min").astype(int)
    d = d.sort_values("sgi", ascending=False)
    items = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in d.to_dict("records")]
    return {"period": {"from": a, "to": b}, "weights": WEIGHTS, "category": category, "items": items}
