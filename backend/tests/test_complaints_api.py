from collections.abc import Callable
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Complaint, Department, User, UserRole, Ward
from app.db.session import SessionLocal
from app.ingestion import pipeline
from tests.conftest import DATA_DIR
from app.ingestion.readers import read_rows

API = "/api/v1"
Headers = Callable[[UserRole], dict[str, str]]


def _load_complaints() -> None:
    name = "complaints_clean_sample.csv"
    with SessionLocal() as db:
        pipeline.ingest_complaints(db, read_rows((DATA_DIR / "samples" / name).read_bytes(), name))


def test_master_data_endpoints(city: None, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ANALYST)
    wards = client.get(f"{API}/wards", headers=h).json()["data"]
    assert len(wards) == 20 and wards[0]["code"] == "W01" and wards[0]["centroid_lat"] and wards[0]["population"] > 0
    gj = client.get(f"{API}/wards/geojson", headers=h).json()["data"]
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == 20
    assert gj["features"][0]["geometry"]["type"] == "MultiPolygon"
    assert client.get(f"{API}/wards/geojson", headers=h).json()["data"] == gj  # served from the cache the second time
    depts = client.get(f"{API}/departments", headers=h).json()["data"]
    assert len(depts) == 10 and {d["category"] for d in depts} >= {"drainage", "roads"}
    assert client.get(f"{API}/wards").status_code == 401


def test_list_filters_pagination_and_sorting(city: None, client: TestClient, auth_headers: Headers) -> None:
    _load_complaints()
    h = auth_headers(UserRole.ANALYST)
    page = client.get(f"{API}/complaints?page_size=20", headers=h).json()["data"]
    assert page["meta"] == {"page": 1, "page_size": 20, "total": 300, "pages": 15} and len(page["items"]) == 20
    dates = [i["submitted_at"] for i in page["items"]]
    assert dates == sorted(dates, reverse=True)
    oldest = client.get(f"{API}/complaints?sort=oldest&page_size=5", headers=h).json()["data"]["items"]
    assert [i["submitted_at"] for i in oldest] == sorted(i["submitted_at"] for i in oldest)

    cat = page["items"][0]["category"]
    only = client.get(f"{API}/complaints?category={cat}&page_size=200", headers=h).json()["data"]
    assert only["meta"]["total"] > 0 and {i["category"] for i in only["items"]} == {cat}
    ward = page["items"][0]["ward_id"]
    by_ward = client.get(f"{API}/complaints?ward_id={ward}&page_size=200", headers=h).json()["data"]
    assert {i["ward_id"] for i in by_ward["items"]} == {ward}
    word = page["items"][0]["description"].split()[0]
    found = client.get(f"{API}/complaints?q={word}&page_size=200", headers=h).json()["data"]
    assert found["meta"]["total"] >= 1 and all(word.lower() in i["description"].lower() for i in found["items"])
    assert client.get(f"{API}/complaints?date_from=2999-01-01", headers=h).json()["data"]["meta"]["total"] == 0
    assert client.get(f"{API}/complaints?status=bogus", headers=h).status_code == 422
    assert client.get(f"{API}/complaints?page_size=500", headers=h).status_code == 422


def test_detail_has_sla_geometry_and_404(city: None, client: TestClient, auth_headers: Headers) -> None:
    _load_complaints()
    h = auth_headers(UserRole.ANALYST)
    item = client.get(f"{API}/complaints?page_size=1", headers=h).json()["data"]["items"][0]
    d = client.get(f"{API}/complaints/{item['id']}", headers=h).json()["data"]
    assert d["ward_name"] and d["department_name"] and d["sla_hours"] and 19.9 < d["latitude"] < 20.1 and 73.7 < d["longitude"] < 73.9
    assert client.get(f"{API}/complaints/999999", headers=h).status_code == 404


