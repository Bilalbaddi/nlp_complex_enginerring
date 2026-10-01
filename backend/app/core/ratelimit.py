"""Fixed-window rate limiter backed by a Postgres table (`rate_limits`), one atomic upsert per hit."""

import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.models import RateLimit
from app.db.session import AutocommitSession as SessionLocal  # single upsert: 1 round trip


def _now() -> float:
    """Wall clock; a separate function so tests can freeze time without touching time.time globally."""
    return time.time()


def hit(key: str, limit: int, window_s: int) -> bool:
    """Register a request for `key`; return True while the count in the current window is within `limit`."""
    now = int(_now())
    window_start = datetime.fromtimestamp(now - now % window_s, tz=timezone.utc)
    stmt = (
        insert(RateLimit)
        .values(key=key[:128], window_start=window_start, count=1)
        .on_conflict_do_update(
            index_elements=[RateLimit.key, RateLimit.window_start],
            set_={"count": RateLimit.count + 1},
        )
        .returning(RateLimit.count)
    )
    with SessionLocal() as db:
        count = db.execute(stmt).scalar_one()
        db.commit()
    return int(count) <= limit


def cleanup_expired(older_than_s: int = 3600) -> int:
    """Delete counters whose window ended more than `older_than_s` seconds ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_s)
    with SessionLocal() as db:
        n = db.execute(delete(RateLimit).where(RateLimit.window_start < cutoff)).rowcount
        db.commit()
    return int(n or 0)


def reset() -> None:
    with SessionLocal() as db:
        db.execute(delete(RateLimit))
        db.commit()


def rate_limit(name: str, limit_getter: Callable[[], int], window_s: int = 60) -> Callable[..., None]:
    """Dependency factory. Keys on the authenticated user id if present, else the client IP."""

    def dependency(request: Request) -> None:
        if not get_settings().rate_limit_enabled:
            return
        ident = getattr(request.state, "user_id", None) or (
            request.client.host if request.client else "unknown"
        )
        if not hit(f"{name}:{ident}", limit_getter(), window_s):
            raise ApiError(429, "rate_limited", "Too many requests, please slow down")

    return dependency


def login_rate_limit() -> Callable[..., None]:
    return rate_limit("login", lambda: get_settings().login_rate_limit_per_minute)

