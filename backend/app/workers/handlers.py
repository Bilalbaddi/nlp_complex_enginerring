"""Built-in job handlers. Later phases register ML training, brief generation, etc. here with @job_handler."""

import logging
from datetime import datetime, timezone
from typing import Any

from app.core import ratelimit
from app.db.models import IngestionLog
from app.db.session import SessionLocal
from app.ingestion import pipeline
from app.ingestion.documents import DocumentError, ingest_document
from app.ingestion.readers import FileFormatError, read_rows
from app.ml import embed_store, enrich
from app.services import cache, jobs, storage
from app.services import cube as cubes

log = logging.getLogger(__name__)


@jobs.job_handler("system.ping")
def ping(payload: dict[str, Any]) -> str:
    return "pong"


@jobs.job_handler("system.fail")
def always_fail(payload: dict[str, Any]) -> None:
    """Used by tests/demos to exercise retries."""
    raise RuntimeError(payload.get("message", "intentional failure"))


@jobs.job_handler("system.cleanup")
def cleanup(payload: dict[str, Any]) -> dict[str, int]:
    """Remove expired cache rows, old rate-limit windows and old finished jobs."""
    return {
        "cache_entries": cache.cleanup_expired(),
        "rate_limits": ratelimit.cleanup_expired(),
        "jobs": jobs.purge_finished(),
    }


@jobs.job_handler("ingest.file")
def ingest_file(payload: dict[str, Any]) -> dict[str, Any]:
    """Process an uploaded complaints/feedback/performance file from Storage. payload: {log_id, kind}."""
    with SessionLocal() as db:
        entry = db.get(IngestionLog, payload["log_id"])
        if entry is None or entry.storage_bucket is None or entry.storage_path is None:
            raise ValueError("ingestion log or stored file not found")
        entry.status = "running"
        db.commit()
        try:
            data = storage.download(entry.storage_bucket, entry.storage_path)
            rows = read_rows(data, entry.filename or entry.storage_path)
            report = pipeline.INGESTORS[payload["kind"]](db, rows, entry=entry)
            if report.ok:
                cubes.invalidate()  # KPIs, gaps, forecasts and maps are built from cached aggregates of the data
            return {"status": report.status, "ok": report.ok, "rejected": report.rejected, "duplicates": report.duplicates}
        except FileFormatError as exc:  # permanent: retrying cannot help
            db.rollback()
            pipeline.finish_log(db, entry, error=str(exc))
            return {"status": "failed", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - transient: recorded, then retried by the job queue
            db.rollback()
            pipeline.finish_log(db, entry, error=f"{type(exc).__name__}: {exc}")
            raise


@jobs.job_handler("ingest.document")
def ingest_document_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract, chunk and store an uploaded document. payload: {log_id, title, doc_type}."""
    with SessionLocal() as db:
        entry = db.get(IngestionLog, payload["log_id"])
        if entry is None or entry.storage_bucket is None or entry.storage_path is None:
            raise ValueError("ingestion log or stored file not found")
        entry.status = "running"
        db.commit()
        try:
            data = storage.download(entry.storage_bucket, entry.storage_path)
            doc, created = ingest_document(
                db, data, entry.filename or "document", title=payload["title"], doc_type=payload.get("doc_type", "policy"),
                user_id=entry.user_id, storage_bucket=entry.storage_bucket, storage_path=entry.storage_path,
            )
            entry.rows_total, entry.rows_ok = doc.num_chunks, doc.num_chunks if created else 0
            entry.rows_duplicate = 0 if created else 1
            entry.status = "success" if created else "partial"
            entry.errors = {"document_id": doc.id, "chunks": doc.num_chunks, "created": created}
            if not created:  # identical text already stored: drop the redundant upload to save storage
                entry.errors["note"] = "an identical document already exists"
                storage.delete(entry.storage_bucket, [entry.storage_path])
            entry.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"document_id": doc.id, "created": created}
        except DocumentError as exc:
            db.rollback()
            pipeline.finish_log(db, entry, error=str(exc))
            return {"status": "failed", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            pipeline.finish_log(db, entry, error=f"{type(exc).__name__}: {exc}")
            raise


@jobs.job_handler("nlp.enrich_complaint")
def enrich_complaint_job(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Classify, score and deduplicate one complaint (embedding it via Hugging Face when configured)."""
    return enrich.enrich_complaint(int(payload["complaint_id"]))


@jobs.job_handler("nlp.predict_all")
def predict_all_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Re-run every prediction for every complaint with the active models."""
    with SessionLocal() as db:
        predictor = enrich.load_predictor(db)
    if predictor is None:
        raise ValueError("No trained category model. Run `python scripts/tasks.py train` first.")
    return enrich.enrich_all(predictor, use_embeddings=bool(payload.get("use_embeddings", True)))


@jobs.job_handler("embeddings.backfill")
def embeddings_backfill_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Embed complaints that have no embedding (Hugging Face Inference API). payload: {limit?, max_seconds?}."""
    return embed_store.backfill_embeddings(limit=payload.get("limit"), max_seconds=payload.get("max_seconds", 600))


@jobs.job_handler("brief.generate")
def brief_generate_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate (and cache) the monthly policy brief in the background. payload: {refresh?: bool}."""
    from app.llm import briefs
    from app.services import cube as cubes

    with SessionLocal() as db:
        b = briefs.policy_brief(db, cubes.get_cube(db), refresh=bool(payload.get("refresh", True)))
    return {"month": b["month"], "chars": len(b["markdown"]), "model": b["label"]["model"]}


@jobs.job_handler("documents.embed")
def documents_embed_job(payload: dict[str, Any]) -> dict[str, int]:
    """Embed policy-document chunks without vectors (Hugging Face API)."""
    from app.llm import rag

    return rag.embed_pending(int(payload.get("limit", 200)))
