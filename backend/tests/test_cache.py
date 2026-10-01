import time

from app.db.models import CacheEntry
from app.db.session import SessionLocal
from app.services import cache


def test_set_get_delete_roundtrip() -> None:
    assert cache.get("k1") is None
    cache.set("k1", {"a": [1, 2], "b": "x"}, ttl_seconds=60)
    assert cache.get("k1") == {"a": [1, 2], "b": "x"}
    cache.set("k1", {"a": 3}, ttl_seconds=60)  # upsert replaces
    assert cache.get("k1") == {"a": 3}
    assert cache.delete_key("k1") is True
    assert cache.get("k1") is None
    assert cache.delete_key("k1") is False


def test_expiry_and_cleanup() -> None:
    cache.set("short", "v", ttl_seconds=1)
    cache.set("long", "v", ttl_seconds=600)
    time.sleep(1.2)
    assert cache.get("short") is None  # expired rows are invisible even before cleanup
    assert cache.get("long") == "v"
    assert cache.cleanup_expired() == 1
    with SessionLocal() as db:
        assert [r.key for r in db.query(CacheEntry)] == ["long"]


def test_get_or_set_computes_once() -> None:
    calls: list[int] = []

    def factory() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    v1, hit1 = cache.get_or_set("kpi:x", 60, factory)
    v2, hit2 = cache.get_or_set("kpi:x", 60, factory)
    assert (hit1, hit2) == (False, True) and v1 == v2 == {"n": 1} and len(calls) == 1


def test_make_key_is_stable_and_bounded() -> None:
    a = cache.make_key("llm", "prompt", "model-x", {"t": 0.2})
    assert a == cache.make_key("llm", "prompt", "model-x", {"t": 0.2})
    assert a != cache.make_key("llm", "prompt", "model-y", {"t": 0.2})
    assert a.startswith("llm:") and len(a) <= cache.MAX_KEY_LEN


def test_delete_prefix_invalidates_namespace() -> None:
    for k in ("kpi:a", "kpi:b", "other:c"):
        cache.set(k, 1, 60)
    assert cache.delete_prefix("kpi:") == 2
    assert cache.get("other:c") == 1
