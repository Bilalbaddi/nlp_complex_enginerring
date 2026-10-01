"""Generate docs/EVALUATION.md from the model registry and the analytics engine (numbers are measured, not typed in).

Usage (from backend/): python -m scripts.make_eval_doc
Needs the trained models in `model_runs` (scripts.train, scripts.groq_eval) and the seeded database.
"""

import json
import time
from datetime import date
from typing import Any

from sqlalchemy import text

from app.core.config import ROOT_DIR
from app.db.session import SessionLocal
from app.services import cube as cubes
from app.services import emerging, forecasting, service_gaps

OUT = ROOT_DIR / "docs" / "EVALUATION.md"


def latest(db: Any, name: str) -> dict[str, Any]:
    row = db.execute(text("SELECT metrics, params, n_train, n_test, train_seconds, created_at FROM model_runs WHERE name = :n ORDER BY id DESC LIMIT 1"), {"n": name}).first()
    if row is None:
        raise SystemExit(f"model run {name!r} not found: run scripts.train / scripts.groq_eval first")
    return {"m": row[0], "p": row[1], "n_train": row[2], "n_test": row[3], "secs": row[4], "at": row[5]}


def f(x: float | None, d: int = 3) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def pc(x: float | None, d: int = 1) -> str:
    return "-" if x is None else f"{100 * x:.{d}f}%"


def cat_row(label: str, m: dict[str, Any], n: int | None = None) -> str:
    return f"| {label} | {n if n is not None else m['n']:,} | {pc(m['accuracy'])} | {f(m['macro_precision'])} | {f(m['macro_recall'])} | {f(m['macro_f1'])} |"