def test_pii_masked_except_for_admin_and_ward_officer(city: None, client: TestClient, auth_headers: Headers) -> None:
    _load_complaints()
    with SessionLocal() as db:
        c = db.scalars(select(Complaint).where(Complaint.citizen_name.isnot(None), Complaint.citizen_phone.isnot(None))).first()
        assert c is not None
        real_name, real_phone, cid = c.citizen_name, c.citizen_phone, c.id
    for role, sees in [(UserRole.ANALYST, False), (UserRole.COMMISSIONER, False), (UserRole.DEPARTMENT_HEAD, False),
                       (UserRole.ADMIN, True), (UserRole.WARD_OFFICER, True)]:
        d = client.get(f"{API}/complaints/{cid}", headers=auth_headers(role)).json()["data"]
        assert (d["citizen_name"] == real_name) is sees and (d["citizen_phone"] == real_phone) is sees, role
        if not sees:
            assert "*" in d["citizen_name"] and d["citizen_phone"].startswith("*") and d["citizen_phone"][-4:] == real_phone[-4:]


def test_ward_officer_and_department_head_are_scoped(city: None, client: TestClient, make_user: Callable[..., User]) -> None:
    _load_complaints()
    with SessionLocal() as db:
        ward = db.scalars(select(Complaint.ward_id).limit(1)).first()
        dept = db.scalars(select(Complaint.department_id).limit(1)).first()
    make_user(UserRole.WARD_OFFICER, email="wo@test.org", ward_id=ward)
    make_user(UserRole.DEPARTMENT_HEAD, email="dh@test.org", department_id=dept)
    from tests.conftest import PASSWORD

    def token(email: str) -> dict[str, str]:
        r = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
        return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    wo = client.get(f"{API}/complaints?page_size=200", headers=token("wo@test.org")).json()["data"]
    assert wo["meta"]["total"] > 0 and {i["ward_id"] for i in wo["items"]} == {ward}
    other = client.get(f"{API}/complaints?ward_id={ward + 1 if ward != 20 else 1}", headers=token("wo@test.org")).json()["data"]
    assert other["meta"]["total"] == 0  # cannot widen the scope with a filter
    dh = client.get(f"{API}/complaints?page_size=200", headers=token("dh@test.org")).json()["data"]
    assert {i["department_id"] for i in dh["items"]} == {dept}
    first = wo["items"][0]["id"]
    with SessionLocal() as db:
        foreign = db.scalars(select(Complaint.id).where(Complaint.ward_id != ward).limit(1)).first()
    assert client.get(f"{API}/complaints/{first}", headers=token("wo@test.org")).status_code == 200
    assert client.get(f"{API}/complaints/{foreign}", headers=token("wo@test.org")).status_code == 404


def test_create_complaint_realtime_feed(city: None, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ANALYST)
    body = {"description": "Water pipeline burst and flooding the lane near the temple", "latitude": 20.0, "longitude": 73.79,
            "channel": "mobile_app", "citizen_name": "Test Citizen", "citizen_phone": "9876500001"}
    r = client.post(f"{API}/complaints", json=body, headers=h)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["ward_name"] and d["status"] == "open" and d["external_id"].startswith("API-") and d["language"] == "en"
    assert d["citizen_name"] != "Test Citizen"  # analysts see masked PII even on their own submission response
    assert client.post(f"{API}/complaints", json=body, headers=h).status_code == 409  # same text/ward/day/citizen
    bad = client.post(f"{API}/complaints", json={"description": "Pothole", "channel": "web"}, headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_complaint"
    assert client.post(f"{API}/complaints", json={"description": "x"}, headers=h).status_code == 422  # too short
    far = client.post(f"{API}/complaints", json={**body, "description": "Broken street light on the road", "latitude": 10.0, "longitude": 70.0}, headers=h)
    assert far.status_code == 422 and "outside every ward" in str(far.json()["error"]["details"])
    assert client.post(f"{API}/complaints", json=body).status_code == 401


def test_create_uses_named_ward_and_department(city: None, client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    r = client.post(f"{API}/complaints", headers=h, json={"description": "Drain overflow at the market", "ward": "ward 7",
                                                            "department": "DSD", "submitted_at": datetime.now(timezone.utc).isoformat()})
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    with SessionLocal() as db:
        assert d["ward_name"] == db.scalar(select(Ward.name).where(Ward.code == "W07"))
        assert d["department_name"] == db.scalar(select(Department.name).where(Department.code == "DSD"))
    assert d["latitude"] is None
