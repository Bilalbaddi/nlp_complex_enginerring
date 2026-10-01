from fastapi.testclient import TestClient


def test_health_reports_extensions_and_demo_mode(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["database"] == "ok" and d["postgis"] is True and d["pgvector"] is True
    assert d["storage"] in ("ok", "not_configured", "degraded")  # real storage checked in test_storage.py
    assert d["jobs_mode"] == "worker"  # tests run with JOBS_INLINE=false
    assert d["llm_demo_mode"] is True and d["llm_provider"] == "mock"


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["success"] is False and r.json()["data"] is None


def test_openapi_docs_available(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert "/api/v1/auth/login" in client.get("/openapi.json").json()["paths"]
