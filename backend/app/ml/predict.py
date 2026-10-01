"""The prediction bundle: category, department routing, sentiment, urgency, entities, review flag, duplicates."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.config import get_settings
from app.ingestion.normalize import CATEGORIES
from app.ml import duplicates as dup
from app.ml.breach import BreachModel, build_features
from app.ml.classifier import CategoryModel
from app.ml.dataset import Row
from app.ml.features import group_codes, recent_counts, to_hours
from app.nlp.entities import extract_entities
from app.nlp.language import detect_language
from app.nlp.sentiment import sentiment
from app.nlp.urgency import UrgencyResult, urgency_score


@dataclass
class Enrichment:
    id: int
    language: str
    predicted_category: str
    confidence: float
    predicted_department_id: int | None
    sentiment: float
    urgency: float
    urgency_level: str
    urgency_reasons: list[str]
    entities: dict[str, list[str]]
    needs_review: bool
    review_reason: str | None
    duplicate_of_id: int | None = None
    duplicate_prob: float | None = None
    top3: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class Predictor:
    category: CategoryModel  # TF-IDF + LR: always available, works offline
    embedding: CategoryModel | None = None  # optional second opinion, used when an embedding is supplied
    breach: BreachModel | None = None
    duplicates: dup.DuplicateModel | None = None  # uses embeddings when they exist
    duplicates_text: dup.DuplicateModel | None = None  # fallback without embeddings
    ward_names: list[str] = field(default_factory=list)
    dept_by_category: dict[str, int] = field(default_factory=dict)

    @property
    def tfidf(self) -> Any:
        return self.category.estimator.named_steps["tfidf"]

    def category_probs(self, texts: list[str], emb: np.ndarray | None = None) -> tuple[np.ndarray, list[str]]:
        """Class probabilities (n, k). Averages the embedding classifier in for rows that have an embedding."""
        p = self.category.predict_proba(texts)
        classes = self.category.classes
        if self.embedding is not None and emb is not None and self.embedding.classes == classes:
            ok = ~np.isnan(emb[:, 0])
            if ok.any():
                p[ok] = 0.5 * p[ok] + 0.5 * self.embedding.predict_proba(emb[ok])
        return p, classes

    def enrich(
        self,
        rows: list[Row],
        emb: np.ndarray | None = None,
        *,
        recent_same: np.ndarray | None = None,
        recent_ward: np.ndarray | None = None,
        detect_duplicates: bool = True,
    ) -> list[Enrichment]:
        """Predict everything for `rows`. `recent_*` (earlier same-ward(+category) complaints in the past 7 days) are
        computed from `rows` themselves unless supplied (single-complaint mode passes them from the database)."""
        s = get_settings()
        if not rows:
            return []
        texts = [r.text for r in rows]
        probs, classes = self.category_probs(texts, emb)
        idx = probs.argmax(axis=1)
        conf = probs.max(axis=1)
        pred = [classes[k] for k in idx]

        hours = to_hours([r.submitted_at for r in rows])
        ward = np.array([r.ward_id or 0 for r in rows])
        cat = np.array([CATEGORIES.index(p) if p in CATEGORIES else -1 for p in pred])
        rs = recent_same if recent_same is not None else recent_counts(group_codes(ward, cat), hours)
        rw = recent_ward if recent_ward is not None else recent_counts(ward, hours)

        risk: np.ndarray | None = None
        if self.breach is not None:
            tmp = [_with_label(r, p) for r, p in zip(rows, pred)]  # features use the PREDICTED category
            risk = self.breach.predict(build_features(tmp, self.breach.enc, hours, rs, rw))

        dups: dict[int, tuple[int, float]] = {}
        model = self.duplicates
        if model is not None and model.use_emb and (emb is None or np.isnan(emb[:, 0]).any()):
            model = self.duplicates_text  # embeddings missing for some complaints: use the text-only model
        if detect_duplicates and model is not None and len(rows) > 1:
            tmp = [_with_label(r, p) for r, p in zip(rows, pred)]
            corpus = dup.build_corpus(tmp, self.tfidf, emb if model.use_emb else None)
            dups = dup.detect(model, corpus)

        out: list[Enrichment] = []
        for n, r in enumerate(rows):
            lang = r.language or detect_language(r.text)
            sent = sentiment(r.text, lang)
            u: UrgencyResult = urgency_score(
                r.text, pred[n], sent, cluster_count=int(rs[n]), breach_risk=float(risk[n]) if risk is not None else None
            )
            reason = None
            if conf[n] < s.review_confidence_threshold:
                reason = f"low confidence ({conf[n]:.2f})"
            elif r.label and pred[n] != r.label and conf[n] >= s.review_mislabel_confidence:
                reason = f"model confidently disagrees with the recorded category '{r.label}' ({conf[n]:.2f})"
            top = np.argsort(probs[n])[::-1][:3]
            d = dups.get(n)
            out.append(
                Enrichment(
                    id=r.id, language=lang, predicted_category=pred[n], confidence=float(conf[n]),
                    predicted_department_id=self.dept_by_category.get(pred[n]), sentiment=sent, urgency=u.score,
                    urgency_level=u.level, urgency_reasons=u.reasons, entities=extract_entities(r.text, self.ward_names),
                    needs_review=reason is not None, review_reason=reason,
                    duplicate_of_id=rows[d[0]].id if d else None, duplicate_prob=d[1] if d else None,
                    top3=[(classes[k], float(probs[n][k])) for k in top],
                )
            )
        return out


def _with_label(r: Row, label: str) -> Row:
    import copy

    c = copy.copy(r)
    c.label = label
    return c
