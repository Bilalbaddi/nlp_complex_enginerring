"""Phase 6: forecasting models (pure numpy), then the API on a clean constant-demand series in the test schema.

API data: one 'roads' complaint per day in ward A from 2023-10-01 to 2026-06-30 (about 30 a month, all resolved in 24h).
"""

from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.models import UserRole
from app.db.session import SessionLocal
from app.ml import forecast as fc
from tests.conftest import PASSWORD

API = "/api/v1"


def test_seasonal_series_is_forecast_accurately_with_ordered_intervals() -> None:
    t = np.arange(42)
    y = 100 + 40 * np.sin(2 * np.pi * t / 12) + np.random.default_rng(1).normal(0, 3, 42)
    f = fc.forecast(y[:36], 6)
    actual = y[36:]
    assert f.model in ("seasonal_naive", "ets_seasonal")
    assert np.mean(np.abs(actual - f.mean) / actual) < 0.12
    assert all(lo <= m <= hi for lo, m, hi in zip(f.lo, f.mean, f.hi)) and min(f.lo) >= 0
    assert f.hi[-1] - f.lo[-1] > f.hi[0] - f.lo[0]  # uncertainty grows with the horizon
    assert set(f.backtest["models"]) >= {"naive", "ets_flat"} and f.backtest["mae"] >= 0 and f.backtest["mape_pct"] >= 0


def test_short_or_flat_series_fall_back_safely() -> None:
    f = fc.forecast(np.array([5.0] * 14), 3)
    assert f.model in fc.CANDIDATES and f.mean[0] == pytest.approx(5.0, abs=0.5)
    short = fc.forecast(np.array([3.0, 4.0, 5.0, 6.0, 5.0, 4.0, 5.0, 6.0, 4.0, 5.0, 6.0, 5.0]), 2)
    assert short.model == "naive" and len(short.mean) == 2  # too short for any backtest origin
    assert fc.forecast(np.zeros(20), 2).mean == [0.0, 0.0]


@pytest.fixture
def daily(city: None) -> int:
    with SessionLocal() as db:
        wa = db.execute(text("SELECT id FROM wards ORDER BY code LIMIT 1")).scalar()
        db.execute(text(
            "INSERT INTO complaints (description, category, status, ward_id, department_id, location, submitted_at, resolved_at, "
            "resolution_hours, sla_hours, needs_review) SELECT 'road broken', 'roads', 'closed', :w, "
            "(SELECT id FROM departments WHERE category = 'roads'), ST_Centroid((SELECT geom FROM wards WHERE id = :w)), d, "
            "d + interval '24 hours', 24, 72, false FROM generate_series(timestamptz '2023-10-01 09:00+00', "
            "timestamptz '2026-06-30 09:00+00', interval '1 day') AS d"), {"w": wa})
        db.commit()
    return int(wa)


