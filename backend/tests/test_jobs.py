import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.models import Job, JobStatus, UserRole
from app.db.session import SessionLocal
from app.services import jobs

API = "/api/v1"
Headers = Callable[[UserRole], dict[str, str]]


def _job(job_id: int) -> Job:
    with SessionLocal() as db:
        j = db.get(Job, job_id)
        assert j is not None
        return j


def test_run_job_success() -> None:
    job_id = jobs.enqueue("system.ping")
    assert _job(job_id).status == JobStatus.QUEUED
    assert jobs.run_job() is True
    j = _job(job_id)
    assert j.status == JobStatus.SUCCEEDED and j.result == "pong" and j.attempts == 1 and j.finished_at
    assert jobs.run_job() is False  # queue empty


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValueError):
        jobs.enqueue("does.not.exist")


def test_retry_with_exponential_backoff_then_permanent_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "job_retry_base_seconds", 10)
    job_id = jobs.enqueue("system.fail", {"message": "boom"}, max_attempts=3)

    assert jobs.run_job(job_id) is True  # attempt 1 fails -> retry in 10s
    j = _job(job_id)
    assert j.status == JobStatus.QUEUED and j.attempts == 1 and "boom" in (j.error or "")
    assert 2 < (j.run_at - datetime.now(timezone.utc)).total_seconds() <= 10  # generous: real network latency
    assert jobs.run_job(job_id) is False  # not due yet, must not run early

    for attempt, wait in ((2, 20), (3, None)):
        with SessionLocal() as db:  # fast-forward time
            db.execute(update(Job).where(Job.id == job_id).values(run_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            db.commit()
        assert jobs.run_job(job_id) is True
        j = _job(job_id)
        assert j.attempts == attempt
        if wait:
            assert j.status == JobStatus.QUEUED
            assert wait - 8 < (j.run_at - datetime.now(timezone.utc)).total_seconds() <= wait  # 10s -> 20s: doubles
    assert j.status == JobStatus.FAILED and j.finished_at and "boom" in (j.error or "")


def test_skip_locked_lets_second_worker_take_other_job() -> None:
    first = jobs.enqueue("system.ping")
    second = jobs.enqueue("system.ping")
    with SessionLocal() as holder:  # another worker holds a row lock on the first job, mid-claim
        locked = holder.scalars(
            select(Job).where(Job.status == JobStatus.QUEUED).order_by(Job.id).limit(1).with_for_update(skip_locked=True)
        ).one()
        assert locked.id == first
        assert jobs.claim_next() == second  # skipped the locked row instead of blocking or double-claiming
        assert jobs.claim_next() is None
        holder.rollback()
    assert jobs.claim_next() == first


def test_stale_running_job_is_reclaimed(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = jobs.enqueue("system.ping")
    assert jobs.claim_next(job_id) == job_id  # "worker" claims it, then dies
    assert jobs.claim_next() is None
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.id == job_id).values(locked_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        db.commit()
    assert jobs.claim_next() == job_id


def test_cleanup_job_runs() -> None:
    job_id = jobs.enqueue("system.cleanup")
    jobs.run_job(job_id)
    j = _job(job_id)
    assert j.status == JobStatus.SUCCEEDED and set(j.result) == {"cache_entries", "rate_limits", "jobs"}


def test_inline_mode_runs_without_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "jobs_inline", True)
    job_id = jobs.enqueue("system.ping")
    for _ in range(50):
        if _job(job_id).status == JobStatus.SUCCEEDED:
            break
        time.sleep(0.1)
    assert _job(job_id).status == JobStatus.SUCCEEDED


def test_jobs_api_admin_only_and_flow(client: TestClient, auth_headers: Headers) -> None:
    assert client.get(f"{API}/jobs").status_code == 401
    assert client.post(f"{API}/jobs", json={"type": "system.ping"}, headers=auth_headers(UserRole.ANALYST)).status_code == 403
    admin = auth_headers(UserRole.ADMIN)
    assert "system.ping" in client.get(f"{API}/jobs/types", headers=admin).json()["data"]
    assert client.post(f"{API}/jobs", json={"type": "nope"}, headers=admin).json()["error"]["code"] == "unknown_job_type"
    r = client.post(f"{API}/jobs", json={"type": "system.ping"}, headers=admin)
    assert r.status_code == 202 and r.json()["data"]["status"] == "queued"
    jid = r.json()["data"]["id"]
    jobs.run_job(jid)
    assert client.get(f"{API}/jobs/{jid}", headers=admin).json()["data"]["result"] == "pong"
    assert client.get(f"{API}/jobs?status=succeeded", headers=admin).json()["data"]["meta"]["total"] == 1
    assert client.get(f"{API}/jobs/9999", headers=admin).status_code == 404
