"""Test setup: an isolated Postgres *schema* (sgdss_test) in the configured database, migrated with Alembic.

Supabase does not let us create extra databases, so tests live in their own schema and never touch app data.
The connection used is TEST_DATABASE_URL, else MIGRATION_DATABASE_URL, else DATABASE_URL (from .env).
The schema is selected with `SET search_path` on each connection (DB_SEARCH_PATH), which needs a session-mode
connection (session pooler port 5432 or direct), NOT the transaction pooler (port 6543). Tests never call external APIs.
"""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import make_url, text

from app.core.config import Settings, normalize_db_url

TEST_SCHEMA = "sgdss_test"

_base = Settings(jwt_secret="placeholder-for-reading-env")  # read connection settings from .env
_admin_url = make_url(normalize_db_url(os.environ.get("TEST_DATABASE_URL") or _base.migration_url))
TEST_URL = _admin_url.render_as_string(hide_password=False)

os.environ.update(
    ENV="test",
    DATABASE_URL=TEST_URL,
    MIGRATION_DATABASE_URL="",  # make Alembic use the test URL above
    DB_SEARCH_PATH=f"{TEST_SCHEMA},public,extensions",  # isolate tests in their own schema
    USER_CACHE_SECONDS="0",  # role/active changes must apply immediately in tests
    HUGGINGFACEHUB_API_TOKEN="",  # tests must never call Hugging Face (or any external API)
    EMBEDDING_PROVIDER="hashing",
    JOBS_INLINE="false",  # tests run jobs explicitly
    JWT_SECRET="test-secret-test-secret-test-secret-123456",
    RATE_LIMIT_ENABLED="false",
    LLM_PROVIDER="mock",
    GROQ_API_KEY="",
)


def _reset_test_schema() -> None:
    plain = _admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(plain, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {TEST_SCHEMA}")


@pytest.fixture(scope="session", autouse=True)
def _migrated_db() -> None:
    _reset_test_schema()
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, check=True)


from app.core import ratelimit  # noqa: E402
from app.ingestion import pipeline  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import User, UserRole  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(_migrated_db: None) -> Iterator[None]:
    with engine.begin() as conn:
        # Never truncate app data: unqualified table names must resolve INSIDE the test schema.
        for table in ("users", "jobs", "cache_entries", "rate_limits", "audit_logs", "complaints", "wards", "documents"):
            schema = conn.execute(
                text(
                    "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.oid = to_regclass(:t)"
                ),
                {"t": table},
            ).scalar()
            assert schema == TEST_SCHEMA, f"{table} resolves to schema {schema!r}; refusing to truncate app data"
        conn.execute(
            text(
                "TRUNCATE audit_logs, users, jobs, cache_entries, rate_limits, complaints, feedback, service_performance, "
                "document_chunks, documents, ingestion_logs, model_runs RESTART IDENTITY CASCADE"
            )
        )
    ratelimit.reset()
    pipeline.Refs.invalidate()
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c


PASSWORD = "Passw0rd-test"


@pytest.fixture
def make_user() -> Callable[..., User]:
    def _make(role: UserRole = UserRole.ANALYST, email: str | None = None, **kw: object) -> User:
        with SessionLocal() as db:
            user = User(
                email=email or f"{role.value}@test.org",
                full_name=f"Test {role.value}",
                hashed_password=hash_password(PASSWORD),
                role=role,
                **kw,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return user

    return _make


@pytest.fixture
def auth_headers(
    client: TestClient, make_user: Callable[..., User]
) -> Callable[[UserRole], dict[str, str]]:
    def _headers(role: UserRole) -> dict[str, str]:
        user = make_user(role)
        r = client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    return _headers


DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="session")
def city(_migrated_db: None) -> None:
    """Departments and the 20 synthetic wards, loaded once per test session (never truncated between tests)."""
    from scripts.seed import load_departments, load_wards

    with SessionLocal() as db:
        load_departments(db)
        load_wards(db, DATA_DIR / "wards.geojson")
