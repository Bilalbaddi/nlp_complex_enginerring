"""Train and evaluate every Phase 3 model, register them, and write predictions for all complaints.

Usage (from backend/, or `python scripts/tasks.py train [options]`):
    python -m scripts.train                   # everything
    python -m scripts.train --no-embeddings   # skip the model that needs Hugging Face embeddings
    python -m scripts.train --no-predict      # train only; do not write predictions to the database

Data comes from the local generated CSVs (the same records that were seeded), so nothing is bulk-downloaded from the
remote database; predictions are uploaded by external id. Training uses the category recorded on each complaint (about
3% noisy, like real data); metrics are reported against BOTH the recorded label and the generator's true label.
Embeddings come from the Hugging Face Inference API for a stratified sample (nothing is downloaded locally).
"""

import argparse
import csv
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.model_selection import train_test_split

from app.core.config import ROOT_DIR, get_settings
from app.db.session import SessionLocal
from app.ingestion.normalize import CATEGORIES
from app.ml import breach as breach_ml
from app.ml import classifier as clf
from app.ml import duplicates as dup
from app.ml import enrich, registry
from app.ml.dataset import Row, load_rows_from_csv, with_retry
from app.nlp.embeddings import HashingEmbedder, get_embedder
from app.nlp.entities import extract_entities
from app.nlp.hf_client import HFError
from app.nlp.language import detect_language
from app.nlp.sentiment import label as sentiment_label
from app.nlp.sentiment import sentiment
from scripts.datagen import constants as K

SEED = 42
EMB_TRAIN, EMB_TEST = 3000, 1000  # complaints embedded through the HF API for the embedding classifier


def save_run(**kw: Any) -> None:
    """Register a model in its own session, retrying when the remote pooler drops the connection."""

    def once() -> None:
        with SessionLocal() as db:
            registry.save_run(db, **kw)

    with_retry(once)


def log(msg: str) -> None:
    print(msg, flush=True)


def fmt(m: dict[str, Any]) -> str:
    return f"acc {m['accuracy']:.4f}  macro-F1 {m['macro_f1']:.4f}  (P {m['macro_precision']:.4f} R {m['macro_recall']:.4f})"


def report_model(name: str, classes: list[str], test_rows: list[Row], pred: list[str], conf: np.ndarray) -> dict[str, Any]:
    out: dict[str, Any] = {"vs_recorded_label": clf.evaluate([r.label for r in test_rows], pred, classes)}
    truth = [r.true_category for r in test_rows]
    if all(truth):
        out["vs_true_label"] = clf.evaluate(truth, pred, classes)
        out["by_language_vs_true"] = clf.accuracy_by([r.language for r in test_rows], truth, pred)
        out["confidence"] = clf.confidence_report(conf, np.array([t == p for t, p in zip(truth, pred)]), get_settings().review_confidence_threshold)
        log(f"  {name:<34} vs true label: {fmt(out['vs_true_label'])}")
    log(f"  {name:<34} vs recorded  : {fmt(out['vs_recorded_label'])}")
    return out


