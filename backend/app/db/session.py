"""Database engines and sessions.

The database is remote (Supabase), so every network round trip costs ~300 ms. Two things follow:

* Transactional sessions (`get_db`) are for writes that need atomicity. psycopg sends BEGIN and COMMIT/ROLLBACK as
  separate round trips, so a single SELECT costs three.
* Autocommit sessions (`get_read_db`, `AutocommitSession`) send each statement once. Use them for read-only
  endpoints and for single-statement writes (cache, rate limiter), which are atomic on their own.

A connection is health-checked on checkout only if it has been idle for a while (instead of pre-pinging every time).
"""

import time
from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, event, exc
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_s = get_settings()
IDLE_PING_AFTER_S = 20.0

# prepare_threshold=None disables server-side prepared statements, which Supabase's transaction-mode
# pooler (Supavisor/pgbouncer, port 6543) does not support. It is harmless on direct connections.
engine = create_engine(
    _s.database_url,
    pool_size=_s.db_pool_size,
    max_overflow=_s.db_max_overflow,
    pool_recycle=1800,
    connect_args={"prepare_threshold": None},
)
autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")  # shares the same connection pool

if _s.db_search_path:

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_conn: Any, _record: Any) -> None:
        with dbapi_conn.cursor() as cur:
            cur.execute(f"SET search_path TO {_s.db_search_path}")
        dbapi_conn.commit()


@event.listens_for(engine, "checkout")
def _ping_if_idle(dbapi_conn: Any, record: Any, _proxy: Any) -> None:
    last = record.info.get("last_used", 0.0)
    if last and time.monotonic() - last > IDLE_PING_AFTER_S:
        try:
            was_autocommit = dbapi_conn.autocommit
            dbapi_conn.autocommit = True  # a ping must not open a transaction
            with dbapi_conn.cursor() as cur:
                cur.execute("SELECT 1")
            dbapi_conn.autocommit = was_autocommit
        except Exception as e:  # noqa: BLE001 - any failure means the connection is dead; the pool reconnects
            raise exc.DisconnectionError() from e
    record.info["last_used"] = time.monotonic()


@event.listens_for(engine, "checkin")
def _mark_used(_dbapi_conn: Any, record: Any) -> None:
    record.info["last_used"] = time.monotonic()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
AutocommitSession = sessionmaker(bind=autocommit_engine, autoflush=False, expire_on_commit=False)


def warm_pool(n: int | None = None) -> int:
    """Open `n` connections concurrently (a new remote connection takes ~5 s; do it at start, not on a user request)."""
    from concurrent.futures import ThreadPoolExecutor

    n = min(n or 3, _s.db_pool_size)

    def ping(_: int) -> None:
        with engine.connect() as c:
            c.exec_driver_sql("SELECT 1")
            time.sleep(0.05)  # keep it checked out so the others open distinct connections

    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(ping, range(n)))
    return n


def get_db() -> Iterator[Session]:
    """Transactional session for endpoints that write."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db() -> Iterator[Session]:
    """Autocommit session for read-only endpoints: one round trip per statement."""
    db = AutocommitSession()
    try:
        yield db
    finally:
        db.close()
