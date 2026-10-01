"""Database-backed enrichment: load the active models, enrich one complaint or all of them, persist the predictions."""

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.pii import strip_pii
from app.db.session import AutocommitSession
from app.ml import duplicates as dup
from app.ml import registry
from app.ml.dataset import Row, load_embeddings, load_rows, with_retry
from app.ml.predict import Enrichment, Predictor, _with_label
from app.nlp.embeddings import get_embedder
from app.nlp.hf_client import HFError

log = logging.getLogger(__name__)
_cached: tuple[float, Predictor] | None = None
PREDICTOR_TTL_S = 120

_SAVE = text(
    "UPDATE complaints c SET predicted_category = v.cat, category_confidence = v.conf, "
    "predicted_department_id = NULLIF(v.dept, 0), sentiment_score = v.sent, urgency_score = v.urg, "
    "needs_review = CASE WHEN c.reviewed_at IS NOT NULL THEN c.needs_review ELSE v.nr END, "
    "review_reason = CASE WHEN c.reviewed_at IS NOT NULL THEN c.review_reason ELSE v.reason END, "
    "entities = CAST(v.ent AS jsonb), duplicate_of_id = NULLIF(v.dup, 0), language = COALESCE(c.language, v.lang) "
    "FROM unnest(CAST(:id AS int[]), CAST(:cat AS text[]), CAST(:conf AS float8[]), CAST(:dept AS int[]), "
    "CAST(:sent AS float8[]), CAST(:urg AS float8[]), CAST(:nr AS bool[]), CAST(:reason AS text[]), CAST(:ent AS text[]), "
    "CAST(:dup AS int[]), CAST(:lang AS text[])) AS v(id, cat, conf, dept, sent, urg, nr, reason, ent, dup, lang) "
    "WHERE c.id = v.id"
)


_SAVE_EXT = text(
    "UPDATE complaints c SET predicted_category = v.cat, category_confidence = v.conf, "
    "predicted_department_id = d.id, sentiment_score = v.sent, urgency_score = v.urg, "
    "needs_review = CASE WHEN c.reviewed_at IS NOT NULL THEN c.needs_review ELSE v.nr END, "
    "review_reason = CASE WHEN c.reviewed_at IS NOT NULL THEN c.review_reason ELSE NULLIF(v.reason, '') END, "
    "entities = CAST(v.ent AS jsonb), duplicate_of_id = o.id, language = COALESCE(c.language, v.lang) "
    "FROM unnest(CAST(:ext AS text[]), CAST(:cat AS text[]), CAST(:conf AS float8[]), CAST(:sent AS float8[]), "
    "CAST(:urg AS float8[]), CAST(:nr AS bool[]), CAST(:reason AS text[]), CAST(:ent AS text[]), CAST(:dupext AS text[]), "
    "CAST(:lang AS text[])) AS v(ext, cat, conf, sent, urg, nr, reason, ent, dupext, lang) "
    "LEFT JOIN departments d ON d.category = v.cat "
    "LEFT JOIN complaints o ON o.external_id = NULLIF(v.dupext, '') "
    "WHERE c.external_id = v.ext"
)


def save_enrichments_by_external_id(items: list[Enrichment], ext_ids: list[str], dup_ext: list[str]) -> None:
    """Write predictions keyed by external id: only uploads data, nothing is read back from the database."""
    params = {
            "ext": ext_ids, "cat": [e.predicted_category for e in items], "conf": [e.confidence for e in items],
            "sent": [e.sentiment for e in items], "urg": [e.urgency for e in items], "nr": [e.needs_review for e in items],
            "reason": [e.review_reason or "" for e in items], "ent": [json.dumps(e.entities, ensure_ascii=False) for e in items],
            "dupext": dup_ext, "lang": [e.language for e in items],
    }

    def run() -> None:
        with AutocommitSession() as db:
            db.execute(_SAVE_EXT, params)

    with_retry(run)  # the remote pooler occasionally drops a connection mid-statement


