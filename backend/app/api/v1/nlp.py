"""NLP endpoints: analyse text, model registry, and the human review queue."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.errors import ApiError
from app.db.models import Complaint, Department, ModelRun, User, UserRole
from app.db.session import get_db, get_read_db
from app.ingestion.normalize import CATEGORIES
from app.ml import enrich
from app.ml.dataset import Row
from app.schemas.common import Envelope, Page, PageParams, make_page, ok
from app.services import audit

router = APIRouter(prefix="/nlp", tags=["nlp"])
reviewer = require_roles(UserRole.WARD_OFFICER, UserRole.DEPARTMENT_HEAD, UserRole.ANALYST)


class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    language: str | None = Field(default=None, pattern="^(en|hi|mr|hinglish)$")


class AnalysisOut(BaseModel):
    language: str
    category: str
    confidence: float
    top_categories: list[dict[str, Any]]
    department: str | None
    sentiment: float
    urgency: float
    urgency_level: str
    urgency_reasons: list[str]
    entities: dict[str, list[str]]
    needs_review: bool
    review_reason: str | None


class ModelRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    task: str
    version: str
    metrics: dict[str, Any]
    params: dict[str, Any]
    n_train: int
    n_test: int
    train_seconds: float
    is_active: bool
    created_at: datetime


class ReviewRequest(BaseModel):
    category: str = Field(min_length=3, max_length=64)


@router.post("/analyze", response_model=Envelope[AnalysisOut])
def analyze(body: AnalyzeRequest, _: User = Depends(get_current_user), db: Session = Depends(get_read_db)) -> dict[str, object]:
    """Analyse free text with the active models (nothing is stored). Uses the offline TF-IDF model only."""
    predictor = enrich.get_predictor(db)
    if predictor is None:
        raise ApiError(503, "models_not_trained", "No trained models yet; run `python scripts/tasks.py train`")
    row = Row(0, None, body.text, body.language, None, None, None, datetime.now(timezone.utc), None, None, None, None,
              "open", None, None)
    e = predictor.enrich([row], detect_duplicates=False)[0]
    dept = next((d for c, d in predictor.dept_by_category.items() if c == e.predicted_category), None)
    dept_name = db.scalar(select(Department.name).where(Department.id == dept)) if dept else None
    return ok(AnalysisOut(
        language=e.language, category=e.predicted_category, confidence=round(e.confidence, 4),
        top_categories=[{"category": c, "probability": round(p, 4)} for c, p in e.top3], department=dept_name,
        sentiment=round(e.sentiment, 3), urgency=e.urgency, urgency_level=e.urgency_level,
        urgency_reasons=e.urgency_reasons, entities=e.entities, needs_review=e.needs_review, review_reason=e.review_reason,
    ))


@router.get("/models", response_model=Envelope[list[ModelRunOut]])
def list_models(
    active_only: bool = False, _: User = Depends(require_roles(UserRole.ANALYST)), db: Session = Depends(get_read_db)
) -> dict[str, object]:
    stmt = select(ModelRun).order_by(ModelRun.id.desc())
    if active_only:
        stmt = stmt.where(ModelRun.is_active.is_(True))
    return ok([ModelRunOut.model_validate(r) for r in db.scalars(stmt)])


@router.get("/review/stats", response_model=Envelope[dict[str, Any]])
def review_stats(_: User = Depends(reviewer), db: Session = Depends(get_read_db)) -> dict[str, object]:
    row = db.execute(
        select(
            func.count().filter(Complaint.needs_review.is_(True)),
            func.count().filter(Complaint.reviewed_at.isnot(None)),
            func.count().filter(Complaint.predicted_category.isnot(None)),
            func.count(),
        )
    ).one()
    return ok({"pending": row[0], "reviewed": row[1], "predicted": row[2], "total": row[3]})


@router.post("/review/{complaint_id}", response_model=Envelope[dict[str, Any]])
def review_complaint(
    complaint_id: int, body: ReviewRequest, request: Request, user: User = Depends(reviewer), db: Session = Depends(get_db)
) -> dict[str, object]:
    """Human decision for a flagged complaint: set the correct category (routes it to that department) and clear the flag."""
    if body.category not in CATEGORIES:
        raise ApiError(422, "invalid_category", f"category must be one of: {', '.join(CATEGORIES)}")
    c = db.get(Complaint, complaint_id)
    if c is None or (user.role == UserRole.WARD_OFFICER and user.ward_id and c.ward_id != user.ward_id):
        raise ApiError(404, "not_found", "Complaint not found")
    dept = db.scalar(select(Department.id).where(Department.category == body.category))
    before = {"category": c.category, "predicted": c.predicted_category}
    db.execute(
        update(Complaint).where(Complaint.id == complaint_id).values(
            category=body.category, department_id=dept, needs_review=False, reviewed_at=datetime.now(timezone.utc),
            reviewed_by_id=user.id,
        )
    )
    db.commit()
    audit.record(db, "complaint_review", user, resource_type="complaint", resource_id=complaint_id,
                 detail={**before, "new_category": body.category}, request=request)
    return ok({"id": complaint_id, "category": body.category, "department_id": dept})
