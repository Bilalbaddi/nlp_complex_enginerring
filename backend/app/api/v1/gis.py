"""Phase 5 GIS endpoints: heatmap, DBSCAN hotspots, ward choropleths, radius search, ward summaries."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.db.models import User
from app.db.session import get_read_db
from app.ingestion.normalize import CATEGORIES
from app.schemas.common import Envelope, ok
from app.services import cube as cubes
from app.services import gis

router = APIRouter(prefix="/gis", tags=["gis"])
Category = Query(None, pattern="^(" + "|".join(CATEGORIES) + ")$")
def Month() -> Any:  # a NEW Query per parameter: FastAPI breaks when one instance is shared
    return Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM (month granularity, for the time slider)")


@router.get("/heatmap", response_model=Envelope[dict[str, Any]])
def heatmap(
    category: str | None = Category, ward_id: int | None = None, date_from: str | None = Month(), date_to: str | None = Month(),
    cell: float = Query(0.003, ge=0.001, le=0.02), user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    ward_id, category = cubes.scope_filters(user, ward_id, category, cubes.get_cube(db))
    return ok(gis.heatmap(db, category=category, ward_id=ward_id, date_from=date_from, date_to=date_to, cell=cell))


@router.get("/hotspots", response_model=Envelope[dict[str, Any]])
def hotspots(
    category: str | None = Category, ward_id: int | None = None, date_from: str | None = Month(), date_to: str | None = Month(),
    eps_m: float = Query(100, ge=30, le=2000), min_samples: int = Query(50, ge=3, le=500),
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    return ok(gis.hotspots(db, cube, category=category, ward_id=ward_id, date_from=date_from, date_to=date_to, eps_m=eps_m, min_samples=min_samples))


@router.get("/choropleth", response_model=Envelope[dict[str, Any]])
def choropleth(
    metric: Literal[tuple(gis.CHOROPLETH_METRICS)] = "complaints_per_10k",  # type: ignore[valid-type]
    category: str | None = Category, date_from: str | None = Month(), date_to: str | None = Month(),
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    _, category = cubes.scope_filters(user, None, category, cube)
    return ok(gis.choropleth(db, cube, metric=metric, category=category, date_from=date_from, date_to=date_to))


@router.get("/within", response_model=Envelope[dict[str, Any]])
def within(
    lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), radius_m: float = Query(500, ge=10, le=10000),
    category: str | None = Category, limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    return ok(gis.within(db, lat=lat, lon=lon, radius_m=radius_m, category=category, limit=limit))


@router.get("/wards/{ward_id}/summary", response_model=Envelope[dict[str, Any]])
def ward_summary(ward_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_read_db)) -> dict[str, object]:
    cube = cubes.get_cube(db)
    if ward_id not in cube.ward_by_id:
        raise ApiError(404, "not_found", "Ward not found")
    ward_id, _ = cubes.scope_filters(user, ward_id, None, cube)
    return ok(gis.ward_summary(db, cube, ward_id))