def enrich_rows_and_save(predictor: Predictor, rows: list[Row], *, save_chunk: int = 1000,
                         progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Predict for `rows` (e.g. from the local CSV) and upload the results by external id."""
    items = predictor.enrich(rows)
    by_id = {r.id: r.external_id or "" for r in rows}
    for i in range(0, len(items), save_chunk):
        part = items[i : i + save_chunk]
        save_enrichments_by_external_id(
            part, [by_id[e.id] for e in part], [by_id.get(e.duplicate_of_id or -1, "") for e in part]
        )
        if progress:
            progress(min(i + save_chunk, len(items)), len(items))
    return {"complaints": len(items), "needs_review": sum(e.needs_review for e in items),
            "duplicates": sum(e.duplicate_of_id is not None for e in items),
            "urgency_levels": {lv: sum(e.urgency_level == lv for e in items) for lv in ("low", "medium", "high", "critical")}}


def load_predictor(db: Session) -> Predictor | None:
    """Assemble the active models. None when no category model has been trained yet."""
    cat = registry.load_active(db, "category_tfidf_lr")
    if cat is None:
        return None
    emb = registry.load_active(db, "category_embedding_lr")
    breach = registry.load_active(db, "breach_risk")
    dup_emb = registry.load_active(db, "duplicates_with_embeddings")
    dup_text = registry.load_active(db, "duplicates_text_only")
    wards = [r[0] for r in db.execute(text("SELECT name FROM wards"))]
    depts = {r[0]: r[1] for r in db.execute(text("SELECT category, id FROM departments WHERE category IS NOT NULL"))}
    return Predictor(
        category=cat[1], embedding=emb[1] if emb else None, breach=breach[1] if breach else None,
        duplicates=(dup_emb or dup_text or (None, None))[1], duplicates_text=dup_text[1] if dup_text else None,
        ward_names=wards, dept_by_category=depts,
    )


def get_predictor(db: Session) -> Predictor | None:
    global _cached
    if _cached and time.monotonic() - _cached[0] < PREDICTOR_TTL_S:
        return _cached[1]
    p = load_predictor(db)
    if p is not None:
        _cached = (time.monotonic(), p)
    return p


def reset_predictor_cache() -> None:
    global _cached
    _cached = None


def save_enrichments(items: list[Enrichment]) -> None:
    if not items:
        return
    with AutocommitSession() as db:
        db.execute(
            _SAVE,
            {
                "id": [e.id for e in items], "cat": [e.predicted_category for e in items],
                "conf": [e.confidence for e in items], "dept": [e.predicted_department_id or 0 for e in items],
                "sent": [e.sentiment for e in items], "urg": [e.urgency for e in items],
                "nr": [e.needs_review for e in items], "reason": [e.review_reason or "" for e in items],
                "ent": [json.dumps(e.entities, ensure_ascii=False) for e in items],
                "dup": [e.duplicate_of_id or 0 for e in items], "lang": [e.language for e in items],
            },
        )


def enrich_all(
    predictor: Predictor, *, use_embeddings: bool = True, save_chunk: int = 1000,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run every prediction for every complaint and store the results."""
    rows = load_rows()
    emb = load_embeddings([r.id for r in rows]) if use_embeddings else None
    enrichments = predictor.enrich(rows, emb)
    for i in range(0, len(enrichments), save_chunk):
        save_enrichments(enrichments[i : i + save_chunk])
        if progress:
            progress(min(i + save_chunk, len(enrichments)), len(enrichments))
    return {
        "complaints": len(enrichments),
        "needs_review": sum(e.needs_review for e in enrichments),
        "duplicates": sum(e.duplicate_of_id is not None for e in enrichments),
        "with_embeddings": int((~np.isnan(emb[:, 0])).sum()) if emb is not None else 0,
    }


def _row_by_id(db: Session, complaint_id: int) -> Row | None:
    r = db.execute(
        text(
            "SELECT id, external_id, description, language, category, ward_id, department_id, submitted_at, resolved_at, "
            "resolution_hours, sla_hours, channel, status, ST_Y(location) AS lat, ST_X(location) AS lon "
            "FROM complaints WHERE id = :id"
        ),
        {"id": complaint_id},
    ).first()
    return Row(*r) if r else None


def enrich_complaint(complaint_id: int) -> dict[str, Any] | None:
    """Enrich one (new) complaint: embed it via Hugging Face when configured, classify, score, find duplicates."""
    with AutocommitSession() as db:
        predictor = get_predictor(db)
        if predictor is None:
            return None
        row = _row_by_id(db, complaint_id)
        if row is None:
            return None
        emb = None
        s = get_settings()
        if s.hf_configured and s.embedding_provider != "hashing":
            try:
                vec = get_embedder("hf").embed([strip_pii(row.text)])
                from app.ml.embed_store import store_embeddings

                store_embeddings([row.id], vec)
                emb = vec
            except HFError as exc:  # enrichment still works without the embedding
                log.warning("embedding skipped for complaint %s: %s", complaint_id, exc)
        pred_cat = predictor.category_probs([row.text], emb)[0].argmax()
        category = predictor.category.classes[int(pred_cat)]
        counts = db.execute(
            text(
                "SELECT count(*) FILTER (WHERE COALESCE(predicted_category, category) = :cat), count(*) FROM complaints "
                "WHERE ward_id = :w AND id <> :id AND submitted_at < :t AND submitted_at >= :t - interval '7 days'"
            ),
            {"cat": category, "w": row.ward_id, "id": row.id, "t": row.submitted_at},
        ).one()
        result = predictor.enrich([row], emb, recent_same=np.array([counts[0]]), recent_ward=np.array([counts[1]]),
                                  detect_duplicates=False)[0]
        match = _find_duplicate(db, predictor, row, category, emb)
        if match:
            result.duplicate_of_id, result.duplicate_prob = match
    save_enrichments([result])
    return {
        "id": complaint_id, "category": result.predicted_category, "confidence": round(result.confidence, 3),
        "urgency": result.urgency, "needs_review": result.needs_review, "duplicate_of_id": result.duplicate_of_id,
    }


def _find_duplicate(db: Session, predictor: Predictor, row: Row, category: str, emb: np.ndarray | None) -> tuple[int, float] | None:
    use_emb = emb is not None and predictor.duplicates is not None and predictor.duplicates.use_emb
    model = predictor.duplicates if use_emb else (predictor.duplicates_text or predictor.duplicates)
    if model is None:
        return None
    cand = db.execute(
        text(
            "SELECT id, external_id, description, language, COALESCE(category, predicted_category) AS category, ward_id, "
            "department_id, submitted_at, resolved_at, resolution_hours, sla_hours, channel, status, "
            "ST_Y(location) AS lat, ST_X(location) AS lon "
            "FROM complaints WHERE ward_id = :w AND id <> :id AND submitted_at < :t "
            "AND submitted_at >= :t - interval '72 hours' ORDER BY submitted_at"
        ),
        {"w": row.ward_id, "id": row.id, "t": row.submitted_at},
    ).all()
    if not cand:
        return None
    rows = [Row(*c) for c in cand] + [row]
    rows = rows[:-1] + [_with_label(row, category)]  # candidates keep their own recorded/predicted category
    vecs = None
    if model.use_emb:
        vecs = load_embeddings([r.id for r in rows])
    corpus = dup.build_corpus(rows, predictor.tfidf, vecs)
    found = dup.detect(model, corpus)
    hit = found.get(len(rows) - 1)
    return (rows[hit[0]].id, hit[1]) if hit else None
