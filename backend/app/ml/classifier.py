"""Complaint category classifiers: TF-IDF + logistic regression (baseline) and an embedding-based classifier."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.pipeline import FeatureUnion, Pipeline

from app.nlp.preprocess import normalize_text

TOKEN_PATTERN = r"(?u)[\wऀ-ॿ]+"  # keeps Devanagari


def build_tfidf() -> FeatureUnion:
    """Word 1-2-grams plus character 2-5-grams (robust to Hinglish spelling variants and Devanagari)."""
    return FeatureUnion(
        [
            ("word", TfidfVectorizer(preprocessor=normalize_text, token_pattern=TOKEN_PATTERN, ngram_range=(1, 2),
                                     min_df=2, max_features=30000, sublinear_tf=True)),
            ("char", TfidfVectorizer(preprocessor=normalize_text, analyzer="char_wb", ngram_range=(2, 5), min_df=3,
                                     max_features=50000, sublinear_tf=True)),
        ]
    )


@dataclass
class CategoryModel:
    kind: str  # "tfidf_lr" | "embedding_lr"
    estimator: Any  # Pipeline (text -> proba) or LogisticRegression (embedding -> proba)
    classes: list[str]

    def predict_proba(self, inputs: Any) -> np.ndarray:
        return np.asarray(self.estimator.predict_proba(inputs))

    def predict(self, inputs: Any) -> tuple[list[str], np.ndarray]:
        p = self.predict_proba(inputs)
        idx = p.argmax(axis=1)
        return [self.classes[i] for i in idx], p.max(axis=1)


def train_tfidf_lr(texts: Sequence[str], labels: Sequence[str], C: float = 10.0) -> CategoryModel:
    pipe = Pipeline([("tfidf", build_tfidf()), ("lr", LogisticRegression(C=C, max_iter=300))])
    pipe.fit(list(texts), list(labels))
    return CategoryModel("tfidf_lr", pipe, [str(c) for c in pipe.classes_])


def train_embedding_lr(vectors: np.ndarray, labels: Sequence[str], C: float = 10.0) -> CategoryModel:
    lr = LogisticRegression(C=C, max_iter=500)
    lr.fit(vectors, list(labels))
    return CategoryModel("embedding_lr", lr, [str(c) for c in lr.classes_])


def evaluate(y_true: Sequence[str], y_pred: Sequence[str], classes: Sequence[str]) -> dict[str, Any]:
    """Accuracy, macro and per-class precision/recall/F1, and the confusion matrix (rows = truth, cols = prediction)."""
    classes = list(classes)
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=classes, zero_division=0)
    return {
        "n": len(y_true),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(p.mean()), "macro_recall": float(r.mean()), "macro_f1": float(f.mean()),
        "per_class": {c: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]), "support": int(s[i])}
                      for i, c in enumerate(classes)},
        "labels": classes,
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=classes).tolist(),
    }


def accuracy_by(groups: Sequence[str | None], y_true: Sequence[str], y_pred: Sequence[str]) -> dict[str, dict[str, float]]:
    out: dict[str, list[int]] = {}
    for g, t, p in zip(groups, y_true, y_pred):
        k = g or "unknown"
        o = out.setdefault(k, [0, 0])
        o[0] += 1
        o[1] += t == p
    return {k: {"n": v[0], "accuracy": v[1] / v[0]} for k, v in sorted(out.items())}


def confidence_report(conf: np.ndarray, correct: np.ndarray, threshold: float) -> dict[str, Any]:
    """How trustworthy is the confidence? Accuracy per confidence bin, and the effect of the review threshold."""
    bins = [0.0, 0.4, 0.6, 0.8, 0.95, 1.0001]
    rows = []
    for lo, hi in zip(bins, bins[1:]):
        m = (conf >= lo) & (conf < hi)
        rows.append({"range": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": int(m.sum()),
                     "accuracy": float(correct[m].mean()) if m.any() else None})
    flagged = conf < threshold
    return {
        "bins": rows, "review_threshold": threshold, "flagged_share": float(flagged.mean()),
        "accuracy_flagged": float(correct[flagged].mean()) if flagged.any() else None,
        "accuracy_unflagged": float(correct[~flagged].mean()) if (~flagged).any() else None,
    }