def train_categories(rows: list[Row], use_embeddings: bool, hashing: bool) -> dict[str, Any]:
    idx = np.arange(len(rows))
    tr, te = train_test_split(idx, test_size=0.2, random_state=SEED, stratify=[r.label for r in rows])
    train_rows, test_rows = [rows[i] for i in tr], [rows[i] for i in te]
    classes = sorted({r.label for r in rows if r.label})
    log(f"\n[1/5] Category classification: {len(train_rows):,} train / {len(test_rows):,} test")

    t0 = time.time()
    tfidf = clf.train_tfidf_lr([r.text for r in train_rows], [r.label for r in train_rows])
    secs = time.time() - t0
    p_tfidf = tfidf.predict_proba([r.text for r in test_rows])
    results: dict[str, Any] = {"tfidf_lr": report_model("TF-IDF + logistic regression", classes, test_rows,
                                                        [tfidf.classes[k] for k in p_tfidf.argmax(1)], p_tfidf.max(1))}
    results["tfidf_lr"]["train_seconds"] = round(secs, 1)

    emb_model = None
    if use_embeddings and get_settings().hf_configured:
        try:
            embedder = get_embedder("hf")
            s_tr = np.random.default_rng(SEED).choice(tr, EMB_TRAIN, replace=False)
            s_te = te[:EMB_TEST]
            t0 = time.time()
            x_tr, x_te = embedder.embed([rows[i].text for i in s_tr]), embedder.embed([rows[i].text for i in s_te])
            embed_secs = time.time() - t0
            emb_model = clf.train_embedding_lr(x_tr, [rows[i].label for i in s_tr])
            sub_rows = [rows[i] for i in s_te]
            p_e = emb_model.predict_proba(x_te)
            results["embedding_lr"] = report_model(f"HF embeddings + LR (n={EMB_TRAIN})", classes, sub_rows,
                                                   [emb_model.classes[k] for k in p_e.argmax(1)], p_e.max(1))
            results["embedding_lr"].update(embedding_model=get_settings().embedding_model, embedding_seconds=round(embed_secs, 1), n_train=EMB_TRAIN)
            p_t = tfidf.predict_proba([r.text for r in sub_rows])
            results["tfidf_same_rows"] = report_model("TF-IDF on the same test rows", classes, sub_rows,
                                                      [tfidf.classes[k] for k in p_t.argmax(1)], p_t.max(1))
            p_en = 0.5 * p_t + 0.5 * p_e
            results["ensemble"] = report_model("Ensemble (TF-IDF + embeddings)", classes, sub_rows,
                                               [classes[k] for k in p_en.argmax(1)], p_en.max(1))
            # deployable embedding model: refit on the whole embedded sample
            emb_model = clf.train_embedding_lr(np.vstack([x_tr, x_te]), [rows[i].label for i in list(s_tr) + list(s_te)])
        except HFError as exc:
            log(f"  Hugging Face embeddings unavailable ({exc}); embedding classifier skipped")
    if hashing:
        t0 = time.time()
        hemb = HashingEmbedder().embed([r.text for r in rows])
        h_model = clf.train_embedding_lr(hemb[tr], [rows[i].label for i in tr])
        p_h = h_model.predict_proba(hemb[te])
        results["hashing_lr"] = report_model("Hashing embeddings + LR (offline)", classes, test_rows,
                                             [h_model.classes[k] for k in p_h.argmax(1)], p_h.max(1))
        results["hashing_lr"]["train_seconds"] = round(time.time() - t0, 1)

    log("  refitting the deployable TF-IDF model on all data ...")
    final_tfidf = clf.train_tfidf_lr([r.text for r in rows], [r.label for r in rows])
    return {"results": results, "tfidf": final_tfidf, "embedding": emb_model, "n_train": len(train_rows), "n_test": len(test_rows)}


def eval_sentiment(feedback_csv: Path) -> dict[str, Any]:
    log("\n[3/5] Sentiment (citizen feedback; the star rating is an independent label)")
    with feedback_csv.open(encoding="utf-8") as f:
        fb = [r for r in csv.DictReader(f) if r["text"]]
    bucket = lambda r: "negative" if int(r) <= 2 else ("neutral" if int(r) == 3 else "positive")  # noqa: E731
    truth = [bucket(r["rating"]) for r in fb]
    pred = [sentiment_label(sentiment(r["text"], r["language"])) for r in fb]
    m = clf.evaluate(truth, pred, ["negative", "neutral", "positive"])
    polar = [(t, p) for t, p in zip(truth, pred) if t != "neutral"]
    m["polarity_accuracy_excluding_neutral"] = float(np.mean([t == p for t, p in polar]))
    m["by_language"] = clf.accuracy_by([r["language"] for r in fb], truth, pred)
    m["caveat"] = "synthetic templated feedback text; expect lower accuracy on real free text"
    log(f"  3-class accuracy {m['accuracy']:.3f}; polarity {m['polarity_accuracy_excluding_neutral']:.3f}; n={len(fb):,}")
    return m


