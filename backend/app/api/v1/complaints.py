import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.core.pii import mask_name, mask_phone
from app.db.models import Complaint, ComplaintStatus, Department, User, UserRole, Ward
from app.db.session import get_db, get_read_db
from app.ingestion import pipeline
from app.schemas.common import Envelope, Page, PageParams, make_page, ok
from app.schemas.complaint import ComplaintCreate, ComplaintOut
from app.services import audit, jobs

router = APIRouter(prefix="/complaints", tags=["complaints"])

PII_ROLES = {UserRole.ADMIN, UserRole.WARD_OFFICER}  # roles that need to contact citizens


def _select() -> Select[Any]:
    return (
        select(
            Complaint, Ward.name, Department.name, func.ST_Y(Complaint.location), func.ST_X(Complaint.location)
        )
        .outerjoin(Ward, Ward.id == Complaint.ward_id)
        .outerjoin(Department, Department.id == Complaint.department_id)
    )


def _to_out(row: Any, user: User) -> ComplaintOut:
    c, ward_name, dept_name, lat, lon = row
    end = c.resolved_at or datetime.now(timezone.utc)
    breached: bool | None = None
    if c.sla_hours:
        hours = c.resolution_hours if c.resolved_at and c.resolution_hours is not None else (end - c.submitted_at).total_seconds() / 3600
        breached = True if hours > c.sla_hours else (False if c.resolved_at else None)
    see_pii = user.role in PII_ROLES
    return ComplaintOut(
        id=c.id, external_id=c.external_id, description=c.description, language=c.language, category=c.category,
        status=c.status, channel=c.channel, ward_id=c.ward_id, ward_name=ward_name, department_id=c.department_id,
        department_name=dept_name, address=c.address, latitude=lat, longitude=lon, submitted_at=c.submitted_at,
        resolved_at=c.resolved_at, resolution_hours=c.resolution_hours, sla_hours=c.sla_hours, sla_breached=breached,
        predicted_category=c.predicted_category, category_confidence=c.category_confidence,
        sentiment_score=c.sentiment_score, urgency_score=c.urgency_score, needs_review=c.needs_review,
        review_reason=c.review_reason, reviewed_at=c.reviewed_at, duplicate_of_id=c.duplicate_of_id,
        predicted_department_id=c.predicted_department_id, entities=c.entities,
        citizen_name=c.citizen_name if see_pii else mask_name(c.citizen_name),
        citizen_phone=c.citizen_phone if see_pii else mask_phone(c.citizen_phone),
    )


def scope(stmt: Select[Any], user: User) -> Select[Any]:
    """Ward officers and department heads only see their own ward / department when they are assigned to one."""
    if user.role == UserRole.WARD_OFFICER and user.ward_id:
        stmt = stmt.where(Complaint.ward_id == user.ward_id)
    if user.role == UserRole.DEPARTMENT_HEAD and user.department_id:
        stmt = stmt.where(Complaint.department_id == user.department_id)
    return stmt


@router.get("", response_model=Envelope[Page[ComplaintOut]])
def list_complaints(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    ward_id: int | None = None,
    department_id: int | None = None,
    category: str | None = Query(None, max_length=64),
    status: ComplaintStatus | None = None,
    language: str | None = Query(None, max_length=16),
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(None, max_length=100, description="substring search in the description"),
    needs_review: bool | None = None,
    sort: Literal["newest", "oldest", "urgency"] = "newest",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
) -> dict[str, object]:
    params = PageParams(page=page, page_size=page_size)
    stmt = scope(_select(), user)
    if ward_id is not None:
        stmt = stmt.where(Complaint.ward_id == ward_id)
    if department_id is not None:
        stmt = stmt.where(Complaint.department_id == department_id)
    if category:
        stmt = stmt.where(Complaint.category == category)
    if status:
        stmt = stmt.where(Complaint.status == status)
    if language:
        stmt = stmt.where(Complaint.language == language)
    if date_from:
        stmt = stmt.where(Complaint.submitted_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(Complaint.submitted_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    if needs_review is not None:
        stmt = stmt.where(Complaint.needs_review == needs_review)
    if q:
        like = "%" + q.replace("%", r"\%").replace("_", r"\_") + "%"
        stmt = stmt.where(or_(Complaint.description.ilike(like), Complaint.external_id.ilike(like)))
    order = {
        "newest": [Complaint.submitted_at.desc(), Complaint.id.desc()],
        "oldest": [Complaint.submitted_at.asc(), Complaint.id.asc()],
        "urgency": [Complaint.urgency_score.desc().nulls_last(), Complaint.submitted_at.desc()],
    }[sort]
    # The total comes back in the same round trip as the page (window count) instead of a second query.
    rows = db.execute(
        stmt.add_columns(func.count().over().label("total")).order_by(*order).offset(params.offset).limit(params.page_size)
    ).all()
    if rows:
        total = int(rows[0][-1])
    elif params.page > 1:  # page beyond the end: no row carries the total, so count separately
        total = db.scalar(select(func.count()).select_from(stmt.with_only_columns(Complaint.id).subquery())) or 0
    else:
        total = 0
    return ok(make_page([_to_out(r[:5], user) for r in rows], total, params))


@router.get("/{complaint_id}", response_model=Envelope[ComplaintOut])
def get_complaint(
    complaint_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_read_db)
) -> dict[str, object]:
    row = db.execute(scope(_select(), user).where(Complaint.id == complaint_id)).first()
    if row is None:
        raise ApiError(404, "not_found", "Complaint not found")
    return ok(_to_out(row, user))


@router.post("", response_model=Envelope[ComplaintOut], status_code=201)
def create_complaint(
    body: ComplaintCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    """Register one complaint (the real-time feed). Goes through the same validation and dedup as file imports."""
    raw = body.model_dump(exclude_none=True)
    raw["external_id"] = f"API-{uuid.uuid4().hex[:10].upper()}"
    raw.setdefault("submitted_at", datetime.now(timezone.utc))
    report = pipeline.ingest_complaints(db, [raw], cache_refs=True)
    if report.rejected:
        raise ApiError(422, "invalid_complaint", "The complaint was rejected", report.rejected_rows[0]["errors"])
    if report.duplicates:
        raise ApiError(409, "duplicate_complaint", "This complaint was already submitted today")
    row = db.execute(_select().where(Complaint.external_id == raw["external_id"])).first()
    assert row is not None
    audit.record(db, "complaint_create", user, resource_type="complaint", resource_id=row[0].id, request=request)
    jobs.enqueue("nlp.enrich_complaint", {"complaint_id": row[0].id}, created_by=user.id)  # classify, score, dedupe
    return ok(_to_out(row, user))
