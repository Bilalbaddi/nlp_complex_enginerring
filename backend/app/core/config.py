import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url

PLACEHOLDER_SECRET = "change-me-to-a-long-random-string"

# Repo root (…/sg-dss). `.env` lives there so the backend, worker and task runner share one file.
ROOT_DIR = Path(__file__).resolve().parents[3]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db"}


def normalize_db_url(value: str) -> str:
    """Force the psycopg 3 driver and, for any non-local host, `sslmode=require`."""
    url = make_url(value.strip())
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    if url.host not in LOCAL_HOSTS and "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return url.render_as_string(hide_password=False)


class Settings(BaseSettings):
    """All configuration comes from environment variables / the repo-root `.env` (see .env.example)."""

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    app_name: str = "SG-DSS"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # Supabase is the ONLY external service: Postgres (data, cache, jobs, rate limits) + Storage (files).
    # DATABASE_URL may be the pooler URL; MIGRATION_DATABASE_URL (optional) is a direct / session-mode URL
    # used by Alembic and the tests (DDL and CREATE EXTENSION don't work well through a transaction pooler).
    database_url: str
    migration_database_url: str | None = None
    # Optional `SET search_path` applied on every new connection (used by tests to isolate a schema).
    # Needs a session-mode connection: transaction poolers don't keep session state.
    db_search_path: str | None = None
    db_pool_size: int = 5
    # Seconds a validated user is remembered in-process (saves one ~300 ms DB round trip per request).
    # A deactivation or role change is therefore effective within this many seconds. 0 disables the cache.
    user_cache_seconds: int = 10
    db_max_overflow: int = 5

    # Supabase Storage (private buckets). The service-role key is BACKEND ONLY; never send it to the frontend.
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    storage_bucket_uploads: str = "sgdss-uploads"
    storage_bucket_documents: str = "sgdss-documents"
    storage_bucket_reports: str = "sgdss-reports"

    # Background jobs live in the `jobs` table. jobs_inline=None -> True in development, False otherwise.
    jobs_inline: bool | None = None
    job_max_attempts: int = 3
    job_retry_base_seconds: int = 10
    job_stale_after_seconds: int = 900

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    cors_origins: str = "http://localhost:3000"

    rate_limit_enabled: bool = True
    login_rate_limit_per_minute: int = 10
    llm_rate_limit_per_minute: int = 20

    # LLM (used from Phase 7; declared now so .env is complete)
    llm_provider: Literal["groq", "mock"] = "mock"
    groq_api_key: str = ""
    llm_model: str = ""
    llm_fast_model: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 4
    llm_reasoning_effort: Literal["low", "medium", "high"] = "low"  # gpt-oss models only
    llm_cache_ttl_seconds: int = 24 * 3600

    # Embeddings come from the Hugging Face Inference API (nothing is downloaded). halfvec keeps 384 dims at 768 B/row.
    # "auto" = HF when a token is set, else the offline hashing embedder (lower quality, single-language similarity).
    embedding_provider: Literal["auto", "hf", "hashing"] = "auto"
    embedding_dim: int = 384
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    huggingfacehub_api_token: str = ""
    hf_api_base: str = "https://router.huggingface.co/hf-inference/models"
    hf_batch_size: int = 64
    hf_concurrency: int = 4  # parallel requests per embed() call
    hf_timeout_s: float = 60.0
    hf_max_retries: int = 5
    hf_requests_per_minute: int = 120  # client-side throttle to stay inside the free-tier limits
    # Trained model artifacts are cached here (inside the project, git-ignored) and stored in Supabase Storage.
    model_cache_dir: str = ".cache/models"
    storage_bucket_models: str = "sgdss-models"

    # NLP: complaints below this classifier confidence, or confidently disagreeing with their label, go to human review
    review_confidence_threshold: float = 0.6
    review_mislabel_confidence: float = 0.85

    # Synthetic data size. Supabase free tier = 500 MB total including indexes. Measured: ~0.7 KB per complaint
    # (data + indexes), plus ~0.8 KB once embeddings are stored, so 50,000 complaints is roughly 80 MB (Phase 3).
    seed_complaint_count: int = 50000
    seed_years: int = 3
    seed_random_seed: int = 42

    @field_validator("database_url")
    @classmethod
    def _db_url(cls, v: str) -> str:
        return normalize_db_url(v)

    @field_validator("migration_database_url")
    @classmethod
    def _migration_db_url(cls, v: str | None) -> str | None:
        return normalize_db_url(v) if v and v.strip() else None

    @field_validator("db_search_path")
    @classmethod
    def _search_path(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if v and not re.fullmatch(r"[A-Za-z0-9_, ]+", v):
            raise ValueError("DB_SEARCH_PATH may only contain letters, digits, underscores and commas")
        return v or None

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _check_secret(self) -> "Settings":
        if self.env == "production" and (
            self.jwt_secret == PLACEHOLDER_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError("JWT_SECRET must be set to a random string of 32+ chars in production")
        return self

    @property
    def migration_url(self) -> str:
        return self.migration_database_url or self.database_url

    @property
    def jobs_run_inline(self) -> bool:
        return self.env == "development" if self.jobs_inline is None else self.jobs_inline

    @property
    def storage_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def storage_buckets(self) -> list[str]:
        return [
            self.storage_bucket_uploads, self.storage_bucket_documents,
            self.storage_bucket_reports, self.storage_bucket_models,
        ]

    @property
    def hf_configured(self) -> bool:
        return bool(self.huggingfacehub_api_token)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def model_cache_path(self) -> Path:
        p = Path(self.model_cache_dir)
        return p if p.is_absolute() else ROOT_DIR / p

    @property
    def llm_demo_mode(self) -> bool:
        """True when AI features fall back to the mock provider."""
        return self.llm_provider == "mock" or not self.groq_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