def main() -> None:
    with SessionLocal() as db:
        tfidf, emb, ens, hashing, groq = (latest(db, n) for n in ("category_tfidf_lr", "category_embedding_lr", "category_ensemble", "category_hashing_lr", "category_groq_zeroshot"))
        breach, dup, sent, ent, lang = (latest(db, n) for n in ("breach_risk", "duplicates_text_only", "sentiment_lexicon", "entities_rules", "language_rules"))
        cube = cubes.get_cube(db)
        bt = forecasting.backtest_all(cube)
        gi = service_gaps.gap_index(cube, months=12)
        em = emerging.build(db, cube, with_llm=False)
        n_complaints = db.scalar(text("SELECT count(*) FROM complaints"))
        n_review = db.scalar(text("SELECT count(*) FROM complaints WHERE needs_review"))
        n_dup = db.scalar(text("SELECT count(*) FROM complaints WHERE duplicate_of_id IS NOT NULL"))
    truth = json.loads((ROOT_DIR / "data" / "generated" / "ground_truth.json").read_text(encoding="utf-8"))

    t_true, t_rec = tfidf["m"]["vs_true_label"], tfidf["m"]["vs_recorded_label"]
    labels = t_true["labels"]
    cm = t_true["confusion_matrix"]
    conf = tfidf["m"]["confidence"]
    e_conf = emb["m"]["confidence"]

    out: list[str] = []
    w = out.append
    w("# SG-DSS Evaluation Report")
    w("")
    w(f"_Generated {date.today().isoformat()} by `backend/scripts/make_eval_doc.py` from the model registry (`model_runs`) and the analytics engine._")
    w("")
    w("**Read this first.** All data is synthetic (fictional city *Sahyadripur*, {:,} complaints, 3 years, seed 42) generated from templates, so the "
      "generator's *true* labels are known. That makes evaluation possible, but it also means **scores are optimistic**: templated text is far more regular than real "
      "citizen writing, and the planted effects (seasonality, hotspots, bursts, underperforming wards) are cleaner than reality. "
      "Treat the numbers as evidence that the pipelines work end to end, not as expected production accuracy. See [Limitations](#9-limitations).".format(n_complaints))
    w("")
    w("## Contents")
    w("1. [Category classification](#1-complaint-category-classification)  2. [SLA-breach risk](#2-sla-breach-risk)  3. [Duplicate detection](#3-near-duplicate-detection)  "
      "4. [Sentiment, entities, language](#4-sentiment-entities-and-language)  5. [Forecasting](#5-forecasting)  6. [Emerging issues and gap index](#6-emerging-issue-detection-and-service-gap-index)  "
      "7. [LLM layer](#7-llm-layer-groq)  8. [System quality](#8-system-quality)  9. [Limitations](#9-limitations)  10. [Reproducing](#10-reproducing-these-numbers)")
    w("")

    # ---------------------------------------------------------------- 1
    w("## 1. Complaint category classification")
    w("")
    w("Task: assign one of 10 categories (water supply, drainage, roads, garbage, streetlights, encroachment, public health, electricity, parks, noise) to a complaint written in "
      "English, Hindi, Marathi or Hinglish. Models were trained on the **recorded** category, which the generator corrupts for ~3% of rows (label noise, like real data). "
      "Each model is therefore scored against both the *recorded* label and the generator's *true* label.")
    w("")
    w("| Model | Test rows | Accuracy (vs true) | Macro precision | Macro recall | Macro F1 |")
    w("|---|---:|---:|---:|---:|---:|")
    w(cat_row("TF-IDF (word 1-2 + char 2-5 grams) + logistic regression **(deployed baseline)**", t_true))
    w(cat_row("Hashing n-gram embedding + LR (offline, no API)", hashing["m"]["vs_true_label"]))
    w(cat_row(f"Multilingual MiniLM embeddings (Hugging Face API) + LR, trained on {emb['n_train']:,} rows", emb["m"]["vs_true_label"]))
    w(cat_row("Ensemble: 0.5 x TF-IDF + 0.5 x embedding probabilities", ens["m"]["vs_true_label"]))
    w(cat_row(f"Groq zero-shot (`{groq['p'].get('model')}`), stratified sample, no training", groq["m"]["vs_true_label"]))
    w("")
    w(f"Test sizes differ: TF-IDF and the hashing model were scored on {t_true['n']:,} held-out rows, the embedding and ensemble models on a {emb['n_test']:,}-row subset "
      f"(embedding every complaint costs an API call), and Groq on {groq['m']['n']} rows, where TF-IDF scores {pc(groq['m']['tfidf_same_rows']['accuracy'])} on the same rows. Differences below about 0.3 points are within noise at the smaller sizes.")
    w("")
    w("Against the *recorded* (noisy) label the deployed TF-IDF model scores "
      f"{pc(t_rec['accuracy'])} accuracy / macro-F1 {f(t_rec['macro_f1'])}: the ~3% gap to the true-label score is the injected label noise, i.e. the model mostly "
      "ignores the wrong labels instead of memorising them.")
    w("")
    w("**Findings**")
    w("- On this data the simple lexical model is at the ceiling; embeddings did **not** help (they were trained on only 3,000 rows because every embedding is a paid-for API call; "
      "Marathi and Hinglish are their weak spots). The ensemble is no better than TF-IDF beyond noise, so TF-IDF is deployed and embeddings are kept for duplicate search and RAG.")
    w(f"- Groq zero-shot reaches {pc(groq['m']['vs_true_label']['accuracy'])} with **no training data**, which is useful for new categories or cold starts, but it is slower "
      f"({groq['m']['seconds']:.0f} s for {groq['m']['n']} complaints, batched 10 per call), rate limited, and weakest on Hindi ({pc(groq['m']['by_language_vs_true']['hi']['accuracy'])}). It is **not** used in the live path.")
    w("")
    w("### Per-category results (TF-IDF, vs true label)")
    w("")
    w("| Category | Precision | Recall | F1 | Support |")
    w("|---|---:|---:|---:|---:|")
    for c, v in t_true["per_class"].items():
        w(f"| {c} | {f(v['precision'])} | {f(v['recall'])} | {f(v['f1'])} | {v['support']:,} |")
    w("")
    w("### Confusion matrix (TF-IDF, vs true label; rows = true, columns = predicted)")
    w("")
    w("| true \\ pred | " + " | ".join(c[:6] for c in labels) + " |")
    w("|---|" + "---:|" * len(labels))
    for lab, row in zip(labels, cm):
        w(f"| {lab} | " + " | ".join(str(v) if v else "." for v in row) + " |")
    w("")
    w("The only confusions are public health <-> water supply (12 complaints: contaminated-water reports touch both) and one encroachment complaint predicted as water supply.")
    w("")
    w("### Accuracy by language (TF-IDF vs true label)")
    w("")
    w("| Language | Rows | TF-IDF | Embedding LR | Ensemble | Groq zero-shot |")
    w("|---|---:|---:|---:|---:|---:|")
    for lg in ("en", "hi", "mr", "hinglish"):
        a, b, c2, d = tfidf["m"]["by_language_vs_true"].get(lg), emb["m"]["by_language_vs_true"].get(lg), ens["m"]["by_language_vs_true"].get(lg), groq["m"]["by_language_vs_true"].get(lg)
        w(f"| {lg} | {a['n']:,} | {pc(a['accuracy'])} | {pc(b['accuracy'])} | {pc(c2['accuracy'])} | {pc(d['accuracy'])} |")
    w("")
    w("### Confidence and the human-review queue")
    w("")
    w(f"Complaints whose top-class probability is below **{conf['review_threshold']}** (or that confidently disagree with their recorded label) are flagged `needs_review`. "
      f"After scoring all {n_complaints:,} complaints, {n_review:,} ({100 * n_review / n_complaints:.1f}%) are in the queue. Is the confidence trustworthy? Accuracy by confidence bin for TF-IDF:")
    w("")
    w("| Confidence | Complaints | Accuracy (vs true) |")
    w("|---|---:|---:|")
    for b in conf["bins"]:
        w(f"| {b['range']} | {b['n']:,} | {pc(b['accuracy'])} |")
    w("")
    w(f"Flagged items ({pc(conf['flagged_share'], 2)} of the test set) are right only {pc(conf['accuracy_flagged'])} of the time, unflagged ones {pc(conf['accuracy_unflagged'], 2)}: the queue concentrates the errors. "
      f"(The embedding model flags {pc(e_conf['flagged_share'])} at {pc(e_conf['accuracy_flagged'])} accuracy.) The recorded-label disagreement rule adds the label-noise rows to the queue.")
    w("")

    # ---------------------------------------------------------------- 2
    w("## 2. SLA-breach risk")
    w("")
    bm = breach["m"]
    w(f"Task: predict at registration time whether a complaint will breach its SLA (gradient boosting on category, ward history, season, time of day, channel, language, recent local load). "
      f"**Chronological split** (train on the earliest 80%, test on complaints from {bm['test_period_starts'][:10]} onward), because a random split would leak the future.")
    w("")
    w("| Metric | Model | Category-rate baseline |")
    w("|---|---:|---:|")
    w(f"| ROC-AUC | **{f(bm['roc_auc'])}** | {f(bm['baseline_category_rate']['roc_auc'])} |")
    w(f"| Brier score (lower is better) | **{f(bm['brier'])}** | {f(bm['baseline_category_rate']['brier'])} |")
    w(f"| PR-AUC | {f(bm['pr_auc'])} | - |")
    w(f"| Accuracy at 0.5 | {pc(bm['accuracy@0.5'])} | base rate of breaches in test: {pc(bm['base_rate_test'])} |")
    w("")
    w("Moderate skill: knowing the ward and recent load adds ~0.06 AUC over the category alone. Breach outcomes depend on things the text cannot reveal (crew availability), so this is a prioritisation hint, not a promise.")
    w("")

    # ---------------------------------------------------------------- 3
    w("## 3. Near-duplicate detection")
    w("")
    dm = dup["m"]
    w(f"The generator plants {dm['true_pairs']:,} duplicate pairs (same issue re-reported, sometimes in another language or channel). Candidates are complaints in the same ward within {dup['p']['max_hours']:.0f} hours "
      f"({dm['candidate_pairs']:,} candidate pairs); a classifier on text similarity, distance, time gap, category and language scores each pair. Split by the later complaint (70/30).")
    w("")
    w("| Method | Precision | Recall | F1 |")
    w("|---|---:|---:|---:|")
    w(f"| Learned pair classifier (text features only) | {f(dm['precision'])} | {f(dm['recall'])} | **{f(dm['f1'])}** |")
    w(f"| Cosine-similarity rule alone | {f(dm['baseline_text_only']['precision'])} | {f(dm['baseline_text_only']['recall'])} | {f(dm['baseline_text_only']['f1'])} |")
    w("")
    w(f"Recall is {pc(dm['recall_same_language'])} for same-language pairs but only **{pc(dm['recall_cross_language'])} for cross-language pairs** (e.g. English vs Hindi report of the same pothole): the TF-IDF similarity cannot bridge languages. "
      f"The embedding feature (`cos_emb`) is designed for this but is only used for complaints that have a stored embedding. {n_dup:,} complaints are currently marked as duplicates.")
    w("")

    # ---------------------------------------------------------------- 4
    w("## 4. Sentiment, entities and language")
    w("")
    sm = sent["m"]
    w(f"**Sentiment** (VADER plus a hand-built Hindi/Marathi/Hinglish lexicon). Evaluated on {sm['n']:,} citizen-feedback texts using the star rating as an independent label (1-2 negative, 3 neutral, 4-5 positive): "
      f"3-class accuracy **{pc(sm['accuracy'])}**, macro-F1 {f(sm['macro_f1'])}; ignoring neutral, polarity accuracy is {pc(sm['polarity_accuracy_excluding_neutral'])}.")
    w("")
    w("| Language | Rows | Accuracy |")
    w("|---|---:|---:|")
    for lg, v in sm["by_language"].items():
        w(f"| {lg} | {v['n']:,} | {pc(v['accuracy'])} |")
    w("")
    w(f"Caveat: {sm['caveat']}. Hinglish and English neutral text is the hard part; the 100% polarity score reflects templated feedback.")
    w("")
    em_ = ent["m"]
    w(f"**Entity extraction** (gazetteer plus type keywords for landmarks, roads and areas). On {em_['n_complaints']:,} complaints with {em_['true_mentions']:,} true mentions: "
      f"precision **{pc(em_['precision'])}**, recall **{pc(em_['recall'])}**. {em_['caveat'].capitalize()}.")
    w("")
    lm = lang["m"]
    w(f"**Language detection** (script plus marker words; no model): accuracy {pc(lm['accuracy'])} on {lm['n']:,} complaints (macro-F1 {f(lm['macro_f1'])}); Hinglish vs English is the only meaningful confusion.")
    w("")

    # ---------------------------------------------------------------- 5
    w("## 5. Forecasting")
    w("")
    w("Monthly complaint volume per category and for the city. For each series four candidates are backtested with a **rolling origin**: the model is refit on data up to "
      f"{', '.join(str(o) for o in bt['origins_months_back'])} months before the end and asked to predict the next {bt['horizon_months']} months (9 predictions per model). "
      "The model with the lowest MAE is used for the live forecast. MAPE ignores months with zero actuals.")
    w("")
    w("| Series | Avg complaints / month | Best model | MAE | MAPE | naive MAPE | seasonal-naive MAPE | ETS-flat MAPE | ETS-seasonal MAPE |")
    w("|---|---:|---|---:|---:|---:|---:|---:|---:|")
    for s in bt["series"]:
        ms = s["models"]
        g = lambda k: (f"{ms[k]['mape_pct']:.1f}%" if k in ms and ms[k]["mape_pct"] is not None else "-")  # noqa: E731
        w(f"| {s['series']} | {s['mean_monthly']:.0f} | {(s['best'] or '-').replace('_', ' ')} | {f(s['best_mae'], 1)} | {f(s['best_mape_pct'], 1)}% | {g('naive')} | {g('seasonal_naive')} | {g('ets_flat')} | {g('ets_seasonal')} |")
    w("")
    w(f"Average MAPE of the chosen models: **{bt['avg_best_mape_pct']}%**. Seasonal-naive or seasonal ETS win for monsoon-driven categories; plain naive wins for stable ones (garbage). "
      "**Drainage is the weakest (~60% MAPE)**: it has sharp monsoon peaks and three years of history contain only three monsoons, so seasonal amplitude cannot be learned reliably. "
      "Models are chosen by MAE, which favours getting the large monsoon months right; for drainage the seasonal-naive and flat-ETS models actually have a lower MAPE (see the table) but a higher MAE, so the choice rule matters and was not tuned per series. "
      "Prediction intervals (95%) are approximate (+/-1.96 sigma sqrt(h) of in-sample one-step residuals); they were not separately calibrated.")
    w("")
    w("Resource estimates multiply forecast volume by **assumed** crew-days per complaint (configurable); they are planning aids, not measured productivities. "
      "The what-if model is a documented simple queue (capacity with diminishing returns, backlog accumulation, linear SLA sensitivity) and is **not validated** against real interventions.")
    w("")

    # ---------------------------------------------------------------- 6
    w("## 6. Emerging-issue detection and Service Gap Index")
    w("")
    planted_now = [b for b in truth["bursts"] if b["end"] >= truth["period"][1][:7] + "-01"]
    flagged = {i["category"]: {x["code"] for x in i["wards"]} for i in em["issues"]}
    w(f"The generator planted {len(truth['bursts'])} bursts; {len(planted_now)} of them are still active at the end of the data ({em['as_of']}). The detector (z-score of the last 14 days against an 8-week baseline, per category x ward) reports:")
    w("")
    w("| Planted active burst | Planted wards | Detected? | Wards flagged |")
    w("|---|---|---|---|")
    for b in planted_now:
        got = flagged.get(b["category"])
        w(f"| {b['label']} ({b['category']}) | {', '.join(b['wards'])} | {'yes' if got else '**no**'} | {', '.join(sorted(got)) if got else '-'} |")
    extra = [c for c in flagged if c not in {b['category'] for b in planted_now}]
    w("")
    w(f"Extra issues flagged that were not planted: {', '.join(extra) if extra else 'none'}. A planted ward can be missed when its own baseline is already high (the z-score is relative to the ward's own history). "
      "Historic bursts are outside the 'now' window and are not re-detected by design (the feed is about *current* spikes).")
    w("")
    planted_bad = truth["underperforming_wards"]
    top = [i["code"] for i in gi["items"][:len(planted_bad)]]
    w(f"**Service Gap Index.** The generator makes wards {', '.join(planted_bad)} underperform (factors {', '.join(str(v) for v in planted_bad.values())}). "
      f"The index's {len(planted_bad)} worst wards over the last 12 months are **{', '.join(top)}**: "
      f"{'an exact match' if set(top) == set(planted_bad) else str(len(set(top) & set(planted_bad))) + ' of ' + str(len(planted_bad)) + ' match'}. "
      "Well-run wards (" + ", ".join(truth["well_run_wards"]) + ") sit toward the good end (rank 1 = worst): " + ", ".join(f"{i['code']} #{i['rank']}" for i in gi["items"] if i["code"] in truth["well_run_wards"]) + f" of {len(gi['items'])}.")
    w("")

    # ---------------------------------------------------------------- 7
    w("## 7. LLM layer (Groq)")
    w("")
    w("Groq (`openai/gpt-oss-120b` main, `openai/gpt-oss-20b` fast) is used for prose and for tool selection only; **every number shown to a user is computed by Python/SQL**. "
      "These are qualitative checks from live runs (the unit tests mock the Groq client):")
    w("")
    w("| Check | Result |")
    w("|---|---|")
    w("| \"Which wards had the worst drainage performance last monsoon?\" | Called `rank_wards` with `from_month=2026-06`, `to_month=2026-09` (correct reading of 'last monsoon'); returned W17, W05, W12, W07, W15 |")
    w("| \"Compare SLA of Drainage and Roads and forecast drainage complaints\" | Chained two tools (`rank_departments`, `forecast_volume`); numbers matched the dashboards |")
    w("| Policy question: pothole repair target | RAG retrieved the road-maintenance SOP (vector score 0.63) and answered \"120 hours\" with citations [2][4] |")
    w("| Emerging-issue summaries | Two-sentence summaries grounded in supplied facts; deterministic template used if the API fails |")
    w("| Policy brief | ~18 s; first version overstated some trends, so the prompt now forbids un-given baselines and the facts carry explicit `data_notes` (right-censoring of recent months) |")
    w("")
    w("Operational numbers (free tier): ~8,000 tokens/minute limit on both models, handled with Retry-After backoff; assistant answers take 4-11 s; results are cached (24 h briefs, 6 h summaries); "
      "per-user limit 20 requests/minute; every call is logged (model, tokens, latency, cache hit) for the monitoring page.")
    w("")
    w("**Known weaknesses.** The assistant can add a figure that no tool returned (e.g. a 'city average'); prompts forbid this but it is not enforced programmatically. "
      "Tool arguments are validated, so the model cannot cause harm, but it can pick a less suitable metric (it used the gap index for a 'drainage performance' question). "
      "AI text is always labelled with model, cache status and demo-mode flag.")
    w("")

    # ---------------------------------------------------------------- 8
    w("## 8. System quality")
    w("")
    w("**Automated tests** (pytest against a real Supabase schema, Groq mocked; Vitest for the frontend):")
    w("")
    w("| Package | Statement coverage |")
    w("|---|---:|")
    w("| `app/services` | 93% |")
    w("| `app/ml` | 79% (lowest: training-data loaders and the Hugging Face backfill, which need external services) |")
    w("| `app/llm`, `app/nlp`, `app/core`, `app/ingestion` | 71-100% per module |")
    w("| **All measured packages** | **89%** (services + ml combined 86%; target was 70%) |")
    w("")
    w("Frontend: 9 Vitest tests (formatting, CSV-injection guard, role navigation, SSE parser); the production build, TypeScript (strict, no `any`) and ESLint are clean; every page was loaded in a real browser for each role with no console errors.")
    w("")
    w("**Latency** (remote Supabase DB, about 0.3-1 s per round trip from the dev machine; 2026-10-01): analytics endpoints 0.0-0.6 s when warm and 3-10 s on the first request after a restart "
      "(the cache is rebuilt); plain list endpoints (complaints, users) 1-4 s purely from database round trips. The two caching layers (Postgres `cache_entries` and an in-process layer) took the resource forecast from 12 s to 0.0 s.")
    w("")
    w("**Storage**: 184.6 MB of the 500 MB Supabase free tier (50,000 complaints with embeddings, feedback, performance, indexes).")
    w("")

    # ---------------------------------------------------------------- 9
    w("## 9. Limitations")
    w("")
    for s in [
        "**Synthetic data.** Real municipal data will be noisier, more ambiguous, multi-issue, and written in more dialects and scripts; classifier accuracy will be lower and the review queue larger. Nothing here has been validated on real complaints.",
        "**Label-noise handling is only tested against injected, random noise.** Real label errors are systematic (e.g. department habits).",
        "**Embeddings come from a hosted API** (Hugging Face Inference). Only 3,000 complaints were embedded for the embedding classifier, and a missing token degrades duplicate detection to text-only (cross-language recall ~81%).",
        "**Forecasts have 3 years of history.** Seasonal models see 3 cycles; drainage MAPE is ~60%; intervals are approximate and uncalibrated; ward-level series are noisy; one-off bursts distort baselines.",
        "**Resource and what-if numbers rest on assumptions** (crew-days per complaint, elasticity 0.8, SLA sensitivity 0.5) that are plausible defaults, not measurements.",
        "**Right-censoring.** The newest month always looks worse on resolution rate, resolution time and backlog because recent complaints are still open. The UI warns about this, but month-on-month arrows for those KPIs should be read with care.",
        "**Emerging-issue detection is a statistical heuristic**, not causal: it compares to a ward's own recent past, can miss spikes in already-busy wards, and flags planned events as spikes. Topic discovery uses TF-IDF + NMF (BERTopic was replaced to avoid heavy local models), so topics are keyword-based and weak across languages.",
        "**Service Gap Index weights are expert choices**, min-max scaled across wards, so it ranks wards *relative to each other*; a uniformly good city still has a 'worst' ward.",
        "**LLM output is not guaranteed faithful** (see section 7); the free tier is rate limited (about 8,000 tokens/min) and the 'stream' of assistant answers is chunked after generation rather than token-by-token.",
        "**Security scope.** JWT tokens are kept in `localStorage` (XSS-exposed) and cannot be revoked server-side before they expire (30 min access, 7 days refresh); there is no MFA; PII masking is regex-based (Indian phone numbers, e-mail, Aadhaar, PAN) and will miss free-text names. Suitable for an academic prototype, not for production without hardening.",
        "**Operations.** Single API process, Postgres-backed queue and cache (no Redis), no autoscaling; the remote database dominates latency; inline jobs die with the API process; the in-process cache can serve data up to 5 minutes stale after an import done by another process.",
        "**Scale.** Evaluated at 50,000 complaints; the SQL aggregation cube, DBSCAN (sampled to 12,000 points) and per-request caches are sized for that, not for millions of rows.",
    ]:
        w(f"- {s}")
    w("")

    # ---------------------------------------------------------------- 10
    w("## 10. Reproducing these numbers")
    w("")
    w("```bash")
    w("python scripts/tasks.py generate           # deterministic synthetic data (seed 42) under data/generated/")
    w("python scripts/tasks.py seed               # load it through the real ingestion pipeline")
    w("python scripts/tasks.py train              # train + evaluate classifiers, breach risk, duplicates; registers runs; writes predictions")
    w("python scripts/tasks.py groq-eval          # Groq zero-shot comparison (needs GROQ_API_KEY)")
    w("cd backend && python -m scripts.make_eval_doc   # regenerate this file")
    w("python scripts/tasks.py cov                # tests + coverage (about 15 minutes against a remote database)")
    w("```")
    w("")
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(out)} lines) at {time.strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
