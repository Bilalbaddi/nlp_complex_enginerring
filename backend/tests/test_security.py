import time

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import TokenError, create_token, decode_token, hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    h = hash_password("Secret123")
    assert h != "Secret123" and h.startswith("$2")
    assert verify_password("Secret123", h)
    assert not verify_password("secret123", h)


def test_verify_password_bad_hash_is_false() -> None:
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_long_password_does_not_crash() -> None:
    assert verify_password("a" * 200, hash_password("a" * 200))


def test_token_roundtrip_and_type_check() -> None:
    token, ttl = create_token(7, "admin", "access")
    assert ttl == get_settings().access_token_expire_minutes * 60
    payload = decode_token(token, "access")
    assert payload["sub"] == "7" and payload["role"] == "admin"
    with pytest.raises(TokenError):
        decode_token(token, "refresh")


def test_tampered_and_expired_tokens_rejected() -> None:
    s = get_settings()
    token, _ = create_token(1, "admin", "access")
    with pytest.raises(TokenError):
        decode_token(token[:-2] + "xx", "access")
    expired = jwt.encode(
        {"sub": "1", "type": "access", "exp": int(time.time()) - 10}, s.jwt_secret, algorithm=s.jwt_algorithm
    )
    with pytest.raises(TokenError):
        decode_token(expired, "access")
