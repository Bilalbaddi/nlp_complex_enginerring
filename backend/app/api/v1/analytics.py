"""Phase 4 analytics: KPIs, service gaps, emerging issues. Ward officers / department heads are auto-scoped."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_read_db
from app.ingestion.normalize import CATEGORIES
from app.schemas.common import Envelope, ok
from app.services import cube as cubes
from app.services import emerging, kpis, service_gaps

router = APIRouter(tags=["analytics"])
Category = Query(None, pattern="^(" + "|".join(CATEGORIES) + ")$")
Months = Query(1, ge=1, le=36, description="Length of the period in whole months, ending at `month`")
def Month() -> Any:  # a NEW Query per parameter: FastAPI breaks when one instance is shared
    return Query(None, pattern=r"^\d{4}-\d{2}$", description="Last month of the period (default: newest month with data)")


@router.get("/kpis/summary", response_model=Envelope[dict[str, Any]])
def kpi_summary(
    ward_id: int | None = None, category: str | None = Category, months: int = Months, month: str | None = Month(),
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    return ok(kpis.summary(db, cube, ward_id=ward_id, category=category, months=months, month=month))


@router.get("/kpis/trend", response_model=Envelope[list[dict[str, Any]]])
def kpi_trend(
    ward_id: int | None = None, category: str | None = Category, date_from: str | None = Month(), date_to: str | None = Month(),
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    return ok(kpis.trend(cube, ward_id=ward_id, category=category, date_from=date_from, date_to=date_to))


@router.get("/kpis/rankings", response_model=Envelope[dict[str, Any]])
def kpi_rankings(
    by: Literal["ward", "department"] = "ward",
    metric: Literal[tuple(kpis.METRICS)] = "sla_compliance_pct",  # type: ignore[valid-type]
    months: int = Months, month: str | None = Month(), category: str | None = Category,
    _: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    return ok(kpis.rankings(cubes.get_cube(db), by=by, metric=metric, months=months, month=month, category=category))


@router.get("/service-gaps/sla", response_model=Envelope[list[dict[str, Any]]])
def sla_breaches(
    by: Literal["ward", "department", "category", "month"] = "department", months: int = Query(12, ge=1, le=36),
    ward_id: int | None = None, category: str | None = Category,
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    return ok(service_gaps.sla_breakdown(cube, by=by, months=months, ward_id=ward_id, category=category))


@router.get("/service-gaps/distribution", response_model=Envelope[dict[str, Any]])
def resolution_distribution(
    months: int = Query(12, ge=1, le=36), ward_id: int | None = None, category: str | None = Category,
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    return ok(service_gaps.distribution(db, cube, months=months, ward_id=ward_id, category=category))


@router.get("/service-gaps/backlog", response_model=Envelope[dict[str, Any]])
def backlog_aging(
    ward_id: int | None = None, category: str | None = Category,
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    return ok(service_gaps.backlog(db, cube, ward_id=ward_id, category=category))


@router.get("/service-gaps/repeat-hotspots", response_model=Envelope[list[dict[str, Any]]])
def repeat_hotspots(
    days: int = Query(90, ge=7, le=365), min_complaints: int = Query(5, ge=2, le=100), ward_id: int | None = None,
    category: str | None = Category, user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    return ok(service_gaps.repeat_hotspots(db, cube, days=days, min_n=min_complaints, ward_id=ward_id, category=category))


@router.get("/service-gaps/index", response_model=Envelope[dict[str, Any]])
def gap_index(
    months: int = Query(12, ge=1, le=36), category: str | None = Category,
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    _, category = cubes.scope_filters(user, None, category, cube)
    return ok(service_gaps.gap_index(cube, months=months, category=category))


@router.get("/emerging-issues", response_model=Envelope[dict[str, Any]])
def emerging_issues(
    category: str | None = Category, ward_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_read_db),
) -> dict[str, object]:
    cube = cubes.get_cube(db)
    ward_id, category = cubes.scope_filters(user, ward_id, category, cube)
    return ok(emerging.feed(db, cube, category=category, ward_id=ward_id))


@router.get("/emerging-issues/topics", response_model=Envelope[dict[str, Any]])
def emerging_topics(_: User = Depends(get_current_user), db: Session = Depends(get_read_db)) -> dict[str, object]:
    return ok(emerging.topics(db))