def _headers(client: TestClient, make_user: Callable[..., Any], role: UserRole, **kw: Any) -> dict[str, str]:
    u = make_user(role, **kw)
    r = client.post(f"{API}/auth/login", json={"email": u.email, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def test_volume_forecast_endpoint(client: TestClient, make_user: Callable[..., Any], daily: int) -> None:
    h = _headers(client, make_user, UserRole.COMMISSIONER)
    d = client.get(f"{API}/forecasts/volume", headers=h, params={"category": "roads", "horizon": 6}).json()["data"]
    assert len(d["forecast"]) == 6 and d["forecast"][0]["month"] == "2026-07" and len(d["history"]) == 24
    assert all(22 <= f["mean"] <= 38 for f in d["forecast"]) and d["model"] and d["backtest"]["mae"] is not None
    assert client.get(f"{API}/forecasts/volume", headers=h, params={"horizon": 7}).status_code == 422
    assert client.get(f"{API}/forecasts/volume", headers=h, params={"ward_id": 999999}).status_code == 404
    assert client.get(f"{API}/forecasts/volume").status_code == 401
    empty = client.get(f"{API}/forecasts/volume", headers=h, params={"category": "parks"}).json()["data"]
    assert empty["forecast"] == [] and empty["model"] is None  # no history: empty, not an error


def test_backtest_and_risk_endpoints(client: TestClient, make_user: Callable[..., Any], daily: int) -> None:
    h = _headers(client, make_user, UserRole.ANALYST)
    b = client.get(f"{API}/forecasts/backtest", headers=h).json()["data"]
    roads = next(s for s in b["series"] if s["category"] == "roads")
    assert roads["best"] and roads["best_mape_pct"] < 15 and b["horizon_months"] == 3
    r = client.get(f"{API}/forecasts/risk", headers=h).json()["data"]
    assert r["items"] and r["items"][0]["ward_id"] == daily and 0 <= r["items"][0]["risk_score"] <= 100
    ward_officer = _headers(client, make_user, UserRole.WARD_OFFICER, ward_id=daily)
    assert len(client.get(f"{API}/forecasts/risk", headers=ward_officer).json()["data"]["items"]) == 1
    assert client.get(f"{API}/forecasts/backtest", headers=ward_officer).status_code == 403


def test_resources_use_configurable_ratios(client: TestClient, make_user: Callable[..., Any], daily: int) -> None:
    h = _headers(client, make_user, UserRole.DEPARTMENT_HEAD)
    base = client.get(f"{API}/forecasts/resources", headers=h, params={"horizon": 3}).json()["data"]
    roads = next(c for c in base["categories"] if c["category"] == "roads")
    assert roads["crew_days_needed"][0] == pytest.approx(roads["forecast_complaints"][0] * 3.5, abs=0.1)
    doubled = client.get(f"{API}/forecasts/resources", headers=h, params={"horizon": 3, "ratios": '{"roads": 7}'}).json()["data"]
    r2 = next(c for c in doubled["categories"] if c["category"] == "roads")
    assert r2["crew_days_needed"][0] == pytest.approx(2 * roads["crew_days_needed"][0], abs=0.2)
    assert doubled["assumptions"]["overrides_applied"] == {"roads": 7.0} and len(base["total_crew_days"]) == 3
    bad = client.get(f"{API}/forecasts/resources", headers=h, params={"ratios": "not json"}).json()["data"]
    assert bad["assumptions"]["overrides_applied"] == {}  # garbage overrides are ignored, not an error
    officer = _headers(client, make_user, UserRole.WARD_OFFICER, email="w@test.org")
    assert client.get(f"{API}/forecasts/resources", headers=officer).status_code == 403


def test_what_if_scenarios_move_in_the_right_direction(client: TestClient, make_user: Callable[..., Any], daily: int) -> None:
    h = _headers(client, make_user, UserRole.COMMISSIONER)

    def run(**body: Any) -> dict[str, Any]:
        r = client.post(f"{API}/scenarios/what-if", headers=h, json={"category": "roads", **body})
        assert r.status_code == 200, r.text
        return r.json()["data"]

    same = run()
    assert same["scenario"]["final_backlog"] == same["baseline"]["final_backlog"] and same["delta"]["final_backlog"] == 0
    surge = run(demand_change_pct=50)
    assert surge["scenario"]["final_backlog"] > surge["baseline"]["final_backlog"]
    assert surge["scenario"]["final_sla_compliance_pct"] < surge["baseline"]["final_sla_compliance_pct"]
    covered = run(demand_change_pct=50, crew_change_pct=100)
    assert covered["scenario"]["final_backlog"] <= surge["scenario"]["final_backlog"]
    assert covered["scenario"]["capacity_per_month"] > surge["baseline"]["capacity_per_month"]
    assert len(same["scenario"]["months"]) == 6 and "assumptions" in same
    for bad in ({"crew_change_pct": -90}, {"horizon_months": 9}, {"category": "bogus"}):
        assert client.post(f"{API}/scenarios/what-if", headers=h, json={"category": "roads", **bad}).status_code == 422
    assert client.post(f"{API}/scenarios/what-if", headers=h, json={"category": "parks"}).status_code == 422  # no history
    assert client.post(f"{API}/scenarios/what-if", json={"category": "roads"}).status_code == 401
