from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_read_db
from app.schemas.common import Envelope, ok
from app.services import storage

router = APIRouter(tags=["system"])


@router.get("/health", response_model=Envelope[dict[str, object]])
def health(db: Session = Depends(get_read_db)) -> dict[str, object]:
    """Database, PostGIS/pgvector extensions, Supabase Storage, job mode and LLM demo-mode flag."""
    s = get_settings()
    checks: dict[str, object] = {}
    try:
        db.execute(text("SELECT 1"))
        exts = set(db.scalars(text("SELECT extname FROM pg_extension")).all())
        checks["database"] = "ok"
        checks["postgis"] = "postgis" in exts
        checks["pgvector"] = "vector" in exts
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    checks.update(storage.health())
    checks["jobs_mode"] = "inline" if s.jobs_run_inline else "worker"
    checks["llm_demo_mode"] = s.llm_demo_mode
    checks["llm_provider"] = "mock" if s.llm_demo_mode else s.llm_provider
    healthy = (
        checks["database"] == "ok"
        and checks.get("postgis") is True
        and checks.get("pgvector") is True
        and checks["storage"] == "ok"
    )
    return ok({"status": "ok" if healthy else "degraded", **checks})
