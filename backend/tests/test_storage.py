"""Storage tests hit the REAL Supabase Storage API (private buckets), using a throwaway path in the reports bucket."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.services import storage

needs_storage = pytest.mark.skipif(not get_settings().storage_configured, reason="Supabase Storage not configured")


def test_safe_name_and_paths() -> None:
    assert storage.safe_name("../../etc/pass wd.csv") == "pass_wd.csv"
    assert storage.safe_name("C:\\dir\\my file (1).pdf") == "my_file_1_.pdf"
    assert storage.safe_name("...") == "file"
    p = storage.new_path("complaints/", "a b.csv")
    assert p.startswith("complaints/") and p.endswith("/a_b.csv") and p != storage.new_path("complaints", "a b.csv")


@needs_storage
def test_buckets_are_private_and_health_ok(client: TestClient) -> None:
    assert set(storage.ensure_buckets()) == set(get_settings().storage_buckets)
    d = client.get("/api/v1/health").json()["data"]
    assert d["storage"] == "ok" and d["storage_public_buckets"] == [] and d["storage_missing_buckets"] == []


@needs_storage
def test_upload_download_signed_url_delete() -> None:
    bucket = get_settings().storage_bucket_reports
    path = storage.new_path("pytest", "hello.txt")
    try:
        storage.upload(bucket, path, b"hello supabase", "text/plain")
        assert storage.exists(bucket, path)
        assert storage.download(bucket, path) == b"hello supabase"
        url = storage.signed_url(bucket, path, 60)
        assert url.startswith("http") and "token=" in url
    finally:
        storage.delete(bucket, [path])
    assert not storage.exists(bucket, path)


def test_every_table_has_row_level_security() -> None:
    """Supabase exposes public tables over its REST API; RLS (with no policies) keeps them backend-only."""
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND c.relname <> 'spatial_ref_sys'"
            )
        ).all()
    assert rows and [r[0] for r in rows if not r[1]] == []
