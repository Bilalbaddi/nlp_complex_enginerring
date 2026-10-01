"""Model training and prediction on generated data. Uses the offline TF-IDF path only; no network, no database."""

from functools import lru_cache

import numpy as np
import pytest

from app.ml import breach as breach_ml
from app.ml import classifier as clf
from app.ml import duplicates as dup
from app.ml.features import group_codes, recent_counts, to_hours
from app.ml.predict import Predictor
from tests.ml_helpers import rows

N = 3000


@lru_cache
def trained() -> tuple[clf.CategoryModel, list, list]:
    r = rows(N)
    train, test = r[:2400], r[2400:]
    return clf.train_tfidf_lr([x.text for x in train], [x.label for x in train]), train, test


def test_recent_counts_matches_brute_force() -> None:
    rng = np.random.default_rng(0)
    group = rng.integers(0, 5, 300)
    hours = np.sort(rng.uniform(0, 600, 300))
    got = recent_counts(group, hours, window=100.0)
    for i in range(300):
        want = sum(1 for j in range(300) if group[j] == group[i] and j < i and hours[i] - 100.0 <= hours[j] <= hours[i])
        assert got[i] == want, i
    assert recent_counts(np.zeros(0, int), np.zeros(0)).size == 0


def test_group_codes_distinguish_combinations() -> None:
    a, b = np.array([0, 0, 1, 1]), np.array([0, 1, 0, 1])
    assert len(set(group_codes(a, b).tolist())) == 4


def test_candidate_pairs_match_brute_force() -> None:
    rng = np.random.default_rng(1)
    ward = rng.integers(0, 4, 200)
    hours = np.sort(rng.uniform(0, 500, 200))
    i, j = dup.candidate_pairs(ward, hours, 72.0)
    got = set(zip(i.tolist(), j.tolist()))
    want = {(a, b) for b in range(200) for a in range(b) if ward[a] == ward[b] and hours[b] - hours[a] <= 72.0}
    assert got == want


def test_tfidf_classifier_generalises_to_true_labels_in_all_languages() -> None:
    model, _, test = trained()
    pred, conf = model.predict([t.text for t in test])
    m = clf.evaluate([t.true_category for t in test], pred, model.classes)
    assert m["accuracy"] > 0.93 and m["macro_f1"] > 0.9
    assert np.array(m["confusion_matrix"]).sum() == len(test) and len(m["labels"]) == 10
    by = clf.accuracy_by([t.language for t in test], [t.true_category for t in test], pred)
    assert set(by) == {"en", "hi", "hinglish", "mr"} and min(v["accuracy"] for v in by.values()) > 0.85
    assert ((conf > 0) & (conf <= 1)).all()


def test_confidence_is_informative_for_review_routing() -> None:
    model, _, test = trained()
    pred, conf = model.predict([t.text for t in test])
    correct = np.array([t.true_category == p for t, p in zip(test, pred)])
    rep = clf.confidence_report(conf, correct, 0.6)
    assert rep["accuracy_unflagged"] >= (rep["accuracy_flagged"] or 0)  # low-confidence items really are less accurate
    assert sum(b["n"] for b in rep["bins"]) == len(test)


def test_label_noise_does_not_hurt_true_label_accuracy_much() -> None:
    """Trained on 3%-noisy recorded labels, the model still matches the TRUE category as well as the recorded one."""
    model, _, test = trained()
    pred, _ = model.predict([t.text for t in test])
    vs_true = np.mean([t.true_category == p for t, p in zip(test, pred)])
    vs_label = np.mean([t.label == p for t, p in zip(test, pred)])
    assert vs_true >= vs_label - 0.005


def test_breach_model_beats_category_baseline_on_a_chronological_split() -> None:
    model, m = breach_ml.train_breach(rows(N))
    assert m["n_train"] > m["n_test"] > 100 and 0 < m["base_rate_test"] < 1
    assert m["roc_auc"] > 0.7 and m["roc_auc"] >= m["baseline_category_rate"]["roc_auc"] - 0.01
    X = breach_ml.build_features(rows(N)[:5], model.enc)
    assert X.shape == (5, len(breach_ml.FEATURE_NAMES)) and ((model.predict(X) >= 0) & (model.predict(X) <= 1)).all()


def test_breach_labels_exclude_rejected_and_not_yet_due() -> None:
    r = rows(N)
    hours = to_hours([x.submitted_at for x in r])
    mask, y = breach_ml.eligible_labels(r, float(hours.max()), hours)
    assert not any(mask[i] for i, x in enumerate(r) if x.status == "rejected")
    assert mask.sum() > 0.85 * len(r) and 0.1 < y[mask].mean() < 0.9


@lru_cache
def dup_model() -> tuple[dup.DuplicateModel, dict, clf.CategoryModel]:
    model, train, _ = trained()
    allrows = rows(N)
    corpus = dup.build_corpus(allrows, model.estimator.named_steps["tfidf"], None)
    m, metrics = dup.train_and_evaluate(corpus, use_emb=False)
    return m, metrics, model


def test_duplicate_detector_finds_planted_duplicates() -> None:
    _, metrics, _ = dup_model()
    assert metrics["true_pairs"] >= 60 and metrics["candidate_pairs"] > 1000
    assert metrics["f1"] > 0.7 and metrics["precision"] > 0.6
    assert metrics["f1"] >= metrics["baseline_text_only"]["f1"]  # space+time features add value over text similarity alone
    assert metrics["recall_cross_language"] is not None


def test_predictor_enrich_end_to_end() -> None:
    m_dup, _, cat = dup_model()
    r = rows(N)
    breach_model, _ = breach_ml.train_breach(r)
    p = Predictor(category=cat, breach=breach_model, duplicates=m_dup, duplicates_text=m_dup, ward_names=["Old City"],
                  dept_by_category={c: i for i, c in enumerate(cat.classes, start=1)})
    out = p.enrich(r[:1200])
    assert len(out) == 1200 and all(0 <= e.confidence <= 1 and 0 <= e.urgency <= 100 for e in out)
    assert all(e.predicted_department_id == cat.classes.index(e.predicted_category) + 1 for e in out)
    assert {e.language for e in out} <= {"en", "hi", "mr", "hinglish"}
    assert any(e.entities["landmarks"] or e.entities["roads"] for e in out)
    # planted duplicates among the first 1200 complaints are linked to the right original
    ids = {x.external_id: x.id for x in r[:1200]}
    planted = {x.id: ids[x.true_dup_of] for x in r[:1200] if x.true_dup_of in ids}
    found = {e.id: e.duplicate_of_id for e in out if e.duplicate_of_id}
    hit = sum(1 for k, v in planted.items() if found.get(k) == v)
    assert planted and hit / len(planted) > 0.6
    assert sum(1 for k, v in found.items() if planted.get(k) != v) < 0.1 * len(out)  # few false links
    # review flag: low confidence or a confident disagreement with the recorded label
    flagged = [e for e in out if e.needs_review]
    assert all(e.review_reason for e in flagged) and all(not e.review_reason for e in out if not e.needs_review)
    # breach risk feeds urgency: the same complaint scores higher with breach risk than without
    no_breach = Predictor(category=cat, ward_names=[]).enrich(r[:50], detect_duplicates=False)
    with_breach = Predictor(category=cat, breach=breach_model, ward_names=[]).enrich(r[:50], detect_duplicates=False)
    assert sum(w.urgency for w in with_breach) > sum(n.urgency for n in no_breach)
