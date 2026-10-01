from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.errors import ApiError
from app.db.models import Job, JobStatus, User
from app.db.session import get_db, get_read_db
from app.schemas.common import Envelope, Page, PageParams, make_page, ok
from app.services import audit, jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    max_attempts: int
    error: str | None
    result: Any | None
    run_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class JobCreate(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("/types", response_model=Envelope[list[str]])
def job_types(_: User = Depends(require_admin)) -> dict[str, object]:
    return ok(jobs.registered_types())


@router.get("", response_model=Envelope[Page[JobOut]])
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status: JobStatus | None = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_read_db),
) -> dict[str, object]:
    params = PageParams(page=page, page_size=page_size)
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Job.id.desc()).offset(params.offset).limit(params.page_size)).all()
    return ok(make_page([JobOut.model_validate(r) for r in rows], total, params))


@router.post("", response_model=Envelope[JobOut], status_code=202)
def create_job(
    body: JobCreate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, object]:
    if body.type not in jobs.registered_types():
        raise ApiError(422, "unknown_job_type", f"Unknown job type '{body.type}'")
    job_id = jobs.enqueue(body.type, body.payload, created_by=admin.id)
    audit.record(db, "job_enqueue", admin, resource_type="job", resource_id=job_id, detail={"type": body.type}, request=request)
    job = db.get(Job, job_id)
    db.refresh(job)
    return ok(JobOut.model_validate(job))


@router.get("/{job_id}", response_model=Envelope[JobOut])
def get_job(job_id: int, _: User = Depends(require_admin), db: Session = Depends(get_read_db)) -> dict[str, object]:
    job = db.get(Job, job_id)
    if job is None:
        raise ApiError(404, "not_found", "Job not found")
    return ok(JobOut.model_validate(job))
