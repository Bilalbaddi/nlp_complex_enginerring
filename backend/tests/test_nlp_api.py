"""NLP endpoints, registry and enrichment against the test schema, with a small predictor injected (no Storage, no HF)."""

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Complaint, Department, ModelRun, UserRole
from app.db.session import SessionLocal
from app.ml import enrich, registry
from app.ml import breach as breach_ml
from app.ml.predict import Predictor
from app.services import jobs
from tests.test_ml import dup_model, rows

API = "/api/v1"
Headers = Callable[[UserRole], dict[str, str]]


@pytest.fixture(scope="module")
def predictor_parts() -> tuple[Any, Any, Any]:
    m_dup, _, cat = dup_model()
    breach_model, _ = breach_ml.train_breach(rows(3000))
    return cat, m_dup, breach_model


@pytest.fixture
def predictor(city: None, predictor_parts: tuple[Any, Any, Any], monkeypatch: pytest.MonkeyPatch) -> Iterator[Predictor]:
    cat, m_dup, breach_model = predictor_parts
    with SessionLocal() as db:
        depts = {d.category: d.id for d in db.scalars(select(Department)) if d.category}
    p = Predictor(category=cat, breach=breach_model, duplicates=m_dup, duplicates_text=m_dup, dept_by_category=depts,
                  ward_names=["Old City", "Shivaji Nagar"])
    monkeypatch.setattr(enrich, "_cached", (time.monotonic() + 10_000, p))
    yield p
    enrich.reset_predictor_cache()


def test_analyze_endpoint_explains_its_answer(predictor: Predictor, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.COMMISSIONER)
    r = client.post(f"{API}/nlp/analyze", headers=h, json={
        "text": "Electric wire fallen on the road near Ganesh Mandir, sparking. Children are at risk since 3 days."})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["category"] == "electricity" and d["department"] == "Electricity Department" and d["language"] == "en"
    assert d["urgency_level"] in ("high", "critical") and any("health/safety" in x for x in d["urgency_reasons"])
    assert d["entities"]["landmarks"] == ["Ganesh Mandir"] and len(d["top_categories"]) == 3 and d["sentiment"] < 0
    hi = client.post(f"{API}/nlp/analyze", headers=h, json={"text": "नल से गंदा और बदबूदार पानी आ रहा है"}).json()["data"]
    assert hi["category"] == "water_supply" and hi["language"] == "hi"
    assert client.post(f"{API}/nlp/analyze", headers=h, json={"text": "x"}).status_code == 422
    assert client.post(f"{API}/nlp/analyze", json={"text": "street light off"}).status_code == 401


