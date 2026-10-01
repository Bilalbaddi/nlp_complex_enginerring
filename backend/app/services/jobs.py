"""Job queue in Postgres (table `jobs`).

- `enqueue()` inserts a job. With JOBS_INLINE on (default in development) it is also run immediately in a
  background thread of the API process, so no worker is needed during development.
- `claim_next()` uses SELECT ... FOR UPDATE SKIP LOCKED so several workers never take the same job.
- Failures are retried with exponential backoff until `max_attempts`, then marked failed.
"""

import logging
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update

from app.core.config import get_settings
from app.db.models import Job, JobStatus
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Any]
_handlers: dict[str, Handler] = {}
_inline_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jobs-inline")


def job_handler(job_type: str) -> Callable[[Handler], Handler]:
    """Register a function as the handler for `job_type`. It receives the payload; return value must be JSON."""

    def register(fn: Handler) -> Handler:
        _handlers[job_type] = fn
        return fn

    return register


def registered_types() -> list[str]:
    import app.workers.handlers  # noqa: F401  (importing registers the built-in handlers)

    return sorted(_handlers)


def enqueue(
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    created_by: int | None = None,
    delay_seconds: int = 0,
    max_attempts: int | None = None,
) -> int:
    """Create a job and return its id. Runs it right away (async, in-process) when JOBS_INLINE is on."""
    s = get_settings()
    if job_type not in registered_types():
        raise ValueError(f"Unknown job type: {job_type}")
    with SessionLocal() as db:
        job = Job(
            type=job_type,
            payload=payload or {},
            created_by=created_by,
            max_attempts=max_attempts or s.job_max_attempts,
            run_at=datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
        )
        db.add(job)
        db.commit()
        job_id = job.id
    if s.jobs_run_inline and delay_seconds == 0:
        _inline_pool.submit(run_job, job_id)
    return job_id


def _reclaim_stale(db: Any) -> None:
    """Jobs stuck in `running` (worker died) go back to the queue."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=get_settings().job_stale_after_seconds)
    db.execute(
        update(Job)
        .where(Job.status == JobStatus.RUNNING, Job.locked_at < cutoff)
        .values(status=JobStatus.QUEUED, locked_at=None, error="Recovered after worker timeout")
    )


def claim_next(job_id: int | None = None) -> int | None:
    """Atomically claim one due job (or the given job if still queued) and mark it running. Returns its id."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        _reclaim_stale(db)
        stmt = select(Job).where(Job.status == JobStatus.QUEUED, Job.run_at <= now)
        if job_id is not None:
            stmt = stmt.where(Job.id == job_id)
        job = db.scalars(stmt.order_by(Job.run_at, Job.id).limit(1).with_for_update(skip_locked=True)).first()
        if job is None:
            db.commit()
            return None
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.locked_at = now
        job.started_at = job.started_at or now
        db.commit()
        return job.id


def _finish(job_id: int, error: str | None, result: Any) -> None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.locked_at = None
        if error is None:
            job.status, job.result, job.error, job.finished_at = JobStatus.SUCCEEDED, result, None, now
        elif job.attempts < job.max_attempts:
            backoff = s.job_retry_base_seconds * 2 ** (job.attempts - 1)
            job.status, job.error = JobStatus.QUEUED, error
            job.run_at = now + timedelta(seconds=backoff)
            log.warning("job %s (%s) failed attempt %s; retry in %ss", job.id, job.type, job.attempts, backoff)
        else:
            job.status, job.error, job.finished_at = JobStatus.FAILED, error, now
            log.error("job %s (%s) failed permanently: %s", job.id, job.type, error.splitlines()[-1])
        db.commit()


def run_job(job_id: int | None = None) -> bool:
    """Claim (a specific or the next due) job, execute its handler and record the outcome. False if none."""
    claimed = claim_next(job_id)
    if claimed is None:
        return False
    import app.workers.handlers  # noqa: F401

    with SessionLocal() as db:
        job = db.get(Job, claimed)
        assert job is not None
        job_type, payload = job.type, dict(job.payload or {})
    try:
        handler = _handlers[job_type]
        _finish(claimed, None, handler(payload))
    except Exception:  # noqa: BLE001 - any handler error is recorded and retried
        _finish(claimed, traceback.format_exc(limit=5), None)
    return True


def get_job(job_id: int) -> Job | None:
    with SessionLocal() as db:
        return db.get(Job, job_id)


def purge_finished(older_than_days: int = 7) -> int:
    from sqlalchemy import delete

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    with SessionLocal() as db:
        n = db.execute(
            delete(Job).where(
                or_(Job.status == JobStatus.SUCCEEDED, Job.status == JobStatus.FAILED), Job.finished_at < cutoff
            )
        ).rowcount
        db.commit()
    return int(n or 0)
