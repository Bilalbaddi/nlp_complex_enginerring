"""Postgres-backed cache with TTL (table `cache_entries`). Used for analytics results and LLM outputs.

Each call uses its own short session so caching never interferes with a request's transaction.
Values must be JSON-serialisable.
"""

import copy
import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.models import CacheEntry
from app.db.session import AutocommitSession as SessionLocal  # single statements: 1 round trip each

MAX_KEY_LEN = 255

# Level-1 in-process cache for the heavy, read-mostly analytics namespaces. Each Postgres cache lookup costs a network
# round trip to the remote database (~0.3 s), and one endpoint can do a dozen. Entries live at most L1_SECONDS, so another
# process's invalidation is seen within that time. Off in tests (they reset data between tests).
L1_PREFIXES = ("analytics:", "forecast:", "gis:")
L1_SECONDS = 300
_l1: dict[str, tuple[float, Any]] = {}


def _use_l1(key: str) -> bool:
    return key.startswith(L1_PREFIXES) and get_settings().env != "test"


def make_key(namespace: str, *parts: Any) -> str:
    """Stable key: `namespace:<sha256 of the JSON-encoded parts>` (e.g. prompt + model for LLM outputs)."""
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"[:MAX_KEY_LEN]


def get(key: str) -> Any | None:
    """Return the cached value, or None when missing or expired."""
    with SessionLocal() as db:
        row = db.execute(
            select(CacheEntry.value).where(CacheEntry.key == key, CacheEntry.expires_at > datetime.now(timezone.utc))
        ).first()
    return row[0] if row else None


def set(key: str, value: Any, ttl_seconds: int) -> None:  # noqa: A001 - mirrors the get/set/delete API
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    stmt = insert(CacheEntry).values(key=key, value=value, expires_at=expires)
    stmt = stmt.on_conflict_do_update(
        index_elements=[CacheEntry.key],
        set_={"value": stmt.excluded.value, "expires_at": stmt.excluded.expires_at, "created_at": datetime.now(timezone.utc)},
    )
    with SessionLocal() as db:
        db.execute(stmt)
        db.commit()


def prefetch(keys: list[str]) -> None:
    """Load many cache entries into the in-process layer with ONE query (instead of one round trip per key)."""
    if get_settings().env == "test":
        return
    now = time.monotonic()
    missing = [k for k in keys if k.startswith(L1_PREFIXES) and not ((hit := _l1.get(k)) and now - hit[0] < L1_SECONDS)]
    if not missing:
        return
    with SessionLocal() as db:
        rows = db.execute(
            select(CacheEntry.key, CacheEntry.value).where(CacheEntry.key.in_(missing), CacheEntry.expires_at > datetime.now(timezone.utc))
        ).all()
    for k, v in rows:
        _l1[k] = (now, v)


def delete_key(key: str) -> bool:
    _l1.pop(key, None)
    with SessionLocal() as db:
        n = db.execute(delete(CacheEntry).where(CacheEntry.key == key)).rowcount
        db.commit()
    return bool(n)


def delete_prefix(prefix: str) -> int:
    """Invalidate a whole namespace, e.g. after new data is ingested."""
    for k in [k for k in _l1 if k.startswith(prefix)]:
        del _l1[k]
    with SessionLocal() as db:
        n = db.execute(delete(CacheEntry).where(CacheEntry.key.like(prefix.replace("%", r"\%") + "%"))).rowcount
        db.commit()
    return int(n or 0)


def get_or_set(key: str, ttl_seconds: int, factory: Callable[[], Any]) -> tuple[Any, bool]:
    """Return (value, cache_hit). Computes and stores via `factory` on a miss."""
    l1 = _use_l1(key)
    if l1:
        hit = _l1.get(key)
        if hit and time.monotonic() - hit[0] < L1_SECONDS:
            return copy.deepcopy(hit[1]), True
    cached = get(key)
    if cached is not None:
        if l1:
            _l1[key] = (time.monotonic(), cached)
        return copy.deepcopy(cached) if l1 else cached, True
    value = factory()
    set(key, value, ttl_seconds)
    if l1:
        _l1[key] = (time.monotonic(), copy.deepcopy(value))
    return value, False


def cleanup_expired() -> int:
    """Delete expired rows; returns how many were removed."""
    with SessionLocal() as db:
        n = db.execute(delete(CacheEntry).where(CacheEntry.expires_at <= datetime.now(timezone.utc))).rowcount
        db.commit()
    return int(n or 0)
