"""SLA-breach risk model: the probability that a complaint will miss its SLA, using only what is known when it is filed.

It is the learned ingredient of the urgency score (the rest is transparent rules). Evaluated on a chronological split
so that it is never tested on the past it was trained on.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, average_precision_score, brier_score_loss, roc_auc_score

from app.ingestion.normalize import CATEGORIES, IST
from app.ml.dataset import Row
from app.ml.features import EPOCH, group_codes, recent_counts, to_hours
from app.nlp.urgency import urgency_score

LANGS = ["en", "hinglish", "hi", "mr"]
CHANNELS = ["mobile_app", "web", "call_center", "whatsapp", "ward_office"]
SMOOTH = 25.0


@dataclass
class BreachEncoders:
    global_rate: float = 0.4
    ward: dict[int, float] = field(default_factory=dict)
    pair: dict[tuple[int, int], float] = field(default_factory=dict)


@dataclass
class BreachModel:
    clf: Any
    enc: BreachEncoders
    feature_names: list[str]

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(X)[:, 1]


def eligible_labels(rows: list[Row], end_hours: float, hours: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(mask of usable rows, breach label). Resolved: took longer than the SLA. Unresolved and overdue: breached.
    Rejected, unresolved-but-not-yet-due, and rows without an SLA are excluded."""
    mask = np.zeros(len(rows), dtype=bool)
    y = np.zeros(len(rows), dtype=np.int8)
    for i, r in enumerate(rows):
        if not r.sla_hours or r.status == "rejected":
            continue
        if r.resolution_hours is not None and r.resolved_at is not None:
            mask[i], y[i] = True, int(r.resolution_hours > r.sla_hours)
        elif end_hours - hours[i] > r.sla_hours:
            mask[i], y[i] = True, 1
    return mask, y


def fit_encoders(rows: list[Row], idx: np.ndarray, y: np.ndarray) -> BreachEncoders:
    enc = BreachEncoders(global_rate=float(y[idx].mean()))
    w_sum: dict[int, list[float]] = {}
    p_sum: dict[tuple[int, int], list[float]] = {}
    for i in idx:
        r = rows[i]
        c = CATEGORIES.index(r.label) if r.label in CATEGORIES else -1
        a = w_sum.setdefault(r.ward_id or 0, [0.0, 0.0]); a[0] += y[i]; a[1] += 1
        b = p_sum.setdefault((r.ward_id or 0, c), [0.0, 0.0]); b[0] += y[i]; b[1] += 1
    g = enc.global_rate
    enc.ward = {k: (v[0] + SMOOTH * g) / (v[1] + SMOOTH) for k, v in w_sum.items()}
    enc.pair = {k: (v[0] + SMOOTH * enc.ward.get(k[0], g)) / (v[1] + SMOOTH) for k, v in p_sum.items()}
    return enc


def hours_to_datetime(h: float) -> datetime:
    return (EPOCH + timedelta(hours=float(h))).astimezone(timezone.utc)


FEATURE_NAMES = (
    ["sla_hours", "ward_rate", "pair_rate", "month_sin", "month_cos", "weekday", "hour", "monsoon", "log_len", "rule_urgency",
     "recent_same_7d", "recent_ward_7d"]
    + [f"cat_{c}" for c in CATEGORIES] + [f"lang_{x}" for x in LANGS] + [f"chan_{c}" for c in CHANNELS]
)


