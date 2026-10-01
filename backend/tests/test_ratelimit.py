import pytest
from sqlalchemy import text

from app.core import ratelimit
from app.db.session import engine


def test_counts_within_window_and_isolated_by_key() -> None:
    results = [ratelimit.hit("a", limit=3, window_s=60) for _ in range(5)]
    assert results == [True, True, True, False, False]
    assert ratelimit.hit("b", limit=3, window_s=60) is True


def test_new_window_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1_000_000.0}  # frozen clock: network latency must not move us across a window boundary
    monkeypatch.setattr(ratelimit, "_now", lambda: clock["now"])
    assert ratelimit.hit("w", limit=1, window_s=60) is True
    assert ratelimit.hit("w", limit=1, window_s=60) is False
    clock["now"] += 60
    assert ratelimit.hit("w", limit=1, window_s=60) is True


def test_cleanup_removes_only_old_windows() -> None:
    ratelimit.hit("recent", limit=5, window_s=60)
    with engine.begin() as c:
        c.execute(text("INSERT INTO rate_limits (key, window_start, count) VALUES ('old', now() - interval '2 hours', 3)"))
    assert ratelimit.cleanup_expired(older_than_s=3600) == 1
    with engine.connect() as c:
        assert c.execute(text("SELECT key FROM rate_limits")).scalars().all() == ["recent"]
