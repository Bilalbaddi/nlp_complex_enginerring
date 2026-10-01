"""Near-duplicate complaint detection.

Candidates are earlier complaints in the same ward within 72 hours (a tiny set per complaint). A gradient-boosted pair
classifier scores each candidate pair from: TF-IDF text similarity, embedding cosine similarity (when both complaints
have a stored multilingual embedding), distance in metres, time gap, and whether category/language match. Each complaint
is linked to its best earlier match above a tuned threshold.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import precision_recall_fscore_support
from sklearn.preprocessing import normalize

from app.ml.dataset import Row
from app.ml.features import to_hours

MAX_HOURS = 72.0
FEATURES = ["cos_text", "cos_emb", "has_emb", "log_dist_m", "dt_hours", "same_cat", "same_lang"]


def candidate_pairs(ward: np.ndarray, hours: np.ndarray, max_hours: float = MAX_HOURS) -> tuple[np.ndarray, np.ndarray]:
    """All (i, j) with i earlier than j, same ward and hours[j] - hours[i] <= max_hours."""
    n = len(ward)
    order = np.lexsort((np.arange(n), hours, ward))
    ws, hs = ward[order], hours[order]
    big = 1e7
    key = ws.astype(np.float64) * big + hs
    lo = np.searchsorted(key, key - max_hours, side="left")
    pos = np.arange(n)
    counts = pos - lo
    j_sorted = np.repeat(pos, counts)
    i_sorted = np.concatenate([np.arange(l, p) for l, p in zip(lo, pos) if p > l]) if counts.sum() else np.zeros(0, int)
    return order[i_sorted], order[j_sorted]


def haversine_m(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * np.arcsin(np.sqrt(a))


def row_cosine(X: sparse.csr_matrix, i: np.ndarray, j: np.ndarray, chunk: int = 200_000) -> np.ndarray:
    out = np.empty(len(i), dtype=np.float32)
    for s in range(0, len(i), chunk):
        out[s : s + chunk] = np.asarray(X[i[s : s + chunk]].multiply(X[j[s : s + chunk]]).sum(axis=1)).ravel()
    return out


@dataclass
class Corpus:
    """Arrays for one batch of complaints, aligned with `rows`."""

    rows: list[Row]
    X: sparse.csr_matrix  # L2-normalised text vectors (rows have unit length)
    emb: np.ndarray | None  # (n, dim), NaN rows where missing
    ward: np.ndarray
    hours: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    cat: np.ndarray
    lang: np.ndarray


def build_corpus(rows: list[Row], tfidf: Any, emb: np.ndarray | None = None) -> Corpus:
    X = normalize(sparse.csr_matrix(tfidf.transform([r.text for r in rows])))  # unit rows: dot product = cosine
    cats = {c: k for k, c in enumerate(sorted({r.label or "" for r in rows}))}
    langs = {c: k for k, c in enumerate(sorted({r.language or "" for r in rows}))}
    return Corpus(
        rows, X.tocsr(), emb, np.array([r.ward_id or 0 for r in rows]), to_hours([r.submitted_at for r in rows]),
        np.array([r.lat if r.lat is not None else np.nan for r in rows]), np.array([r.lon if r.lon is not None else np.nan for r in rows]),
        np.array([cats[r.label or ""] for r in rows]), np.array([langs[r.language or ""] for r in rows]),
    )


def pair_features(c: Corpus, i: np.ndarray, j: np.ndarray, use_emb: bool) -> np.ndarray:
    F = np.zeros((len(i), len(FEATURES)), dtype=np.float32)
    F[:, 0] = row_cosine(c.X, i, j)
    if use_emb and c.emb is not None:
        both = ~(np.isnan(c.emb[i, 0]) | np.isnan(c.emb[j, 0]))
        F[both, 1] = (c.emb[i[both]] * c.emb[j[both]]).sum(axis=1)
        F[:, 2] = both
    d = haversine_m(c.lat[i], c.lon[i], c.lat[j], c.lon[j])
    F[:, 3] = np.log1p(np.nan_to_num(d, nan=5000.0))
    F[:, 4] = c.hours[j] - c.hours[i]
    F[:, 5] = c.cat[i] == c.cat[j]
    F[:, 6] = c.lang[i] == c.lang[j]
    return F


def gate(F: np.ndarray) -> np.ndarray:
    """Hard constraint applied before the model: a duplicate must be about the same kind of problem, i.e. share the
    category, or have clearly similar text or (when available) similar multilingual embeddings. Without it, two unrelated
    complaints filed at the same spot and time would look like duplicates."""
    return (F[:, 5] == 1) | (F[:, 0] >= 0.2) | ((F[:, 2] == 1) & (F[:, 1] >= 0.6))


@dataclass
class DuplicateModel:
    clf: Any
    threshold: float
    use_emb: bool
    feature_names: list[str]

    def score(self, F: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(F)[:, 1] * gate(F)


def pair_labels(rows: list[Row], i: np.ndarray, j: np.ndarray) -> np.ndarray:
    ext = [r.external_id for r in rows]
    dup_of = [r.true_dup_of for r in rows]
    return np.array([dup_of[b] is not None and dup_of[b] == ext[a] for a, b in zip(i, j)], dtype=np.int8)


def best_threshold(y: np.ndarray, p: np.ndarray) -> float:
    best, best_f = 0.5, -1.0
    for t in np.linspace(0.05, 0.95, 91):
        pr, rc, f, _ = precision_recall_fscore_support(y, p >= t, average="binary", zero_division=0)
        if f > best_f:
            best, best_f = float(t), float(f)
    return best


def detect(model: DuplicateModel, c: Corpus) -> dict[int, tuple[int, float]]:
    """For each complaint index j: (best earlier match i, probability) when the probability reaches the threshold."""
    i, j = candidate_pairs(c.ward, c.hours)
    if len(i) == 0:
        return {}
    p = model.score(pair_features(c, i, j, model.use_emb))
    keep = p >= model.threshold
    best: dict[int, tuple[int, float]] = {}
    for a, b, pr in zip(i[keep], j[keep], p[keep]):
        if int(b) not in best or pr > best[int(b)][1]:
            best[int(b)] = (int(a), float(pr))
    return best


def train_and_evaluate(c: Corpus, use_emb: bool, seed: int = 42) -> tuple[DuplicateModel, dict[str, Any]]:
    """Needs ground-truth duplicate links on the rows (synthetic data). Splits candidate pairs by the later complaint."""
    i, j = candidate_pairs(c.ward, c.hours)
    y = pair_labels(c.rows, i, j)
    rng = np.random.default_rng(seed)
    in_test = rng.random(len(c.rows)) < 0.3  # split by the later complaint so a duplicate is never in both sets
    tr, te = ~in_test[j], in_test[j]
    F = pair_features(c, i, j, use_emb)
    g = gate(F)
    clf = HistGradientBoostingClassifier(max_depth=4, max_iter=120, learning_rate=0.1, random_state=seed)
    clf.fit(F[tr & g], y[tr & g])
    p = clf.predict_proba(F)[:, 1] * g  # pairs rejected by the gate can never be duplicates
    p_tr, p_te = p[tr], p[te]
    thr = best_threshold(y[tr], p_tr)
    pr, rc, f1, _ = precision_recall_fscore_support(y[te], p_te >= thr, average="binary", zero_division=0)
    same_lang = F[te][:, 6] == 1
    pos = y[te] == 1

    def recall_of(mask: np.ndarray) -> float | None:
        m = pos & mask
        return float((p_te[m] >= thr).mean()) if m.any() else None

    # rule baseline: text similarity only
    tb = best_threshold(y[tr], F[tr][:, 0])
    bp, br, bf, _ = precision_recall_fscore_support(y[te], F[te][:, 0] >= tb, average="binary", zero_division=0)
    metrics = {
        "uses_embeddings": use_emb, "candidate_pairs": int(len(i)), "true_pairs": int(y.sum()), "threshold": thr,
        "precision": float(pr), "recall": float(rc), "f1": float(f1),
        "recall_same_language": recall_of(same_lang), "recall_cross_language": recall_of(~same_lang),
        "baseline_text_only": {"threshold": tb, "precision": float(bp), "recall": float(br), "f1": float(bf)},
        "split": "by later complaint: 70% train / 30% test",
        "feature_names": FEATURES,
    }
    metrics["gate_recall_ceiling"] = float(g[y == 1].mean()) if y.any() else None  # true pairs the gate lets through
    final = HistGradientBoostingClassifier(max_depth=4, max_iter=120, learning_rate=0.1, random_state=seed).fit(F[g], y[g])
    return DuplicateModel(final, thr, use_emb, FEATURES), metrics