def test_analyze_without_models_is_a_clear_503(city: None, client: TestClient, auth_headers: Headers) -> None:
    enrich.reset_predictor_cache()
    r = client.post(f"{API}/nlp/analyze", headers=auth_headers(UserRole.ANALYST), json={"text": "street light off for days"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "models_not_trained"


def test_registry_roundtrip_and_models_endpoint(city: None, predictor_parts: tuple[Any, Any, Any], client: TestClient,
                                                auth_headers: Headers, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "supabase_url", "")  # keep artifacts local: no Storage traffic in this test
    cat, *_ = predictor_parts
    with SessionLocal() as db:
        a = registry.save_run(db, name="category_tfidf_lr", task="category", metrics={"accuracy": 0.9}, artifact=cat, n_train=10, activate=True)
        b = registry.save_run(db, name="category_tfidf_lr", task="category", metrics={"accuracy": 0.95}, artifact=cat, activate=True)
        run, model = registry.load_active(db, "category_tfidf_lr")  # type: ignore[misc]
        assert run.id == b.id and model.classes == cat.classes
        assert not db.get(ModelRun, a.id).is_active  # type: ignore[union-attr]
    assert client.get(f"{API}/nlp/models", headers=auth_headers(UserRole.WARD_OFFICER)).status_code == 403
    listing = client.get(f"{API}/nlp/models?active_only=true", headers=auth_headers(UserRole.ANALYST)).json()["data"]
    assert [m["id"] for m in listing] == [b.id] and listing[0]["metrics"]["accuracy"] == 0.95


def _create(client: TestClient, h: dict[str, str], text: str, lat: float = 20.0, lon: float = 73.79, **kw: Any) -> dict[str, Any]:
    r = client.post(f"{API}/complaints", headers=h, json={"description": text, "latitude": lat, "longitude": lon, **kw})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_new_complaint_is_enriched_by_a_job(predictor: Predictor, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    c = _create(client, h, "Drain is overflowing onto the road near Ganesh Mandir since 4 days. Sewage smell, children affected.")
    assert c["predicted_category"] is None and c["needs_review"] is False  # the API returns before enrichment runs
    assert jobs.run_job() is True  # nlp.enrich_complaint
    d = client.get(f"{API}/complaints/{c['id']}", headers=h).json()["data"]
    assert d["predicted_category"] == "drainage" and d["category_confidence"] > 0.5
    assert d["urgency_score"] > 20 and d["sentiment_score"] < 0 and d["entities"]["landmarks"] == ["Ganesh Mandir"]
    assert d["predicted_department_id"] is not None and d["language"] == "en"


def test_near_duplicate_is_linked_to_the_original(predictor: Predictor, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    first = _create(client, h, "Garbage has not been collected for several days near Hanuman Temple", channel="web")
    second = _create(client, h, "Garbage not collected for many days near Hanuman Temple. Please take immediate action.", 20.00002, 73.79002,
                     channel="whatsapp")
    other = _create(client, h, "Street light not working on Station Road", 20.0, 73.79)
    while jobs.run_job():
        pass
    g = lambda i: client.get(f"{API}/complaints/{i}", headers=h).json()["data"]  # noqa: E731
    assert g(second["id"])["duplicate_of_id"] == first["id"]
    assert g(first["id"])["duplicate_of_id"] is None and g(other["id"])["duplicate_of_id"] is None


def test_review_queue_and_human_decision(predictor: Predictor, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    c = _create(client, h, "Something is wrong here near the school, please check")  # vague: low confidence expected
    jobs.run_job()
    d = client.get(f"{API}/complaints/{c['id']}", headers=h).json()["data"]
    assert d["needs_review"] is True and "low confidence" in d["review_reason"]
    assert client.get(f"{API}/complaints?needs_review=true", headers=h).json()["data"]["meta"]["total"] == 1
    assert client.get(f"{API}/nlp/review/stats", headers=h).json()["data"]["pending"] == 1

    assert client.post(f"{API}/nlp/review/{c['id']}", headers=auth_headers(UserRole.COMMISSIONER), json={"category": "roads"}).status_code == 403
    assert client.post(f"{API}/nlp/review/{c['id']}", headers=h, json={"category": "astrology"}).status_code == 422
    assert client.post(f"{API}/nlp/review/999999", headers=h, json={"category": "roads"}).status_code == 404
    r = client.post(f"{API}/nlp/review/{c['id']}", headers=h, json={"category": "roads"})
    assert r.status_code == 200
    d = client.get(f"{API}/complaints/{c['id']}", headers=h).json()["data"]
    assert d["category"] == "roads" and d["needs_review"] is False and d["reviewed_at"] and d["department_name"] == "Roads & Maintenance Department"
    # a later re-prediction must not undo the human decision
    enrich.enrich_complaint(c["id"])
    assert client.get(f"{API}/complaints/{c['id']}", headers=h).json()["data"]["needs_review"] is False
    assert client.get(f"{API}/nlp/review/stats", headers=h).json()["data"]["reviewed"] == 1
    with SessionLocal() as db:
        from app.db.models import AuditLog

        assert db.scalar(select(AuditLog).where(AuditLog.action == "complaint_review")) is not None


def test_enrichment_job_types_are_registered() -> None:
    assert {"nlp.enrich_complaint", "nlp.predict_all", "embeddings.backfill"} <= set(jobs.registered_types())


def test_enrich_complaint_without_models_is_a_noop(city: None, client: TestClient, auth_headers: Headers) -> None:
    enrich.reset_predictor_cache()
    c = _create(client, auth_headers(UserRole.ADMIN), "Pothole on the main road near the market")
    assert enrich.enrich_complaint(c["id"]) is None
    with SessionLocal() as db:
        assert db.get(Complaint, c["id"]).predicted_category is None  # type: ignore[union-attr]
