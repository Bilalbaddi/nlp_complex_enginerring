# SG-DSS Evaluation Report

_Generated 2026-10-01 by `backend/scripts/make_eval_doc.py` from the model registry (`model_runs`) and the analytics engine._

**Read this first.** All data is synthetic (fictional city *Sahyadripur*, 50,000 complaints, 3 years, seed 42) generated from templates, so the generator's *true* labels are known. That makes evaluation possible, but it also means **scores are optimistic**: templated text is far more regular than real citizen writing, and the planted effects (seasonality, hotspots, bursts, underperforming wards) are cleaner than reality. Treat the numbers as evidence that the pipelines work end to end, not as expected production accuracy. See [Limitations](#9-limitations).

## Contents
1. [Category classification](#1-complaint-category-classification)  2. [SLA-breach risk](#2-sla-breach-risk)  3. [Duplicate detection](#3-near-duplicate-detection)  4. [Sentiment, entities, language](#4-sentiment-entities-and-language)  5. [Forecasting](#5-forecasting)  6. [Emerging issues and gap index](#6-emerging-issue-detection-and-service-gap-index)  7. [LLM layer](#7-llm-layer-groq)  8. [System quality](#8-system-quality)  9. [Limitations](#9-limitations)  10. [Reproducing](#10-reproducing-these-numbers)

## 1. Complaint category classification

Task: assign one of 10 categories (water supply, drainage, roads, garbage, streetlights, encroachment, public health, electricity, parks, noise) to a complaint written in English, Hindi, Marathi or Hinglish. Models were trained on the **recorded** category, which the generator corrupts for ~3% of rows (label noise, like real data). Each model is therefore scored against both the *recorded* label and the generator's *true* label.

| Model | Test rows | Accuracy (vs true) | Macro precision | Macro recall | Macro F1 |
|---|---:|---:|---:|---:|---:|
| TF-IDF (word 1-2 + char 2-5 grams) + logistic regression **(deployed baseline)** | 10,000 | 99.9% | 0.999 | 0.998 | 0.999 |
| Hashing n-gram embedding + LR (offline, no API) | 10,000 | 99.8% | 0.998 | 0.997 | 0.998 |
| Multilingual MiniLM embeddings (Hugging Face API) + LR, trained on 3,000 rows | 1,000 | 97.2% | 0.975 | 0.969 | 0.972 |
| Ensemble: 0.5 x TF-IDF + 0.5 x embedding probabilities | 1,000 | 99.9% | 0.999 | 0.998 | 0.999 |
| Groq zero-shot (`openai/gpt-oss-20b`), stratified sample, no training | 200 | 92.5% | 0.941 | 0.925 | 0.924 |

Test sizes differ: TF-IDF and the hashing model were scored on 10,000 held-out rows, the embedding and ensemble models on a 1,000-row subset (embedding every complaint costs an API call), and Groq on 200 rows, where TF-IDF scores 99.5% on the same rows. Differences below about 0.3 points are within noise at the smaller sizes.

Against the *recorded* (noisy) label the deployed TF-IDF model scores 96.9% accuracy / macro-F1 0.964: the ~3% gap to the true-label score is the injected label noise, i.e. the model mostly ignores the wrong labels instead of memorising them.

**Findings**
- On this data the simple lexical model is at the ceiling; embeddings did **not** help (they were trained on only 3,000 rows because every embedding is a paid-for API call; Marathi and Hinglish are their weak spots). The ensemble is no better than TF-IDF beyond noise, so TF-IDF is deployed and embeddings are kept for duplicate search and RAG.
- Groq zero-shot reaches 92.5% with **no training data**, which is useful for new categories or cold starts, but it is slower (52 s for 200 complaints, batched 10 per call), rate limited, and weakest on Hindi (83.3%). It is **not** used in the live path.

### Per-category results (TF-IDF, vs true label)

| Category | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| noise | 1.000 | 1.000 | 1.000 | 571 |
| parks | 1.000 | 1.000 | 1.000 | 372 |
| roads | 1.000 | 1.000 | 1.000 | 1,655 |
| garbage | 1.000 | 1.000 | 1.000 | 1,476 |
| drainage | 1.000 | 1.000 | 1.000 | 1,434 |
| electricity | 1.000 | 1.000 | 1.000 | 801 |
| encroachment | 1.000 | 0.998 | 0.999 | 465 |
| streetlights | 1.000 | 1.000 | 1.000 | 1,091 |
| water_supply | 0.995 | 0.996 | 0.996 | 1,467 |
| public_health | 0.991 | 0.991 | 0.991 | 668 |

### Confusion matrix (TF-IDF, vs true label; rows = true, columns = predicted)

| true \ pred | draina | electr | encroa | garbag | noise | parks | public | roads | street | water_ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| drainage | 1434 | . | . | . | . | . | . | . | . | . |
| electricity | . | 801 | . | . | . | . | . | . | . | . |
| encroachment | . | . | 464 | . | . | . | . | . | . | 1 |
| garbage | . | . | . | 1476 | . | . | . | . | . | . |
| noise | . | . | . | . | 571 | . | . | . | . | . |
| parks | . | . | . | . | . | 372 | . | . | . | . |
| public_health | . | . | . | . | . | . | 662 | . | . | 6 |
| roads | . | . | . | . | . | . | . | 1655 | . | . |
| streetlights | . | . | . | . | . | . | . | . | 1091 | . |
| water_supply | . | . | . | . | . | . | 6 | . | . | 1461 |

The only confusions are public health <-> water supply (12 complaints: contaminated-water reports touch both) and one encroachment complaint predicted as water supply.

### Accuracy by language (TF-IDF vs true label)

| Language | Rows | TF-IDF | Embedding LR | Ensemble | Groq zero-shot |
|---|---:|---:|---:|---:|---:|
| en | 5,420 | 99.9% | 99.1% | 99.8% | 94.3% |
| hi | 1,231 | 100.0% | 99.2% | 100.0% | 83.3% |
| mr | 1,831 | 99.9% | 91.8% | 100.0% | 90.9% |
| hinglish | 1,518 | 99.7% | 94.8% | 100.0% | 100.0% |

### Confidence and the human-review queue

Complaints whose top-class probability is below **0.6** (or that confidently disagree with their recorded label) are flagged `needs_review`. After scoring all 50,000 complaints, 1,149 (2.3%) are in the queue. Is the confidence trustworthy? Accuracy by confidence bin for TF-IDF:

| Confidence | Complaints | Accuracy (vs true) |
|---|---:|---:|
| 0.00-0.40 | 0 | - |
| 0.40-0.60 | 21 | 81.0% |
| 0.60-0.80 | 124 | 94.4% |
| 0.80-0.95 | 1,832 | 99.9% |
| 0.95-1.00 | 8,023 | 100.0% |

Flagged items (0.21% of the test set) are right only 81.0% of the time, unflagged ones 99.91%: the queue concentrates the errors. (The embedding model flags 9.1% at 74.7% accuracy.) The recorded-label disagreement rule adds the label-noise rows to the queue.

## 2. SLA-breach risk

Task: predict at registration time whether a complaint will breach its SLA (gradient boosting on category, ward history, season, time of day, channel, language, recent local load). **Chronological split** (train on the earliest 80%, test on complaints from 2026-04-09 onward), because a random split would leak the future.

| Metric | Model | Category-rate baseline |
|---|---:|---:|
| ROC-AUC | **0.788** | 0.732 |
| Brier score (lower is better) | **0.189** | 0.215 |
| PR-AUC | 0.780 | - |
| Accuracy at 0.5 | 71.9% | base rate of breaches in test: 48.5% |

Moderate skill: knowing the ward and recent load adds ~0.06 AUC over the category alone. Breach outcomes depend on things the text cannot reveal (crew availability), so this is a prioritisation hint, not a promise.

## 3. Near-duplicate detection

The generator plants 1,500 duplicate pairs (same issue re-reported, sometimes in another language or channel). Candidates are complaints in the same ward within 72 hours (468,272 candidate pairs); a classifier on text similarity, distance, time gap, category and language scores each pair. Split by the later complaint (70/30).

| Method | Precision | Recall | F1 |
|---|---:|---:|---:|
| Learned pair classifier (text features only) | 0.968 | 0.929 | **0.948** |
| Cosine-similarity rule alone | 0.061 | 0.595 | 0.110 |

Recall is 98.4% for same-language pairs but only **80.6% for cross-language pairs** (e.g. English vs Hindi report of the same pothole): the TF-IDF similarity cannot bridge languages. The embedding feature (`cos_emb`) is designed for this but is only used for complaints that have a stored embedding. 1,462 complaints are currently marked as duplicates.

## 4. Sentiment, entities and language

**Sentiment** (VADER plus a hand-built Hindi/Marathi/Hinglish lexicon). Evaluated on 14,077 citizen-feedback texts using the star rating as an independent label (1-2 negative, 3 neutral, 4-5 positive): 3-class accuracy **91.0%**, macro-F1 0.860; ignoring neutral, polarity accuracy is 100.0%.

| Language | Rows | Accuracy |
|---|---:|---:|
| en | 7,346 | 89.9% |
| hi | 1,861 | 100.0% |
| mr | 2,628 | 100.0% |
| hinglish | 2,242 | 76.9% |

Caveat: synthetic templated feedback text; expect lower accuracy on real free text. Hinglish and English neutral text is the hard part; the 100% polarity score reflects templated feedback.

**Entity extraction** (gazetteer plus type keywords for landmarks, roads and areas). On 5,000 complaints with 5,289 true mentions: precision **100.0%**, recall **95.2%**. Recall misses lower-cased (sloppily typed) place names; precision is against the known gazetteer.

**Language detection** (script plus marker words; no model): accuracy 99.8% on 50,000 complaints (macro-F1 0.996); Hinglish vs English is the only meaningful confusion.

## 5. Forecasting

Monthly complaint volume per category and for the city. For each series four candidates are backtested with a **rolling origin**: the model is refit on data up to 9, 6, 3 months before the end and asked to predict the next 3 months (9 predictions per model). The model with the lowest MAE is used for the live forecast. MAPE ignores months with zero actuals.

| Series | Avg complaints / month | Best model | MAE | MAPE | naive MAPE | seasonal-naive MAPE | ETS-flat MAPE | ETS-seasonal MAPE |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| All categories | 1526 | ets seasonal | 226.9 | 13.5% | 28.7% | 14.5% | 20.6% | 13.5% |
| Water Supply Department | 218 | seasonal naive | 18.9 | 8.7% | 39.3% | 8.7% | 36.8% | 13.9% |
| Drainage & Sewerage Department | 193 | ets seasonal | 62.1 | 60.8% | 77.9% | 39.0% | 35.4% | 60.8% |
| Roads & Maintenance Department | 265 | seasonal naive | 58.8 | 17.0% | 43.5% | 17.0% | 36.7% | 16.4% |
| Solid Waste Management Department | 208 | naive | 27.4 | 13.4% | 13.4% | 30.0% | 14.4% | 14.5% |
| Street Lighting Department | 176 | seasonal naive | 31.3 | 21.3% | 36.7% | 21.3% | 40.8% | 38.9% |
| Encroachment Removal Department | 77 | ets seasonal | 20.4 | 20.0% | 21.9% | 21.8% | 20.4% | 20.0% |
| Public Health Department | 116 | seasonal naive | 45.2 | 18.8% | 30.9% | 18.8% | 24.6% | 26.4% |
| Electricity Department | 124 | seasonal naive | 14.0 | 11.4% | 31.8% | 11.4% | 30.3% | 10.4% |
| Parks & Gardens Department | 64 | ets seasonal | 5.2 | 8.6% | 30.9% | 14.1% | 34.4% | 8.6% |
| Pollution Control Department | 85 | ets seasonal | 8.0 | 9.5% | 31.4% | 13.6% | 16.4% | 9.5% |

Average MAPE of the chosen models: **18.5%**. Seasonal-naive or seasonal ETS win for monsoon-driven categories; plain naive wins for stable ones (garbage). **Drainage is the weakest (~60% MAPE)**: it has sharp monsoon peaks and three years of history contain only three monsoons, so seasonal amplitude cannot be learned reliably. Models are chosen by MAE, which favours getting the large monsoon months right; for drainage the seasonal-naive and flat-ETS models actually have a lower MAPE (see the table) but a higher MAE, so the choice rule matters and was not tuned per series. Prediction intervals (95%) are approximate (+/-1.96 sigma sqrt(h) of in-sample one-step residuals); they were not separately calibrated.

Resource estimates multiply forecast volume by **assumed** crew-days per complaint (configurable); they are planning aids, not measured productivities. The what-if model is a documented simple queue (capacity with diminishing returns, backlog accumulation, linear SLA sensitivity) and is **not validated** against real interventions.

## 6. Emerging-issue detection and Service Gap Index

The generator planted 7 bursts; 3 of them are still active at the end of the data (2026-09-29). The detector (z-score of the last 14 days against an 8-week baseline, per category x ward) reports:

| Planted active burst | Planted wards | Detected? | Wards flagged |
|---|---|---|---|
| Vector-borne disease surge (public_health) | W05, W06 | yes | W06 |
| Ring road pothole surge (roads) | W15, W16, W20 | yes | W15, W16, W20 |
| Market gate encroachment (encroachment) | W08 | yes | W08 |

Extra issues flagged that were not planted: none. A planted ward can be missed when its own baseline is already high (the z-score is relative to the ward's own history). Historic bursts are outside the 'now' window and are not re-detected by design (the feed is about *current* spikes).

**Service Gap Index.** The generator makes wards W05, W12, W17, W15 underperform (factors 1.9, 1.7, 1.8, 1.4). The index's 4 worst wards over the last 12 months are **W17, W05, W12, W15**: an exact match. Well-run wards (W03, W14, W20) sit toward the good end (rank 1 = worst): W20 #13, W03 #19, W14 #20 of 20.

## 7. LLM layer (Groq)

Groq (`openai/gpt-oss-120b` main, `openai/gpt-oss-20b` fast) is used for prose and for tool selection only; **every number shown to a user is computed by Python/SQL**. These are qualitative checks from live runs (the unit tests mock the Groq client):

| Check | Result |
|---|---|
| "Which wards had the worst drainage performance last monsoon?" | Called `rank_wards` with `from_month=2026-06`, `to_month=2026-09` (correct reading of 'last monsoon'); returned W17, W05, W12, W07, W15 |
| "Compare SLA of Drainage and Roads and forecast drainage complaints" | Chained two tools (`rank_departments`, `forecast_volume`); numbers matched the dashboards |
| Policy question: pothole repair target | RAG retrieved the road-maintenance SOP (vector score 0.63) and answered "120 hours" with citations [2][4] |
| Emerging-issue summaries | Two-sentence summaries grounded in supplied facts; deterministic template used if the API fails |
| Policy brief | ~18 s; first version overstated some trends, so the prompt now forbids un-given baselines and the facts carry explicit `data_notes` (right-censoring of recent months) |

Operational numbers (free tier): ~8,000 tokens/minute limit on both models, handled with Retry-After backoff; assistant answers take 4-11 s; results are cached (24 h briefs, 6 h summaries); per-user limit 20 requests/minute; every call is logged (model, tokens, latency, cache hit) for the monitoring page.

**Known weaknesses.** The assistant can add a figure that no tool returned (e.g. a 'city average'); prompts forbid this but it is not enforced programmatically. Tool arguments are validated, so the model cannot cause harm, but it can pick a less suitable metric (it used the gap index for a 'drainage performance' question). AI text is always labelled with model, cache status and demo-mode flag.

## 8. System quality

**Automated tests** (pytest against a real Supabase schema, Groq mocked; Vitest for the frontend):

| Package | Statement coverage |
|---|---:|
| `app/services` | 93% |
| `app/ml` | 79% (lowest: training-data loaders and the Hugging Face backfill, which need external services) |
| `app/llm`, `app/nlp`, `app/core`, `app/ingestion` | 71-100% per module |
| **All measured packages** | **89%** (services + ml combined 86%; target was 70%) |

Frontend: 9 Vitest tests (formatting, CSV-injection guard, role navigation, SSE parser); the production build, TypeScript (strict, no `any`) and ESLint are clean; every page was loaded in a real browser for each role with no console errors.

**Latency** (remote Supabase DB, about 0.3-1 s per round trip from the dev machine; 2026-10-01): analytics endpoints 0.0-0.6 s when warm and 3-10 s on the first request after a restart (the cache is rebuilt); plain list endpoints (complaints, users) 1-4 s purely from database round trips. The two caching layers (Postgres `cache_entries` and an in-process layer) took the resource forecast from 12 s to 0.0 s.

**Storage**: 184.6 MB of the 500 MB Supabase free tier (50,000 complaints with embeddings, feedback, performance, indexes).

## 9. Limitations

- **Synthetic data.** Real municipal data will be noisier, more ambiguous, multi-issue, and written in more dialects and scripts; classifier accuracy will be lower and the review queue larger. Nothing here has been validated on real complaints.
- **Label-noise handling is only tested against injected, random noise.** Real label errors are systematic (e.g. department habits).
- **Embeddings come from a hosted API** (Hugging Face Inference). Only 3,000 complaints were embedded for the embedding classifier, and a missing token degrades duplicate detection to text-only (cross-language recall ~81%).
- **Forecasts have 3 years of history.** Seasonal models see 3 cycles; drainage MAPE is ~60%; intervals are approximate and uncalibrated; ward-level series are noisy; one-off bursts distort baselines.
- **Resource and what-if numbers rest on assumptions** (crew-days per complaint, elasticity 0.8, SLA sensitivity 0.5) that are plausible defaults, not measurements.
- **Right-censoring.** The newest month always looks worse on resolution rate, resolution time and backlog because recent complaints are still open. The UI warns about this, but month-on-month arrows for those KPIs should be read with care.
- **Emerging-issue detection is a statistical heuristic**, not causal: it compares to a ward's own recent past, can miss spikes in already-busy wards, and flags planned events as spikes. Topic discovery uses TF-IDF + NMF (BERTopic was replaced to avoid heavy local models), so topics are keyword-based and weak across languages.
- **Service Gap Index weights are expert choices**, min-max scaled across wards, so it ranks wards *relative to each other*; a uniformly good city still has a 'worst' ward.
- **LLM output is not guaranteed faithful** (see section 7); the free tier is rate limited (about 8,000 tokens/min) and the 'stream' of assistant answers is chunked after generation rather than token-by-token.
- **Security scope.** JWT tokens are kept in `localStorage` (XSS-exposed) and cannot be revoked server-side before they expire (30 min access, 7 days refresh); there is no MFA; PII masking is regex-based (Indian phone numbers, e-mail, Aadhaar, PAN) and will miss free-text names. Suitable for an academic prototype, not for production without hardening.
- **Operations.** Single API process, Postgres-backed queue and cache (no Redis), no autoscaling; the remote database dominates latency; inline jobs die with the API process; the in-process cache can serve data up to 5 minutes stale after an import done by another process.
- **Scale.** Evaluated at 50,000 complaints; the SQL aggregation cube, DBSCAN (sampled to 12,000 points) and per-request caches are sized for that, not for millions of rows.

## 10. Reproducing these numbers

```bash
python scripts/tasks.py generate           # deterministic synthetic data (seed 42) under data/generated/
python scripts/tasks.py seed               # load it through the real ingestion pipeline
python scripts/tasks.py train              # train + evaluate classifiers, breach risk, duplicates; registers runs; writes predictions
python scripts/tasks.py groq-eval          # Groq zero-shot comparison (needs GROQ_API_KEY)
cd backend && python -m scripts.make_eval_doc   # regenerate this file
python scripts/tasks.py cov                # tests + coverage (about 15 minutes against a remote database)
```

