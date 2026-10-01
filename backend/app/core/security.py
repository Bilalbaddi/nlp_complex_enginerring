import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]

_BCRYPT_MAX_BYTES = 72


class TokenError(Exception):
    """Raised when a token is invalid, expired or of the wrong type."""


def _pw_bytes(password: str) -> bytes:
    # bcrypt only considers the first 72 bytes; truncate explicitly (bcrypt>=5 raises otherwise).
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(password), hashed.encode("ascii"))
    except ValueError:
        return False


def create_token(subject: int, role: str, token_type: TokenType) -> tuple[str, int]:
    """Return (encoded token, lifetime in seconds)."""
    s = get_settings()
    lifetime = (
        timedelta(minutes=s.access_token_expire_minutes)
        if token_type == "access"
        else timedelta(days=s.refresh_token_expire_days)
    )
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), int(lifetime.total_seconds())


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    s = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token, s.jwt_secret, algorithms=[s.jwt_algorithm], options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise TokenError(f"Expected {expected_type} token")
    return payload
