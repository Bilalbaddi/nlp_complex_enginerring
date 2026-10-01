from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ComplaintStatus


class ComplaintOut(BaseModel):
    id: int
    external_id: str | None
    description: str
    language: str | None
    category: str | None
    status: ComplaintStatus
    channel: str | None
    ward_id: int | None
    ward_name: str | None
    department_id: int | None
    department_name: str | None
    address: str | None
    latitude: float | None
    longitude: float | None
    submitted_at: datetime
    resolved_at: datetime | None
    resolution_hours: float | None
    sla_hours: int | None
    sla_breached: bool | None  # None while open and still within the SLA window
    predicted_category: str | None
    category_confidence: float | None
    sentiment_score: float | None
    urgency_score: float | None
    needs_review: bool
    review_reason: str | None
    reviewed_at: datetime | None
    duplicate_of_id: int | None
    predicted_department_id: int | None
    entities: dict[str, Any] | None
    citizen_name: str | None  # masked unless the viewer's role may see PII
    citizen_phone: str | None


class ComplaintCreate(BaseModel):
    """A new complaint from the (simulated) real-time feed."""

    description: str = Field(min_length=5, max_length=5000)
    submitted_at: datetime | None = None
    ward: str | None = Field(default=None, max_length=128)
    department: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    channel: str = Field(default="api", max_length=32)
    address: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    citizen_name: str | None = Field(default=None, max_length=128)
    citizen_phone: str | None = Field(default=None, max_length=32)


class WardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    population: int
    area_sq_km: float
    road_km: float
    water_connections: int
    centroid_lat: float | None = None
    centroid_lon: float | None = None


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    category: str | None
    sla_hours: int


class IngestionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_type: str
    filename: str | None
    status: str
    rows_total: int
    rows_ok: int
    rows_rejected: int
    rows_duplicate: int
    errors: dict[str, Any] | None
    user_id: int | None
    started_at: datetime
    finished_at: datetime | None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    doc_type: str
    source_filename: str | None
    num_chunks: int
    uploaded_by: int | None
    created_at: datetime
