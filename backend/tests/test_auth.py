from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import ratelimit
from app.core.config import get_settings
from app.db.models import AuditLog, User, UserRole
from app.db.session import SessionLocal
from tests.conftest import PASSWORD

API = "/api/v1"


def login(client: TestClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


def test_login_success_envelope(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user(UserRole.COMMISSIONER)
    r = login(client, user.email)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["error"] is None
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["user"]["role"] == "commissioner"
    assert "hashed_password" not in r.text


def test_login_wrong_password_and_unknown_user(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user()
    for email, pw in [(user.email, "wrong-pass1"), ("nobody@test.org", PASSWORD)]:
        r = login(client, email, pw)
        assert r.status_code == 401
        assert r.json() == {
            "success": False,
            "data": None,
            "error": {"code": "invalid_credentials", "message": "Incorrect email or password", "details": None},
        }


def test_inactive_user_cannot_login(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user(is_active=False)
    assert login(client, user.email).status_code == 401


def test_me_requires_token(client: TestClient) -> None:
    r = client.get(f"{API}/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"
    r = client.get(f"{API}/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"


def test_me_and_refresh_flow(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user(UserRole.WARD_OFFICER)
    data = login(client, user.email).json()["data"]
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.json()["data"]["email"] == user.email

    # a refresh token can't be used as an access token, and vice versa
    bad = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {data['refresh_token']}"})
    assert bad.status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": data["access_token"]}).status_code == 401

    r = client.post(f"{API}/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 200 and r.json()["data"]["access_token"]


def test_deactivated_user_token_rejected(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user()
    token = login(client, user.email).json()["data"]["access_token"]
    with SessionLocal() as db:
        db.get(User, user.id).is_active = False  # type: ignore[union-attr]
        db.commit()
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_login_validation_error_envelope(client: TestClient) -> None:
    r = client.post(f"{API}/auth/login", json={"email": "not-an-email"})
    body = r.json()
    assert r.status_code == 422 and body["success"] is False
    assert body["error"]["code"] == "validation_error"
    assert {d["field"] for d in body["error"]["details"]} == {"email", "password"}


def test_login_and_failure_are_audited(client: TestClient, make_user: Callable[..., User]) -> None:
    user = make_user()
    login(client, user.email, "wrong-pass1")
    login(client, user.email)
    with SessionLocal() as db:
        actions = [a.action for a in db.query(AuditLog).order_by(AuditLog.id)]
    assert actions == ["login_failed", "login"]


def test_login_rate_limited(
    client: TestClient, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    monkeypatch.setattr(s, "login_rate_limit_per_minute", 3)
    ratelimit.reset()
    monkeypatch.setattr(ratelimit, "_now", lambda: 2_000_000.0)  # frozen: slow network must not cross a window
    user = make_user()
    codes = [login(client, user.email, "wrong-pass1").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    assert login(client, user.email).json()["error"]["code"] == "rate_limited"