def eval_entities(rows: list[Row], n: int = 5000) -> dict[str, Any]:
    log("\n[4/5] Entity extraction (landmarks, roads, areas)")
    strip = lambda x: re.sub(r"\s*\{k\}", "", x)  # noqa: E731
    pool = [strip(x) for x in K.LANDMARK_POOL] + [strip(x) for x in K.STREET_POOL]
    known = pool + K.WARD_NAMES
    hit = tot = extracted = spurious = 0
    for r in rows[:n]:
        e = extract_entities(r.text, K.WARD_NAMES)
        got = e["landmarks"] + e["roads"] + e["areas"]
        truth = [p for p in pool if p.lower() in r.text.lower()]
        tot += len(truth)
        hit += sum(any(p.lower() in g.lower() for g in got) for p in truth)
        extracted += len(got)
        spurious += sum(not any(re.sub(r"\s*\d+", "", k).strip().lower() in g.lower() for k in known) for g in got)
    m = {"n_complaints": min(n, len(rows)), "recall": hit / max(tot, 1), "precision": 1 - spurious / max(extracted, 1),
         "true_mentions": tot, "extracted": extracted,
         "caveat": "recall misses lower-cased (sloppily typed) place names; precision is against the known gazetteer"}
    log(f"  recall {m['recall']:.3f}, precision {m['precision']:.3f} ({tot:,} true mentions)")
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-embeddings", action="store_true")
    ap.add_argument("--no-hashing", action="store_true", help="skip the offline hashing-embedding baseline")
    ap.add_argument("--no-predict", action="store_true")
    args = ap.parse_args()
    t_all = time.time()
    rows = [r for r in load_rows_from_csv() if r.label in CATEGORIES]
    log(f"Loaded {len(rows):,} labelled complaints from data/generated/complaints.csv")

    cat = train_categories(rows, not args.no_embeddings, not args.no_hashing)
    results = cat["results"]

    log("\n[2/5] SLA-breach risk (chronological split) and near-duplicate detection")
    t0 = time.time()
    breach_model, breach_metrics = breach_ml.train_breach(rows)
    bsecs = time.time() - t0
    log(f"  breach risk: ROC-AUC {breach_metrics['roc_auc']:.3f} (category-only baseline "
        f"{breach_metrics['baseline_category_rate']['roc_auc']:.3f}), PR-AUC {breach_metrics['pr_auc']:.3f}, base rate {breach_metrics['base_rate_test']:.2f}")
    corpus = dup.build_corpus(rows, cat["tfidf"].estimator.named_steps["tfidf"], None)
    dup_model, dup_metrics = dup.train_and_evaluate(corpus, use_emb=False)
    log(f"  duplicates: P {dup_metrics['precision']:.3f} R {dup_metrics['recall']:.3f} F1 {dup_metrics['f1']:.3f} "
        f"(text-similarity rule alone: F1 {dup_metrics['baseline_text_only']['f1']:.3f}; cross-language recall {dup_metrics['recall_cross_language']})")

    sent_metrics = eval_sentiment(ROOT_DIR / "data" / "generated" / "feedback.csv")
    ent_metrics = eval_entities(rows)
    log("\n[5/5] Language detection")
    lang = clf.evaluate([r.language or "" for r in rows], [detect_language(r.text) for r in rows], ["en", "hi", "mr", "hinglish"])
    log(f"  {fmt(lang)}")

    log("\nRegistering models ...")
    with SessionLocal() as db:
        save_run( name="category_tfidf_lr", task="category", metrics=results["tfidf_lr"], artifact=cat["tfidf"],
                          params={"features": "word 1-2gram + char_wb 2-5gram TF-IDF", "C": 10, "classes": cat["tfidf"].classes},
                          n_train=cat["n_train"], n_test=cat["n_test"], train_seconds=results["tfidf_lr"]["train_seconds"], activate=True)
        if cat["embedding"] is not None:
            save_run( name="category_embedding_lr", task="category", metrics=results["embedding_lr"], artifact=cat["embedding"],
                              params={"embedding_model": get_settings().embedding_model, "C": 10, "trained_on": "embedded sample"},
                              n_train=EMB_TRAIN, n_test=EMB_TEST, train_seconds=results["embedding_lr"]["embedding_seconds"], activate=True)
            save_run( name="category_ensemble", task="category", metrics=results["ensemble"],
                              params={"combination": "0.5 * tfidf_lr + 0.5 * embedding_lr (probability average)"})
        if "hashing_lr" in results:
            save_run( name="category_hashing_lr", task="category", metrics=results["hashing_lr"],
                              params={"note": "offline baseline; vectors are not stored"}, train_seconds=results["hashing_lr"]["train_seconds"])
        save_run( name="breach_risk", task="breach_risk", metrics=breach_metrics, artifact=breach_model,
                          params={"model": "HistGradientBoostingClassifier", "features": breach_model.feature_names},
                          n_train=breach_metrics["n_train"], n_test=breach_metrics["n_test"], train_seconds=bsecs, activate=True)
        save_run( name="duplicates_text_only", task="duplicates", metrics=dup_metrics, artifact=dup_model,
                          params={"max_hours": dup.MAX_HOURS, "threshold": dup_model.threshold}, activate=True)
        save_run( name="sentiment_lexicon", task="sentiment", metrics=sent_metrics, params={"method": "VADER + multilingual lexicon"}, activate=True)
        save_run( name="entities_rules", task="entities", metrics=ent_metrics, params={"method": "gazetteer + type-keyword rules"}, activate=True)
        save_run( name="language_rules", task="language", metrics=lang, params={"method": "script + marker words"}, activate=True)
    enrich.reset_predictor_cache()

    if not args.no_predict:
        log("\nWriting predictions for every complaint (upload only) ...")
        with SessionLocal() as db:
            predictor = enrich.load_predictor(db)
        assert predictor is not None
        res = enrich.enrich_rows_and_save(predictor, rows, progress=lambda d, t: log(f"  saved {d:,}/{t:,}"))
        log(f"  {res}")
    log(f"\nAll done in {time.time() - t_all:.0f}s")


if __name__ == "__main__":
    main()
