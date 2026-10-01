from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.db.models import AuditLog, UserRole
from app.db.session import SessionLocal
from tests.conftest import PASSWORD

API = "/api/v1"
NEW_USER = {
    "email": "New.Person@test.org",
    "full_name": "New Person",
    "password": "Str0ngpass1",
    "role": "ward_officer",
}
Headers = Callable[[UserRole], dict[str, str]]


@pytest.mark.parametrize(
    "role", [UserRole.COMMISSIONER, UserRole.DEPARTMENT_HEAD, UserRole.WARD_OFFICER, UserRole.ANALYST]
)
def test_non_admin_forbidden_on_admin_endpoints(client: TestClient, auth_headers: Headers, role: UserRole) -> None:
    h = auth_headers(role)
    calls = [
        client.get(f"{API}/users", headers=h),
        client.post(f"{API}/users", headers=h, json=NEW_USER),
        client.get(f"{API}/audit-logs", headers=h),
        client.patch(f"{API}/users/1", headers=h, json={"is_active": False}),
    ]
    for r in calls:
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "forbidden"


def test_admin_endpoints_require_auth(client: TestClient) -> None:
    assert client.get(f"{API}/users").status_code == 401
    assert client.get(f"{API}/audit-logs").status_code == 401


def test_admin_creates_user_and_new_user_can_login(client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    r = client.post(f"{API}/users", json=NEW_USER, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["email"] == "new.person@test.org"  # normalised to lowercase
    login = client.post(
        f"{API}/auth/login", json={"email": "new.person@test.org", "password": NEW_USER["password"]}
    )
    assert login.status_code == 200 and login.json()["data"]["user"]["role"] == "ward_officer"


def test_duplicate_email_conflict(client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    assert client.post(f"{API}/users", json=NEW_USER, headers=h).status_code == 201
    r = client.post(f"{API}/users", json=NEW_USER, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_exists"


@pytest.mark.parametrize("pw", ["short1", "onlyletters", "12345678"])
def test_weak_password_rejected(client: TestClient, auth_headers: Headers, pw: str) -> None:
    r = client.post(f"{API}/users", json={**NEW_USER, "password": pw}, headers=auth_headers(UserRole.ADMIN))
    assert r.status_code == 422


def test_invalid_role_and_reference_rejected(client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    assert client.post(f"{API}/users", json={**NEW_USER, "role": "god"}, headers=h).status_code == 422
    r = client.post(f"{API}/users", json={**NEW_USER, "ward_id": 999}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_reference"


def test_list_users_pagination_and_filter(client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    for i in range(4):
        client.post(f"{API}/users", json={**NEW_USER, "email": f"u{i}@test.org"}, headers=h)
    r = client.get(f"{API}/users?page=1&page_size=2", headers=h).json()["data"]
    assert len(r["items"]) == 2 and r["meta"] == {"page": 1, "page_size": 2, "total": 5, "pages": 3}
    only = client.get(f"{API}/users?role=ward_officer", headers=h).json()["data"]
    assert only["meta"]["total"] == 4
    assert client.get(f"{API}/users?page_size=1000", headers=h).status_code == 422


def test_update_user_and_self_lockout_guard(client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    uid = client.post(f"{API}/users", json=NEW_USER, headers=h).json()["data"]["id"]
    r = client.patch(f"{API}/users/{uid}", json={"role": "analyst", "is_active": False}, headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "analyst" and r.json()["data"]["is_active"] is False
    assert client.patch(f"{API}/users/9999", json={"is_active": False}, headers=h).status_code == 404
    me = client.get(f"{API}/auth/me", headers=h).json()["data"]["id"]
    r = client.patch(f"{API}/users/{me}", json={"is_active": False}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "self_lockout"


def test_password_reset_by_admin(client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    uid = client.post(f"{API}/users", json=NEW_USER, headers=h).json()["data"]["id"]
    client.patch(f"{API}/users/{uid}", json={"password": "Brandn3wpass"}, headers=h)
    good = client.post(f"{API}/auth/login", json={"email": "new.person@test.org", "password": "Brandn3wpass"})
    old = client.post(
        f"{API}/auth/login", json={"email": "new.person@test.org", "password": NEW_USER["password"]}
    )
    assert good.status_code == 200 and old.status_code == 401


def test_sensitive_actions_audited_without_secrets(client: TestClient, auth_headers: Headers) -> None:
    h = auth_headers(UserRole.ADMIN)
    uid = client.post(f"{API}/users", json=NEW_USER, headers=h).json()["data"]["id"]
    client.patch(f"{API}/users/{uid}", json={"password": "Brandn3wpass"}, headers=h)
    logs = client.get(f"{API}/audit-logs?action=user_update", headers=h).json()["data"]["items"]
    assert len(logs) == 1 and logs[0]["detail"] == {"fields": ["password"]}
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action="user_create").count() == 1
    everything = str(client.get(f"{API}/audit-logs", headers=h).json())
    assert PASSWORD not in everything and "Brandn3wpass" not in everything
