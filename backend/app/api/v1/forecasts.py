"""Phase 6: volume forecasts, resource needs, ward risk, backtests and the what-if scenario tool."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.errors import ApiError
from app.db.models import User, UserRole
from app.db.session import get_read_db
from app.ingestion.normalize import CATEGORIES
from app.schemas.common import Envelope, ok
from app.services import cube as cubes
from app.services import forecasting

router = APIRouter(tags=["forecasting"])
planner = require_roles(UserRole.COMMISSIONER, UserRole.DEPARTMENT_HEAD, UserRole.ANALYST)


@router.get("/forecasts/volume", response_model=Envelope[dict[str, Any]])
def forecast_volume(
    category: str | None = Query(None, pattern="^(" + "|".join(CATEGORIES) + ")$"), ward_id: int | None = None,
    horizon: int = Query(6, ge=1, le=6), user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    if ward_id is not None and ward_id not in cube.ward_by_id:
        raise ApiError(404, "not_found", "Ward not found")
    return ok(forecasting.volume(cube, category=category, ward_id=ward_id, horizon=horizon))


@router.get("/forecasts/backtest", response_model=Envelope[dict[str, Any]])
def forecast_backtest(_: User = Depends(require_roles(UserRole.ANALYST, UserRole.COMMISSIONER)), db: Session = Depends(get_read_db)) -> dict[str, object]:
    """Rolling-origin backtest (MAE / MAPE) of every candidate model, per category."""
    return ok(forecasting.backtest_all(cubes.get_cube(db)))


@router.get("/forecasts/resources", response_model=Envelope[dict[str, Any]])
def forecast_resources(
    horizon: int = Query(6, ge=1, le=6),
    ratios: str | None = Query(None, description='JSON override of crew-days per complaint, e.g. {"drainage": 1.5}'),
    _: User = Depends(planner), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    return ok(forecasting.resources(db, cubes.get_cube(db), horizon=horizon, overrides=forecasting.parse_overrides(ratios)))


@router.get("/forecasts/risk", response_model=Envelope[dict[str, Any]])
def forecast_risk(user: User = Depends(get_current_user), db: Session = Depends(get_read_db)) -> dict[str, object]:
    data = forecasting.risk(cubes.get_cube(db))
    if user.ward_id and user.role == UserRole.WARD_OFFICER:
        data = {**data, "items": [i for i in data["items"] if i["ward_id"] == user.ward_id]}
    return ok(data)


class WhatIfRequest(BaseModel):
    category: str = Field(pattern="^(" + "|".join(CATEGORIES) + ")$")
    crew_change_pct: float = Field(0, ge=-80, le=300, description="Change in field crews / throughput, percent")
    demand_change_pct: float = Field(0, ge=-50, le=200, description="Extra demand on top of the forecast, percent")
    horizon_months: int = Field(6, ge=1, le=6)


@router.post("/scenarios/what-if", response_model=Envelope[dict[str, Any]])
def what_if(body: WhatIfRequest, _: User = Depends(planner), db: Session = Depends(get_read_db)) -> dict[str, object]:
    r = forecasting.what_if(db, cubes.get_cube(db), category=body.category, crew_change_pct=body.crew_change_pct,
                            demand_change_pct=body.demand_change_pct, horizon=body.horizon_months)
    if "error" in r:
        raise ApiError(422, "insufficient_data", r["error"])
    return ok(r)
