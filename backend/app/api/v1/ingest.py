"""File uploads: complaint / feedback / performance tables, and policy documents. Files go to private Storage and are
processed by a background job (inline in development); the ingestion log is the status/audit record."""

from pathlib import PurePosixPath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.models import Document, DocumentChunk, IngestionLog, User, UserRole
from app.db.session import get_db, get_read_db
from app.ingestion import pipeline
from app.ingestion.documents import ALLOWED_DOC_EXTENSIONS
from app.ingestion.readers import ALLOWED_TABLE_EXTENSIONS
from app.schemas.common import Envelope, Page, PageParams, make_page, ok
from app.schemas.complaint import DocumentOut, IngestionLogOut
from app.services import audit, jobs, storage

router = APIRouter(tags=["ingestion"])
uploader = require_roles(UserRole.ANALYST)  # admin is always allowed

MAX_TABLE_BYTES = 25 * 1024 * 1024
MAX_DOC_BYTES = 15 * 1024 * 1024


def _read_upload(file: UploadFile, allowed: set[str], max_bytes: int) -> tuple[str, bytes]:
    name = file.filename or ""
    if PurePosixPath(name.lower()).suffix not in allowed:
        raise ApiError(415, "unsupported_file_type", f"Allowed file types: {', '.join(sorted(allowed))}")
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError(413, "file_too_large", f"File is larger than {max_bytes // (1024 * 1024)} MB")
    if not data:
        raise ApiError(422, "empty_file", "The uploaded file is empty")
    return name, data


@router.post("/ingest/{kind}", response_model=Envelope[IngestionLogOut], status_code=202)
def upload_table(
    kind: Literal["complaints", "feedback", "performance"],
    request: Request,
    file: Annotated[UploadFile, File(description="CSV, Excel or JSON file")],
    user: User = Depends(uploader),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    name, data = _read_upload(file, ALLOWED_TABLE_EXTENSIONS, MAX_TABLE_BYTES)
    bucket = get_settings().storage_bucket_uploads
    path = storage.upload(bucket, storage.new_path(kind, name), data, file.content_type or "application/octet-stream")
    entry = pipeline.start_log(db, kind, name, user.id, storage_bucket=bucket, storage_path=path, status="queued")
    audit.record(db, "ingest_upload", user, resource_type="ingestion_log", resource_id=entry.id,
                 detail={"kind": kind, "filename": name, "bytes": len(data)}, request=request)
    jobs.enqueue("ingest.file", {"log_id": entry.id, "kind": kind}, created_by=user.id)
    db.refresh(entry)
    return ok(IngestionLogOut.model_validate(entry))


@router.get("/ingestion-logs", response_model=Envelope[Page[IngestionLogOut]])
def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    source_type: str | None = Query(None, max_length=32),
    _: User = Depends(uploader),
    db: Session = Depends(get_read_db),
) -> dict[str, object]:
    params = PageParams(page=page, page_size=page_size)
    stmt = select(IngestionLog)
    if source_type:
        stmt = stmt.where(IngestionLog.source_type == source_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(IngestionLog.id.desc()).offset(params.offset).limit(params.page_size)).all()
    return ok(make_page([IngestionLogOut.model_validate(r) for r in rows], total, params))


@router.get("/ingestion-logs/{log_id}", response_model=Envelope[IngestionLogOut])
def get_log(log_id: int, _: User = Depends(uploader), db: Session = Depends(get_read_db)) -> dict[str, object]:
    entry = db.get(IngestionLog, log_id)
    if entry is None:
        raise ApiError(404, "not_found", "Ingestion log not found")
    return ok(IngestionLogOut.model_validate(entry))


# ---------------------------------------------------------------- documents
@router.post("/documents", response_model=Envelope[IngestionLogOut], status_code=202)
def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(description="PDF, DOCX, TXT or MD")],
    title: Annotated[str, Form(min_length=3, max_length=255)],
    doc_type: Annotated[Literal["policy", "report"], Form()] = "policy",
    user: User = Depends(uploader),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    name, data = _read_upload(file, ALLOWED_DOC_EXTENSIONS, MAX_DOC_BYTES)
    bucket = get_settings().storage_bucket_documents
    path = storage.upload(bucket, storage.new_path("documents", name), data, file.content_type or "application/octet-stream")
    entry = pipeline.start_log(db, "document", name, user.id, storage_bucket=bucket, storage_path=path, status="queued")
    audit.record(db, "document_upload", user, resource_type="ingestion_log", resource_id=entry.id,
                 detail={"filename": name, "title": title, "bytes": len(data)}, request=request)
    jobs.enqueue("ingest.document", {"log_id": entry.id, "title": title, "doc_type": doc_type}, created_by=user.id)
    db.refresh(entry)
    return ok(IngestionLogOut.model_validate(entry))


@router.get("/documents", response_model=Envelope[Page[DocumentOut]])
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    doc_type: Literal["policy", "report"] | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
) -> dict[str, object]:
    params = PageParams(page=page, page_size=page_size)
    stmt = select(Document)
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Document.id.desc()).offset(params.offset).limit(params.page_size)).all()
    return ok(make_page([DocumentOut.model_validate(r) for r in rows], total, params))


@router.get("/documents/{doc_id}/download", response_model=Envelope[dict[str, str]])
def download_document(doc_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_read_db)) -> dict[str, object]:
    """A short-lived signed URL (5 minutes) for the original file; buckets are private."""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise ApiError(404, "not_found", "Document not found")
    if not doc.storage_bucket or not doc.storage_path:
        raise ApiError(404, "no_file", "The original file is not stored")
    return ok({"url": storage.signed_url(doc.storage_bucket, doc.storage_path, 300), "filename": doc.source_filename or ""})


@router.delete("/documents/{doc_id}", response_model=Envelope[None])
def delete_document(
    doc_id: int, request: Request, admin: User = Depends(require_roles()), db: Session = Depends(get_db)
) -> dict[str, object]:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise ApiError(404, "not_found", "Document not found")
    bucket, path, title = doc.storage_bucket, doc.storage_path, doc.title
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
    db.delete(doc)
    db.commit()
    if bucket and path:
        storage.delete(bucket, [path])
    audit.record(db, "document_delete", admin, resource_type="document", resource_id=doc_id, detail={"title": title}, request=request)
    return ok(None)
