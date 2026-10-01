"""Model registry: artifacts live in Supabase Storage (private bucket) with a local cache; runs/metrics in `model_runs`."""

import io
import logging
import time
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ModelRun
from app.services import storage

log = logging.getLogger(__name__)
_memory: dict[int, Any] = {}


def _cache_path(run_id: int) -> Path:
    p = get_settings().model_cache_path / "sgdss"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"run_{run_id}.joblib"


def save_run(
    db: Session, *, name: str, task: str, metrics: dict[str, Any], params: dict[str, Any] | None = None,
    artifact: Any = None, n_train: int = 0, n_test: int = 0, train_seconds: float = 0.0, activate: bool = False,
) -> ModelRun:
    """Register a trained model. If `activate`, it becomes the active model for its name (others are deactivated)."""
    version = time.strftime("%Y%m%d-%H%M%S")
    run = ModelRun(name=name, task=task, version=version, metrics=metrics, params=params or {}, n_train=n_train,
                   n_test=n_test, train_seconds=train_seconds)
    db.add(run)
    db.flush()
    if artifact is not None:
        buf = io.BytesIO()
        joblib.dump(artifact, buf, compress=3)
        data = buf.getvalue()
        bucket = get_settings().storage_bucket_models
        if get_settings().storage_configured:
            run.artifact_bucket = bucket
            run.artifact_path = storage.upload(bucket, f"{name}/{version}.joblib", data, "application/octet-stream")
        _cache_path(run.id).write_bytes(data)
        run.params = {**run.params, "artifact_bytes": len(data)}
    if activate:
        db.execute(update(ModelRun).where(ModelRun.name == name, ModelRun.id != run.id).values(is_active=False))
        run.is_active = True
    db.commit()
    return run


def load_artifact(run: ModelRun) -> Any:
    if run.id in _memory:
        return _memory[run.id]
    path = _cache_path(run.id)
    if not path.exists():
        if not (run.artifact_bucket and run.artifact_path):
            raise FileNotFoundError(f"Model run {run.id} has no stored artifact")
        log.info("downloading model artifact %s", run.artifact_path)
        path.write_bytes(storage.download(run.artifact_bucket, run.artifact_path))
    obj = joblib.load(path)
    _memory[run.id] = obj
    return obj


def active_run(db: Session, name: str) -> ModelRun | None:
    return db.scalars(
        select(ModelRun).where(ModelRun.name == name, ModelRun.is_active.is_(True)).order_by(ModelRun.id.desc()).limit(1)
    ).first()


def load_active(db: Session, name: str) -> tuple[ModelRun, Any] | None:
    run = active_run(db, name)
    if run is None or not run.artifact_path and not _cache_path(run.id).exists():
        return None
    return run, load_artifact(run)