def build_features(rows: list[Row], enc: BreachEncoders, hours: np.ndarray | None = None,
                   recent_same: np.ndarray | None = None, recent_ward: np.ndarray | None = None) -> np.ndarray:
    n = len(rows)
    hours = to_hours([r.submitted_at for r in rows]) if hours is None else hours
    if recent_same is None or recent_ward is None:
        ward = np.array([r.ward_id or 0 for r in rows])
        cat = np.array([CATEGORIES.index(r.label) if r.label in CATEGORIES else -1 for r in rows])
        recent_same = recent_counts(group_codes(ward, cat), hours)
        recent_ward = recent_counts(ward, hours)
    X = np.zeros((n, len(FEATURE_NAMES)), dtype=np.float32)
    for i, r in enumerate(rows):
        local = r.submitted_at.astimezone(IST)
        c = CATEGORIES.index(r.label) if r.label in CATEGORIES else -1
        base = [
            float(r.sla_hours or 72),
            enc.ward.get(r.ward_id or 0, enc.global_rate),
            enc.pair.get((r.ward_id or 0, c), enc.ward.get(r.ward_id or 0, enc.global_rate)),
            math.sin(2 * math.pi * local.month / 12), math.cos(2 * math.pi * local.month / 12),
            float(local.weekday()), float(local.hour), 1.0 if 6 <= local.month <= 9 else 0.0,
            math.log1p(len(r.text)), urgency_score(r.text, r.label).score,
            float(recent_same[i]), float(recent_ward[i]),
        ]
        onehot = [0.0] * (len(CATEGORIES) + len(LANGS) + len(CHANNELS))
        if c >= 0:
            onehot[c] = 1.0
        if r.language in LANGS:
            onehot[len(CATEGORIES) + LANGS.index(r.language)] = 1.0
        if r.channel in CHANNELS:
            onehot[len(CATEGORIES) + len(LANGS) + CHANNELS.index(r.channel)] = 1.0
        X[i] = base + onehot
    return X


def train_breach(rows: list[Row], test_fraction: float = 0.2, seed: int = 42) -> tuple[BreachModel, dict[str, Any]]:
    """Chronological split: fit on the earliest (1 - test_fraction) of usable complaints, test on the latest."""
    hours = to_hours([r.submitted_at for r in rows])
    end = float(hours.max()) if len(hours) else 0.0
    mask, y = eligible_labels(rows, end, hours)
    idx = np.where(mask)[0]
    idx = idx[np.argsort(hours[idx], kind="stable")]
    cut = int(len(idx) * (1 - test_fraction))
    train_i, test_i = idx[:cut], idx[cut:]
    enc = fit_encoders(rows, train_i, y)
    ward = np.array([r.ward_id or 0 for r in rows])
    cat = np.array([CATEGORIES.index(r.label) if r.label in CATEGORIES else -1 for r in rows])
    rs, rw = recent_counts(group_codes(ward, cat), hours), recent_counts(ward, hours)
    X = build_features(rows, enc, hours, rs, rw)
    clf = HistGradientBoostingClassifier(max_depth=4, max_iter=150, learning_rate=0.08, random_state=seed)
    clf.fit(X[train_i], y[train_i])
    p = clf.predict_proba(X[test_i])[:, 1]
    # baseline: the training breach rate of each category only
    cat_rate = {c: y[train_i][cat[train_i] == c].mean() for c in set(cat[train_i])}
    pb = np.array([cat_rate.get(c, y[train_i].mean()) for c in cat[test_i]])
    metrics = {
        "n_train": int(len(train_i)), "n_test": int(len(test_i)), "base_rate_test": float(y[test_i].mean()),
        "roc_auc": float(roc_auc_score(y[test_i], p)), "pr_auc": float(average_precision_score(y[test_i], p)),
        "accuracy@0.5": float(accuracy_score(y[test_i], p >= 0.5)), "brier": float(brier_score_loss(y[test_i], p)),
        "baseline_category_rate": {"roc_auc": float(roc_auc_score(y[test_i], pb)), "brier": float(brier_score_loss(y[test_i], pb))},
        "split": "chronological (train = earliest 80%, test = latest 20%)",
        "test_period_starts": (hours_to_datetime(hours[test_i[0]]).isoformat() if len(test_i) else None),
    }
    return BreachModel(clf, enc, FEATURE_NAMES), metrics


