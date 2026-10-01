"""Compute complaint embeddings with the Hugging Face Inference API and store them in pgvector (halfvec).

Resumable (only rows with a NULL embedding are touched) and safe to stop at any time. Text is PII-stripped before it is
sent to the external service. Only HF embeddings are stored; never mix providers in the same column.
"""

import logging
import time
from collections.abc import Callable

import numpy as np
from sqlalchemy import text

from app.core.pii import strip_pii
from app.db.session import AutocommitSession
from app.nlp.embeddings import EmbeddingProvider, get_embedder
from app.nlp.hf_client import HFError

log = logging.getLogger(__name__)

_UPDATE = text(
    "UPDATE complaints c SET embedding = CAST(v.emb AS halfvec) "
    "FROM unnest(CAST(:ids AS int[]), CAST(:embs AS text[])) AS v(id, emb) WHERE c.id = v.id"
)


def vector_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.5g}" for x in v.tolist()) + "]"


def count_missing() -> int:
    with AutocommitSession() as db:
        return int(db.scalar(text("SELECT count(*) FROM complaints WHERE embedding IS NULL")) or 0)


def store_embeddings(ids: list[int], vectors: np.ndarray) -> None:
    with AutocommitSession() as db:
        db.execute(_UPDATE, {"ids": ids, "embs": [vector_literal(v) for v in vectors]})


def backfill_embeddings(
    *,
    limit: int | None = None,
    max_seconds: float | None = None,
    chunk: int = 512,
    provider: EmbeddingProvider | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int | str]:
    """Embed complaints that have no embedding yet. Returns {"done": n, "remaining": m, "stopped": reason}."""
    embedder = provider or get_embedder("hf")
    started = time.monotonic()
    done = 0
    stopped = "complete"
    total = count_missing()
    while True:
        if limit is not None and done >= limit:
            stopped = "limit"
            break
        if max_seconds is not None and time.monotonic() - started > max_seconds:
            stopped = "time budget"
            break
        take = chunk if limit is None else min(chunk, limit - done)
        with AutocommitSession() as db:
            rows = db.execute(
                text("SELECT id, description FROM complaints WHERE embedding IS NULL ORDER BY id LIMIT :n"), {"n": take}
            ).all()
        if not rows:
            break
        try:
            vectors = embedder.embed([strip_pii(r.description) for r in rows])
        except HFError as exc:
            stopped = f"{type(exc).__name__}: {exc}"
            log.error("embedding backfill stopped: %s", stopped)
            break
        store_embeddings([r.id for r in rows], vectors)
        done += len(rows)
        if progress:
            progress(done, total)
    return {"done": done, "remaining": count_missing(), "stopped": stopped}
