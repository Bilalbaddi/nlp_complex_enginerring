"""Upload endpoints use real Supabase Storage (throwaway objects) and the job queue (run explicitly in tests)."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Complaint, Document, UserRole
from app.db.session import SessionLocal
from app.services import jobs, storage
from tests.conftest import DATA_DIR

API = "/api/v1"
Headers = Callable[[UserRole], dict[str, str]]
needs_storage = pytest.mark.skipif(not get_settings().storage_configured, reason="Supabase Storage not configured")


def _upload(client: TestClient, path: str, name: str, h: dict[str, str], **extra: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    with open(DATA_DIR / path, "rb") as f:
        r = client.post(f"{API}{extra.pop('url')}", headers=h, files={"file": (name, f.read())}, data=extra)
    return r.status_code, r.json()


def test_upload_permissions_and_validation(city: None, client: TestClient, auth_headers: Headers) -> None:
    csv = {"file": ("x.csv", b"description,submitted_at\nx,2025-01-01\n")}
    assert client.post(f"{API}/ingest/complaints", files=csv).status_code == 401
    assert client.post(f"{API}/ingest/complaints", files=csv, headers=auth_headers(UserRole.WARD_OFFICER)).status_code == 403
    assert client.post(f"{API}/documents", files=csv, data={"title": "T T T"}, headers=auth_headers(UserRole.COMMISSIONER)).status_code == 403
    h = auth_headers(UserRole.ANALYST)
    assert client.post(f"{API}/ingest/complaints", files={"file": ("x.exe", b"abc")}, headers=h).status_code == 415
    assert client.post(f"{API}/ingest/complaints", files={"file": ("x.csv", b"")}, headers=h).status_code == 422
    assert client.post(f"{API}/ingest/weird", files=csv, headers=h).status_code == 422
    assert client.post(f"{API}/documents", files={"file": ("x.pdf", b"abc")}, data={"title": "ab"}, headers=h).status_code == 422  # title too short
    assert client.get(f"{API}/ingestion-logs", headers=auth_headers(UserRole.DEPARTMENT_HEAD)).status_code == 403


@needs_storage
def test_complaint_file_upload_end_to_end(city: None, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ANALYST)
    status, body = _upload(client, "samples/complaints_messy_sample.csv", "messy.csv", h, url="/ingest/complaints")
    assert status == 202 and body["data"]["status"] == "queued" and body["data"]["source_type"] == "complaints"
    log_id = body["data"]["id"]
    assert jobs.run_job() is True  # the ingest.file job
    log = client.get(f"{API}/ingestion-logs/{log_id}", headers=h).json()["data"]
    assert log["status"] == "partial" and log["rows_rejected"] == 15 and log["rows_duplicate"] == 6 and log["rows_ok"] > 150
    assert log["errors"]["rejected_rows"][0]["row"] >= 2
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Complaint)) == log["rows_ok"]
    listed = client.get(f"{API}/ingestion-logs?source_type=complaints", headers=h).json()["data"]
    assert listed["meta"]["total"] == 1
    # the raw upload is retained in a private bucket for audit
    with SessionLocal() as db:
        from app.db.models import IngestionLog

        entry = db.get(IngestionLog, log_id)
        assert entry and storage.exists(entry.storage_bucket or "", entry.storage_path or "")
        storage.delete(entry.storage_bucket or "", [entry.storage_path or ""])


@needs_storage
def test_unreadable_upload_fails_permanently_without_retry(city: None, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ANALYST)
    r = client.post(f"{API}/ingest/complaints", headers=h, files={"file": ("broken.json", b"{not json")})
    log_id = r.json()["data"]["id"]
    assert jobs.run_job() is True
    log = client.get(f"{API}/ingestion-logs/{log_id}", headers=h).json()["data"]
    assert log["status"] == "failed" and "Invalid JSON" in log["errors"]["error"]
    assert jobs.run_job() is False  # not requeued: a corrupt file cannot succeed on retry
    with SessionLocal() as db:
        from app.db.models import IngestionLog

        e = db.get(IngestionLog, log_id)
        storage.delete(e.storage_bucket or "", [e.storage_path or ""])  # type: ignore[union-attr]


@needs_storage
def test_document_upload_download_duplicate_and_delete(city: None, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ANALYST)
    status, body = _upload(client, "policies/water_supply_service_charter.pdf", "charter.pdf", h,
                           url="/documents", title="Water Supply Charter", doc_type="policy")
    assert status == 202
    assert jobs.run_job() is True
    log = client.get(f"{API}/ingestion-logs/{body['data']['id']}", headers=h).json()["data"]
    assert log["status"] == "success" and log["errors"]["created"] is True
    docs = client.get(f"{API}/documents", headers=h).json()["data"]
    assert docs["meta"]["total"] == 1 and docs["items"][0]["title"] == "Water Supply Charter" and docs["items"][0]["num_chunks"] >= 1
    doc_id = docs["items"][0]["id"]

    url = client.get(f"{API}/documents/{doc_id}/download", headers=auth_headers(UserRole.COMMISSIONER)).json()["data"]["url"]
    assert url.startswith("http") and "token=" in url

    # identical content again -> reported as a duplicate, no second document, redundant upload removed
    _, again = _upload(client, "policies/water_supply_service_charter.pdf", "charter-copy.pdf", h, url="/documents", title="Duplicate Copy")
    assert jobs.run_job() is True
    dup = client.get(f"{API}/ingestion-logs/{again['data']['id']}", headers=h).json()["data"]
    assert dup["status"] == "partial" and dup["rows_duplicate"] == 1
    assert client.get(f"{API}/documents", headers=h).json()["data"]["meta"]["total"] == 1

    assert client.delete(f"{API}/documents/{doc_id}", headers=h).status_code == 403  # admin only
    assert client.delete(f"{API}/documents/{doc_id}", headers=auth_headers(UserRole.ADMIN)).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 0
    assert client.get(f"{API}/documents/{doc_id}/download", headers=h).status_code == 404
