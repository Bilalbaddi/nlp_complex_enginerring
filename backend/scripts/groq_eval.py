"""Zero-shot complaint classification with a Groq model (third comparison point for M3), on a small stratified sample.

Usage (from backend/): python -m scripts.groq_eval [--n 200] [--batch 10]
Compares the LLM with the trained TF-IDF model on the SAME rows, against the generator's true label, and registers
the result in model_runs (name `category_groq_zeroshot`).
"""

import argparse
import json
import time

import numpy as np

from app.core.config import get_settings
from app.core.pii import strip_pii
from app.db.session import SessionLocal
from app.ingestion.normalize import CATEGORIES
from app.llm import service
from app.llm.base import LLMError
from app.ml import classifier as clf
from app.ml import registry
from app.ml.dataset import load_rows_from_csv


def classify_batch(texts: list[str]) -> list[tuple[str, float]]:
    items = "\n".join(f"{i + 1}. {strip_pii(t)[:400]}" for i, t in enumerate(texts))
    prompt = service.load_prompt("zero_shot_category").format(categories=", ".join(CATEGORIES), items=items)
    r = service.generate("zero_shot_category", prompt, kind="fast", json_mode=True, max_tokens=1500, cache_ttl=7 * 86400)
    out: dict[int, tuple[str, float]] = {}
    for x in json.loads(r.text).get("results", []):
        c = str(x.get("category", "")).strip()
        out[int(x["i"])] = (c if c in CATEGORIES else "unknown", float(x.get("confidence", 0.5)))
    return [out.get(i + 1, ("unknown", 0.0)) for i in range(len(texts))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()
    s = get_settings()
    print(f"provider={service.get_provider().name} model={service.model_for('fast')}", flush=True)

    rows = [r for r in load_rows_from_csv() if r.true_category in CATEGORIES]
    rng = np.random.default_rng(7)
    per = max(1, args.n // len(CATEGORIES))
    sample = []
    for c in CATEGORIES:
        pool = [r for r in rows if r.true_category == c]
        sample += [pool[i] for i in rng.choice(len(pool), min(per, len(pool)), replace=False)]
    print(f"{len(sample)} sampled complaints", flush=True)

    t0 = time.time()
    preds: list[tuple[str, float]] = []
    for i in range(0, len(sample), args.batch):
        try:
            preds += classify_batch([r.text for r in sample[i : i + args.batch]])
        except (LLMError, json.JSONDecodeError, KeyError, ValueError) as exc:
            print(f"  batch {i} failed: {exc}", flush=True)
            preds += [("unknown", 0.0)] * len(sample[i : i + args.batch])
        print(f"  {min(i + args.batch, len(sample))}/{len(sample)}", flush=True)
    secs = time.time() - t0

    truth = [r.true_category or "" for r in sample]
    pred = [p[0] for p in preds]
    labels = list(CATEGORIES)
    llm_m = clf.evaluate(truth, pred, labels)
    with SessionLocal() as db:
        loaded = registry.load_active(db, "category_tfidf_lr")
    assert loaded is not None, "train the TF-IDF model first"
    tfidf = loaded[1]
    tp, _ = tfidf.predict([r.text for r in sample])
    tf_m = clf.evaluate(truth, tp, labels)
    print(f"Groq zero-shot : acc {llm_m['accuracy']:.3f} macro-F1 {llm_m['macro_f1']:.3f}")
    print(f"TF-IDF + LR    : acc {tf_m['accuracy']:.3f} macro-F1 {tf_m['macro_f1']:.3f}")
    metrics = {"vs_true_label": llm_m, "tfidf_same_rows": tf_m, "n": len(sample), "unknown": pred.count("unknown"),
               "by_language_vs_true": clf.accuracy_by([r.language for r in sample], truth, pred),
               "seconds": round(secs, 1), "caveat": "small stratified sample to respect free-tier rate limits"}
    with SessionLocal() as db:
        registry.save_run(db, name="category_groq_zeroshot", task="category", metrics=metrics,
                          params={"model": service.model_for("fast"), "provider": service.get_provider().name,
                                  "batch": args.batch, "prompt": "prompts/zero_shot_category.txt"},
                          n_test=len(sample), train_seconds=secs)
    print("registered category_groq_zeroshot")


if __name__ == "__main__":
    main()
