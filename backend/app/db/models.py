"""SQLAlchemy 2.x ORM models for SG-DSS.

Geometry columns use PostGIS (SRID 4326); embeddings use pgvector (dimension from settings).
Enumerations are stored as plain strings (native_enum=False) to keep migrations simple.

Storage (Supabase free tier = 500 MB): embeddings are pgvector `halfvec` (2 bytes/dim -> 768 B/row). Only
document_chunks gets an ANN (HNSW) index; complaint embeddings are compared exactly within a ward/category/
time window, which avoids an index as large as the data itself.
"""

import enum
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    COMMISSIONER = "commissioner"
    DEPARTMENT_HEAD = "department_head"
    WARD_OFFICER = "ward_officer"
    ANALYST = "analyst"
    ADMIN = "admin"


class ComplaintStatus(str, enum.Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REJECTED = "rejected"


def _enum(e: type[enum.Enum]) -> Enum:
    return Enum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    sla_hours: Mapped[int] = mapped_column(Integer, default=72)
    category: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)  # complaint category it serves


class Ward(Base, TimestampMixin):
    __tablename__ = "wards"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    population: Mapped[int] = mapped_column(Integer, default=0)
    area_sq_km: Mapped[float] = mapped_column(Float, default=0.0)
    road_km: Mapped[float] = mapped_column(Float, default=0.0)
    water_connections: Mapped[int] = mapped_column(Integer, default=0)
    geom: Mapped[Any] = mapped_column(Geometry("MULTIPOLYGON", srid=4326), nullable=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    hashed_password: Mapped[str] = mapped_column(String(128))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    ward_id: Mapped[int | None] = mapped_column(ForeignKey("wards.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    department: Mapped[Department | None] = relationship()
    ward: Mapped[Ward | None] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Complaint(Base):
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ground-truth / reported
    status: Mapped[ComplaintStatus] = mapped_column(_enum(ComplaintStatus), default=ComplaintStatus.OPEN)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ward_id: Mapped[int | None] = mapped_column(ForeignKey("wards.id"), nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    sla_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # PII (masked in analytics views, stripped before any LLM call)
    citizen_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    citizen_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # NLP outputs (Phase 3)
    predicted_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    urgency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("complaints.id"), nullable=True)
    entities: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    embedding: Mapped[Any] = mapped_column(HALFVEC(EMBEDDING_DIM), nullable=True)

    dedup_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ward: Mapped[Ward | None] = relationship(foreign_keys=[ward_id])
    department: Mapped[Department | None] = relationship(foreign_keys=[department_id])

    __table_args__ = (
        Index("ix_complaints_ward_submitted", "ward_id", "submitted_at"),
        Index("ix_complaints_category_submitted", "category", "submitted_at"),
        Index("ix_complaints_dept_status", "department_id", "status"),
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int | None] = mapped_column(ForeignKey("complaints.id"), nullable=True)
    ward_id: Mapped[int | None] = mapped_column(ForeignKey("wards.id"), nullable=True, index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1-5
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dedup_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ServicePerformance(Base):
    """Monthly service performance record per department (optionally per ward)."""

    __tablename__ = "service_performance"

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    ward_id: Mapped[int | None] = mapped_column(ForeignKey("wards.id"), nullable=True)
    period: Mapped[date] = mapped_column(Date)  # first day of month
    complaints_received: Mapped[int] = mapped_column(Integer, default=0)
    complaints_resolved: Mapped[int] = mapped_column(Integer, default=0)
    sla_breaches: Mapped[int] = mapped_column(Integer, default=0)
    avg_resolution_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    staff_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_spent: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("department_id", "ward_id", "period", name="uq_perf_dept_ward_period"),
        # NULL ward_id rows (city-wide) are not covered by the constraint above, so add a partial unique index
        Index("uq_perf_dept_period_citywide", "department_id", "period", unique=True, postgresql_where=text("ward_id IS NULL")),
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    doc_type: Mapped[str] = mapped_column(String(32), default="policy")  # policy | report
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    num_chunks: Mapped[int] = mapped_column(Integer, default=0)
    storage_bucket: Mapped[str | None] = mapped_column(String(64), nullable=True)  # original file in Supabase Storage
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(HALFVEC(EMBEDDING_DIM), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
    )


class IngestionLog(Base):
    __tablename__ = "ingestion_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32))  # complaints | feedback | performance | document
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_bucket: Mapped[str | None] = mapped_column(String(64), nullable=True)  # uploaded file in Supabase Storage
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | success | partial | failed
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_ok: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ModelRun(Base):
    """Registry of trained ML models: metrics, parameters and where the artifact is stored."""

    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)  # e.g. category_tfidf_lr
    task: Mapped[str] = mapped_column(String(32))  # category | duplicates | breach_risk | ...
    version: Mapped[str] = mapped_column(String(32))
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    n_train: Mapped[int] = mapped_column(Integer, default=0)
    n_test: Mapped[int] = mapped_column(Integer, default=0)
    train_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    artifact_bucket: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)  # the model used for predictions
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CacheEntry(Base):
    """Key/value cache with TTL (analytics results, LLM outputs). See services/cache.py."""

    __tablename__ = "cache_entries"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[Any] = mapped_column(JsonType)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(Base):
    """Background job queue claimed with SELECT ... FOR UPDATE SKIP LOCKED. See services/jobs.py."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[Any] = mapped_column(JsonType, nullable=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_jobs_claim", "status", "run_at"),)


class RateLimit(Base):
    """Fixed-window request counters (one row per key and window). See core/ratelimit.py."""

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(128))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (PrimaryKeyConstraint("key", "window_start"),)
