"""Analytics cube: one aggregate query (ward x category x month) cached in Postgres, reused by KPIs, service gaps,
choropleths and forecasts. With a remote database this replaces dozens of round trips with a single cached JSON blob.

"As of" date: analytics are relative to the newest complaint in the data (not the wall clock), so the demo dataset
always has a meaningful "current month". A complaint is breached when it was resolved later than its SLA, or is still
unresolved and older than its SLA at the as-of date.
"""

import time
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import User, UserRole
from app.services import cache

CUBE_TTL = 3600
CUBE_KEY = "analytics:cube:v1"

_CUBE_SQL = text(
    """
    WITH asof AS (SELECT max(submitted_at) AS t FROM complaints)
    SELECT c.ward_id, c.category, to_char(date_trunc('month', c.submitted_at), 'YYYY-MM') AS month,
           count(*) AS n,
           count(*) FILTER (WHERE c.resolved_at IS NOT NULL) AS resolved,
           count(*) FILTER (WHERE c.resolved_at IS NULL) AS open,
           count(*) FILTER (WHERE (c.resolved_at IS NOT NULL AND c.resolution_hours > c.sla_hours)
                              OR (c.resolved_at IS NULL AND extract(epoch FROM (a.t - c.submitted_at)) / 3600 > c.sla_hours)) AS breached,
           COALESCE(sum(c.resolution_hours) FILTER (WHERE c.resolved_at IS NOT NULL), 0) AS res_hours,
           COALESCE(sum(c.sla_hours) FILTER (WHERE c.resolved_at IS NOT NULL), 0) AS sla_hours_resolved,
           count(*) FILTER (WHERE c.duplicate_of_id IS NOT NULL) AS repeats,
           count(*) FILTER (WHERE c.needs_review) AS needs_review,
           COALESCE(avg(c.urgency_score), 0) AS urgency
    FROM complaints c, asof a
    WHERE c.ward_id IS NOT NULL AND c.category IS NOT NULL
    GROUP BY 1, 2, 3
    """
)
_FB_SQL = text(
    "SELECT f.ward_id, d.category, to_char(date_trunc('month', f.submitted_at), 'YYYY-MM') AS month, "
    "count(*) AS n, sum(f.rating) AS rating_sum FROM feedback f LEFT JOIN departments d ON d.id = f.department_id "
    "WHERE f.ward_id IS NOT NULL GROUP BY 1, 2, 3"
)


def _build(db: Session) -> dict[str, Any]:
    cols = ["ward_id", "category", "month", "n", "resolved", "open", "breached", "res_hours", "sla_hours_resolved",
            "repeats", "needs_review", "urgency"]
    rows = [[(float(v) if hasattr(v, "as_tuple") else v) for v in r] for r in db.execute(_CUBE_SQL).all()]
    fb = [[(float(v) if hasattr(v, "as_tuple") else v) for v in r] for r in db.execute(_FB_SQL).all()]
    asof = db.scalar(text("SELECT max(submitted_at) FROM complaints"))
    wards = [dict(id=r[0], code=r[1], name=r[2], population=r[3], area=r[4], road_km=r[5], water=r[6])
             for r in db.execute(text("SELECT id, code, name, population, area_sq_km, road_km, water_connections FROM wards ORDER BY code"))]
    depts = [dict(id=r[0], code=r[1], name=r[2], category=r[3], sla_hours=r[4])
             for r in db.execute(text("SELECT id, code, name, category, sla_hours FROM departments ORDER BY id"))]
    return {"cols": cols, "rows": rows, "fb": fb, "asof": asof.isoformat() if asof else None, "wards": wards, "depts": depts}


class Cube:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.df = pd.DataFrame(raw["rows"], columns=raw["cols"])
        self.fb = pd.DataFrame(raw["fb"], columns=["ward_id", "category", "month", "n", "rating_sum"])
        self.asof = datetime.fromisoformat(raw["asof"]) if raw["asof"] else datetime.now()
        self.wards: list[dict[str, Any]] = raw["wards"]
        self.depts: list[dict[str, Any]] = raw["depts"]
        self.ward_by_id = {w["id"]: w for w in self.wards}
        self.dept_by_cat = {d["category"]: d for d in self.depts if d["category"]}
        self.population = float(sum(w["population"] for w in self.wards)) or 1.0

    @property
    def latest_month(self) -> str:
        return self.asof.strftime("%Y-%m")

    def filt(self, *, ward_id: int | None = None, category: str | None = None, date_from: str | None = None,
             date_to: str | None = None, fb: bool = False) -> pd.DataFrame:
        d = self.fb if fb else self.df
        m = pd.Series(True, index=d.index)
        if ward_id is not None:
            m &= d["ward_id"] == ward_id
        if category:
            m &= d["category"] == category
        if date_from:
            m &= d["month"] >= date_from[:7]
        if date_to:
            m &= d["month"] <= date_to[:7]
        return d[m]


_memo: tuple[float, Cube] | None = None
MEMO_SECONDS = 120


def get_cube(db: Session) -> Cube:
    """The cube is ~1 MB of JSON in the cache table; fetching it from the remote DB on every request cost seconds, so the
    parsed object is also memoised in this process for MEMO_SECONDS (disabled in tests, which reset data between tests)."""
    global _memo
    use_memo = get_settings().env != "test"
    if use_memo and _memo and time.monotonic() - _memo[0] < MEMO_SECONDS:
        return _memo[1]
    raw, _ = cache.get_or_set(CUBE_KEY, CUBE_TTL, lambda: _build(db))
    cube = Cube(raw)
    if use_memo:
        _memo = (time.monotonic(), cube)
    return cube


def invalidate() -> None:
    global _memo
    _memo = None
    cache.delete_prefix("analytics:")


def scope_filters(user: User, ward_id: int | None, category: str | None, cube: Cube) -> tuple[int | None, str | None]:
    """Ward officers see their ward; department heads their department's category."""
    if user.role == UserRole.WARD_OFFICER and user.ward_id:
        ward_id = user.ward_id
    if user.role == UserRole.DEPARTMENT_HEAD and user.department_id:
        dept = next((d for d in cube.depts if d["id"] == user.department_id), None)
        if dept and dept["category"]:
            category = dept["category"]
    return ward_id, category


def month_add(month: str, k: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"
