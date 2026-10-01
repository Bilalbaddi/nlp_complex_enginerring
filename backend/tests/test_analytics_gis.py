"""Phase 4 (KPIs, service gaps, emerging issues) and Phase 5 (GIS) against tiny deterministic data in the test schema.

Layout (as-of = 2026-06-30 12:00, the newest complaint):
  ward A / roads (SLA 72h): May = 5 on-time; June = 6 on-time (24h), 2 late (100h), 2 still open since 15 June.
  ward B / garbage: 1 complaint per week for weeks 2-9 before as-of, then 12 in the last 10 days (a spike).
  ward B / water_supply: steady 1 per week (must NOT be flagged).
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.models import UserRole
from app.db.session import SessionLocal
from tests.conftest import PASSWORD

API = "/api/v1"
ASOF = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
INSERT = text(
    "INSERT INTO complaints (description, category, status, ward_id, department_id, location, submitted_at, resolved_at, "
    "resolution_hours, sla_hours, needs_review) VALUES (:d, CAST(:c AS varchar), CAST(:s AS varchar), :w, (SELECT id FROM departments WHERE category = CAST(:c AS varchar)), "
    "ST_Centroid((SELECT geom FROM wards WHERE id = :w)), :t, :r, :h, 72, false)"
)


def _row(w: int, cat: str, t: datetime, res_hours: float | None) -> dict[str, Any]:
    return {"d": f"{cat} problem near market", "c": cat, "s": "closed" if res_hours is not None else "open", "w": w, "t": t,
            "r": t + timedelta(hours=res_hours) if res_hours is not None else None, "h": res_hours}


@pytest.fixture
def data(city: None) -> tuple[int, int]:
    with SessionLocal() as db:
        wa, wb = [r[0] for r in db.execute(text("SELECT id FROM wards ORDER BY code LIMIT 2"))]
        rows = [_row(wa, "roads", datetime(2026, 5, 10 + i, 9, tzinfo=timezone.utc), 24) for i in range(5)]
        rows += [_row(wa, "roads", datetime(2026, 6, 20 + i, 9, tzinfo=timezone.utc), 24) for i in range(6)]
        rows += [_row(wa, "roads", datetime(2026, 6, 25 + i, 9, tzinfo=timezone.utc), 100) for i in range(2)]
        rows += [_row(wa, "roads", datetime(2026, 6, 15, 9, tzinfo=timezone.utc), None)]  # open and past its SLA
        rows += [_row(wa, "roads", ASOF, None)]  # the newest complaint defines "as of"; open but not yet late
        rows += [_row(wb, "garbage", ASOF - timedelta(days=7 * k + 3), 20) for k in range(2, 10)]
        rows += [_row(wb, "garbage", ASOF - timedelta(days=d, hours=1), 20) for d in range(0, 10) for _ in range(1 + (d < 2))]
        rows += [_row(wb, "water_supply", ASOF - timedelta(days=7 * k + 2), 20) for k in range(0, 10)]
        db.execute(INSERT, rows)
        db.commit()
    return wa, wb


def _login(client: TestClient, make_user: Callable[..., Any], role: UserRole, **kw: Any) -> dict[str, str]:
    u = make_user(role, **kw)
    r = client.post(f"{API}/auth/login", json={"email": u.email, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture
def h(client: TestClient, make_user: Callable[..., Any], data: tuple[int, int]) -> dict[str, str]:
    return _login(client, make_user, UserRole.COMMISSIONER)


def get(client: TestClient, h: dict[str, str], path: str, **params: Any) -> Any:
    r = client.get(f"{API}{path}", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---- Phase 4 ----------------------------------------------------------------------------------------------------


def test_kpi_summary_values_and_comparisons(client: TestClient, h: dict[str, str], data: tuple[int, int]) -> None:
    wa, _ = data
    d = get(client, h, "/kpis/summary", ward_id=wa, category="roads", months=1)
    cur = d["current"]
    assert d["period"]["to"] == "2026-06" and d["as_of"].startswith("2026-06-30")
    assert cur["complaints"] == 10 and cur["open_backlog"] == 2
    assert cur["resolution_rate"] == pytest.approx(80, abs=0.01)
    assert cur["sla_compliance_pct"] == pytest.approx(70, abs=0.01)  # 2 late + 1 old open breached
    assert cur["median_resolution_hours"] == 24
    assert d["previous_period"]["complaints"] == 5 and d["change_vs_previous_pct"]["complaints"] == pytest.approx(100.0)
    assert d["same_period_last_year"]["complaints"] == 0 and d["change_vs_last_year_pct"]["complaints"] is None


def test_kpi_trend_and_rankings(client: TestClient, h: dict[str, str], data: tuple[int, int]) -> None:
    wa, wb = data
    t = get(client, h, "/kpis/trend", ward_id=wa, category="roads", date_from="2026-04", date_to="2026-06")
    assert [x["month"] for x in t] == ["2026-04", "2026-05", "2026-06"] and [x["complaints"] for x in t] == [0, 5, 10]
    r = get(client, h, "/kpis/rankings", by="ward", metric="sla_compliance_pct", months=3)
    ids = [i["id"] for i in r["items"]]
    assert r["higher_is_better"] and ids.index(wb) < ids.index(wa) and r["items"][0]["rank"] == 1
    dep = get(client, h, "/kpis/rankings", by="department", metric="complaints", months=3)
    assert {i["category"] for i in dep["items"]} >= {"roads", "garbage"} and not dep["higher_is_better"]


def test_kpi_validation_and_auth(client: TestClient, h: dict[str, str]) -> None:
    assert client.get(f"{API}/kpis/summary").status_code == 401
    assert client.get(f"{API}/kpis/summary", headers=h, params={"category": "bogus"}).status_code == 422
    assert client.get(f"{API}/kpis/summary", headers=h, params={"months": 0}).status_code == 422
    assert client.get(f"{API}/kpis/rankings", headers=h, params={"metric": "nope"}).status_code == 422


def test_role_scoping_forces_ward_and_department(client: TestClient, make_user: Callable[..., Any], data: tuple[int, int]) -> None:
    wa, wb = data
    officer = _login(client, make_user, UserRole.WARD_OFFICER, ward_id=wb)
    d = get(client, officer, "/kpis/summary", ward_id=wa, months=12)  # asks for ward A, is scoped to ward B
    assert d["current"]["complaints"] == 8 + 12 + 10  # ward B only (garbage + water_supply)
    with SessionLocal() as db:
        dept = db.execute(text("SELECT id FROM departments WHERE category = 'roads'")).scalar()
    head = _login(client, make_user, UserRole.DEPARTMENT_HEAD, email="head@test.org", department_id=dept)
    assert get(client, head, "/kpis/summary", months=12, category="garbage")["current"]["complaints"] == 15  # forced to roads


def test_service_gaps(client: TestClient, h: dict[str, str], data: tuple[int, int]) -> None:
    wa, wb = data
    sla = {i["key"]: i for i in get(client, h, "/service-gaps/sla", by="department", months=12)}
    assert sla["roads"]["complaints"] == 15 and sla["roads"]["breached"] == 3 and sla["roads"]["breach_rate_pct"] == pytest.approx(20.0)
    b = get(client, h, "/service-gaps/backlog")
    assert b["total_open"] == 2 and b["total_overdue"] == 1
    assert {x["bucket"]: x["count"] for x in b["by_age"]}["7-30d"] == 1 and b["by_ward"][0]["ward_id"] == wa
    dist = get(client, h, "/service-gaps/distribution", months=12)
    assert sum(x["count"] for x in dist["buckets"]) == 13 + 20 + 10  # every resolved complaint lands in a bucket
    assert dist["percentiles_hours"]["p50"] is not None
    idx = get(client, h, "/service-gaps/index", months=12)
    assert idx["items"][0]["ward_id"] == wa and idx["items"][0]["rank"] == 1
    assert all(0 <= i["sgi"] <= 100 for i in idx["items"]) and sum(idx["weights"].values()) == pytest.approx(1.0)
    rep = get(client, h, "/service-gaps/repeat-hotspots", days=365, min_complaints=5)
    assert rep and rep[0]["complaints"] >= 5


def test_emerging_issue_detected_and_steady_series_ignored(client: TestClient, h: dict[str, str], data: tuple[int, int]) -> None:
    _, wb = data
    d = get(client, h, "/emerging-issues")
    cats = [i["category"] for i in d["issues"]]
    assert "garbage" in cats and "water_supply" not in cats
    issue = next(i for i in d["issues"] if i["category"] == "garbage")
    assert issue["wards"][0]["ward_id"] == wb and issue["wards"][0]["z"] >= 3 and issue["recent_complaints"] == 12
    assert issue["trend"] in ("rising", "steady", "falling") and issue["samples"]
    assert issue["summary"]["text"]  # mock/LLM or template summary
    assert get(client, h, "/emerging-issues", category="water_supply")["issues"] == []


# ---- Phase 5 ----------------------------------------------------------------------------------------------------


def test_heatmap_and_time_filter(client: TestClient, h: dict[str, str], data: tuple[int, int]) -> None:
    all_pts = get(client, h, "/gis/heatmap")
    assert sum(p[2] for p in all_pts["points"]) == 15 + 8 + 12 + 10 and all_pts["max"] > 0
    may = get(client, h, "/gis/heatmap", date_from="2026-05", date_to="2026-05", category="roads")
    assert sum(p[2] for p in may["points"]) == 5
    assert client.get(f"{API}/gis/heatmap", headers=h, params={"date_from": "May"}).status_code == 422


def test_hotspots_dbscan(client: TestClient, h: dict[str, str], data: tuple[int, int]) -> None:
    d = get(client, h, "/gis/hotspots", min_samples=5, eps_m=100)
    assert d["clusters"], d  # identical locations per ward form dense clusters
    top = d["clusters"][0]
    assert top["complaints"] >= 5 and top["dominant_category"] and len(top["center"]) == 2
    assert d["params"]["algorithm"].startswith("DBSCAN")
    assert get(client, h, "/gis/hotspots", min_samples=500)["clusters"] == []


def test_choropleth_metrics(client: TestClient, h: dict[str, str], data: tuple[int, int]) -> None:
    wa, _ = data
    for metric in ("complaints_per_10k", "sla_compliance_pct", "gap_index"):
        c = get(client, h, "/gis/choropleth", metric=metric, date_from="2026-04", date_to="2026-06")
        assert c["type"] == "FeatureCollection" and len(c["features"]) == 20 and c["metric"] == metric
        feat = next(f for f in c["features"] if f["properties"]["ward_id"] == wa)
        assert feat["properties"]["value"] is not None and feat["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert client.get(f"{API}/gis/choropleth", headers=h, params={"metric": "x"}).status_code == 422


def test_within_radius_and_ward_summary(client: TestClient, h: dict[str, str], data: tuple[int, int]) -> None:
    wa, _ = data
    with SessionLocal() as db:
        lat, lon = db.execute(text("SELECT ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom)) FROM wards WHERE id = :w"), {"w": wa}).one()
    near = get(client, h, "/gis/within", lat=lat, lon=lon, radius_m=200)
    assert near["total"] == 15 and near["by_category"][0]["category"] == "roads" and near["nearest"][0]["distance_m"] < 5
    assert "citizen_name" not in near["nearest"][0]
    far = get(client, h, "/gis/within", lat=lat + 1, lon=lon, radius_m=500)
    assert far["total"] == 0
    s = get(client, h, f"/gis/wards/{wa}/summary")
    assert s["kpis"]["complaints"] > 0 and s["top_categories"][0]["category"] == "roads" and s["gap_index"]["rank"] == 1
    assert client.get(f"{API}/gis/wards/999999/summary", headers=h).status_code == 404
    assert client.get(f"{API}/gis/within", headers=h, params={"lat": 95, "lon": 0}).status_code == 422
