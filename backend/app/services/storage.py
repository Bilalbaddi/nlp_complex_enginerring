"""Supabase Storage wrapper (private buckets only). Uses the service-role key: BACKEND ONLY.

Files are never served publicly; hand out short-lived signed URLs (`signed_url`) or stream via the API.
"""

import logging
import re
import uuid
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from app.core.config import get_settings
from app.core.errors import ApiError

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_buckets_ready = False


@lru_cache
def _client() -> Client:
    s = get_settings()
    if not s.storage_configured:
        raise ApiError(503, "storage_not_configured", "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set")
    return create_client(s.supabase_url, s.supabase_service_role_key)


def safe_name(filename: str) -> str:
    """Sanitise a user-supplied filename for use in an object path."""
    name = _SAFE.sub("_", filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip("._") or "file"
    return name[:120]


def new_path(prefix: str, filename: str) -> str:
    """Unique object path like `complaints/3f9c…/data.csv`."""
    return f"{prefix.strip('/')}/{uuid.uuid4().hex}/{safe_name(filename)}"


def ensure_buckets() -> list[str]:
    """Create any missing private buckets. Returns the bucket names that exist afterwards."""
    global _buckets_ready
    client = _client()
    existing = {b.name for b in client.storage.list_buckets()}
    for name in get_settings().storage_buckets:
        if name not in existing:
            client.storage.create_bucket(name, options={"public": False})
            log.info("created private storage bucket %s", name)
    _buckets_ready = True
    return sorted({b.name for b in client.storage.list_buckets()} & set(get_settings().storage_buckets))


def _ready() -> Client:
    if not _buckets_ready:
        ensure_buckets()
    return _client()


def upload(bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Store bytes at bucket/path (overwrites). Returns the path."""
    _ready().storage.from_(bucket).upload(
        path, data, {"content-type": content_type, "upsert": "true"}
    )
    return path


def download(bucket: str, path: str) -> bytes:
    return bytes(_ready().storage.from_(bucket).download(path))


def delete(bucket: str, paths: list[str]) -> None:
    _ready().storage.from_(bucket).remove(paths)


def signed_url(bucket: str, path: str, expires_in: int = 300) -> str:
    res: Any = _ready().storage.from_(bucket).create_signed_url(path, expires_in)
    return str(res["signedURL"] if "signedURL" in res else res["signed_url"])


def exists(bucket: str, path: str) -> bool:
    folder, _, name = path.rpartition("/")
    items = _ready().storage.from_(bucket).list(folder)
    return any(i.get("name") == name for i in items)


def health() -> dict[str, Any]:
    """Non-mutating check used by /health: can we reach Storage, and do the buckets exist and stay private?"""
    s = get_settings()
    if not s.storage_configured:
        return {"storage": "not_configured"}
    try:
        buckets = {b.name: b for b in _client().storage.list_buckets()}
    except Exception as exc:  # noqa: BLE001
        return {"storage": f"error: {type(exc).__name__}"}
    missing = [n for n in s.storage_buckets if n not in buckets]
    public = [n for n in s.storage_buckets if n in buckets and getattr(buckets[n], "public", False)]
    status = "ok" if not missing and not public else "degraded"
    return {"storage": status, "storage_missing_buckets": missing, "storage_public_buckets": public}
