# Model cards

One card per model or scoring method. Metrics are measured on the synthetic data set and copied from [EVALUATION.md](EVALUATION.md) (regenerate that file to refresh them). All models run on a laptop CPU; **no model weights are downloaded or hosted locally** (embeddings and the LLM are API calls). Trained models are versioned in the `model_runs` table and their artifacts live in the private `sgdss-models` Supabase bucket.

> **Intended use of all models:** decision *support* for municipal staff (triage, prioritisation, planning). None of them makes a decision about a citizen automatically; low-confidence items go to a human review queue. **Not for:** law enforcement, eligibility decisions, individual scoring of citizens, or any use on real data without re-validation.

---

## 1. Complaint category classifier (deployed: TF-IDF + logistic regression)

| | |
|---|---|
| Code | `app/ml/classifier.py`, `app/ml/features.py`, trained by `scripts/train.py` |
| Task | 10-class text classification of complaint descriptions in English, Hindi, Marathi, Hinglish |
| Input | Normalised text (lower-cased, whitespace collapsed; PII is not used as a feature) |
| Features | TF-IDF word 1-2-grams (30k) + character 2-5-grams (50k, `char_wb`; robust to Hinglish spelling and Devanagari) |
| Model | Logistic regression, C = 10, trained in ~23 s |
| Training data | 40,000 complaints (80%), stratified; label = the **recorded** category (about 3% noisy) |
| Output | Category, confidence (max probability), top-3, routed department (one department per category) |
| Metrics (10,000 held-out) | accuracy 99.9% vs true label, macro-F1 0.999; 96.9% vs the noisy recorded label |
| By language | English 99.9%, Hindi 100%, Marathi 99.9%, Hinglish 99.7% |
| Confidence | Below 0.6 -> `needs_review` (2.3% of all complaints incl. label-disagreement rule); flagged items are 81% correct, unflagged 99.9% |
| Alternatives compared | Hashing embeddings + LR (99.8%), Hugging Face MiniLM embeddings + LR (97.2% on 1,000 rows), ensemble (99.9%), Groq zero-shot `gpt-oss-20b` (92.5% on 200 rows) |
| Failure modes | Public health vs water supply (contaminated water); new vocabulary never seen in training; sarcasm; multi-issue complaints (only one label is returned) |
| Limitations | Trained on templated synthetic text: expect markedly lower accuracy on real complaints. Misspelling robustness comes from character n-grams only |
| Human oversight | Review queue in the Complaints Explorer; a reviewer's category overrides the prediction and is never overwritten by re-scoring |

## 2. Embedding classifier and embeddings (supporting)

Multilingual `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-d) via the **Hugging Face Inference API**. Used for: near-duplicate similarity (`cos_emb` feature), RAG retrieval over policy documents, and an alternative classifier (97.2%, trained on only 3,000 rows, weaker on Marathi 91.8% and Hinglish 94.8%). Text is PII-stripped and truncated to 1,000 characters before leaving the system. A deterministic hashing embedder is the offline fallback (it lives in a different vector space, so **its vectors are never stored**).

## 3. Groq zero-shot classifier (comparison only)

| | |
|---|---|
| Model | `LLM_FAST_MODEL` (`openai/gpt-oss-20b` in the test run), JSON mode, 10 complaints per call |
| Prompt | `app/llm/prompts/zero_shot_category.txt` (category list + numbered complaints -> JSON) |
| Metrics | 92.5% accuracy / macro-F1 0.924 on a 200-complaint stratified sample (TF-IDF on the same rows: 99.5%); Hindi 83.3% |
| Use | Third comparison point required by the spec; handy for new categories without training data. **Not in the live path** (latency 52 s / 200 complaints, rate limits, cost) |

## 4. SLA-breach risk model

| | |
|---|---|
| Code | `app/ml/breach.py` |
| Task | Predict at registration whether a complaint will breach its SLA (binary) |
| Model | `HistGradientBoostingClassifier` |
| Features | SLA hours, ward and ward x category historical breach rates, month (sin/cos), weekday, hour, monsoon flag, text length, rule-based urgency, complaints in the same ward/category in the previous 7 days, one-hot category / language / channel |
| Evaluation | Chronological split: train on the earliest 80% (38,915), test on the latest 20% (9,729, from 2026-04-09) |
| Metrics | ROC-AUC 0.788 (category-only baseline 0.732), Brier 0.189 (baseline 0.215), PR-AUC 0.780 |
| Limitations | Moderate skill; historical rates are learned from training period only (no leakage into test); crew availability, the main real driver, is not observable |
| Fairness note | Ward-level rates mean the model assigns higher risk to historically slow wards; use it for prioritising help, never for blaming officers |

## 5. Near-duplicate detector

| | |
|---|---|
| Code | `app/ml/duplicates.py` |
| Method | Candidate pairs: same ward, within 72 h. Pair classifier (`HistGradientBoostingClassifier`, depth 4, 120 iterations) on `cos_text` (TF-IDF cosine), `cos_emb` (when both embeddings exist), distance (log metres), time gap, same category, same language |
| Metrics | Precision 0.968, recall 0.929, F1 0.948 (pairs split by the later complaint, 70/30; 1,500 planted pairs). Cosine rule alone: F1 0.110 |
| Weakness | Cross-language recall 80.6% (same-language 98.4%) when only text similarity is available |
| Output | `duplicate_of_id` (the earlier complaint); duplicates stay in the data and are counted in the repeat-complaint rate |

## 6. Sentiment

VADER plus a hand-built Hindi / Marathi / Hinglish lexicon (`app/nlp/sentiment.py`); score in [-1, 1], label negative <= -0.25 < neutral < 0.25 <= positive. Evaluated on 14,077 feedback texts against star ratings: 3-class accuracy 91.0% (English 89.9%, Hindi 100%, Marathi 100%, Hinglish 76.9%). Rule-based, no training; real free text will score lower.

## 7. Urgency / priority score

Transparent rule-based score 0-100 (`app/nlp/urgency.py`), optionally blended with the SLA-breach risk from model 4. Components: a base severity per category (electricity and public health highest, parks lowest); multilingual critical-harm phrases (live wire, sparking, accident, open manhole, dengue, outbreak, contaminated, ...); vulnerable-people mentions (children, elderly, school, hospital); escalation phrases ("complained earlier", "no response"); urgent wording; and stated duration ("since 3 days", "a week"). Levels: low < 30 <= medium < 50 <= high < 70 <= critical. Every point is explained in the returned `reasons`, so staff can audit it. The weights are a documented policy, not learned: no ground truth for "true urgency" exists in the data.

## 8. Entity extraction and language detection

Gazetteer (ward names, landmarks, roads) plus type keywords -> `entities` {landmarks, roads, areas}; precision 100%, recall 95.2% on 5,000 complaints (misses lower-cased place names). Language: Unicode script + marker words, 99.8% accuracy. Both are rule-based and need gazetteer maintenance for a new city.

## 9. Volume forecaster

| | |
|---|---|
| Code | `app/ml/forecast.py`, `app/services/forecasting.py` |
| Candidates | naive (last-3 mean), seasonal naive (same month last year, level-adjusted), damped-trend ETS, damped-trend ETS + 12-month season (`statsmodels.ExponentialSmoothing`) |
| Selection | Rolling-origin backtest (origins 9, 6, 3 months back, 3-month horizon), lowest MAE wins, per series (city, each category, each ward) |
| Interval | mean +/- 1.96 x sigma x sqrt(h) (sigma = in-sample one-step residual std, floored at sqrt(mean)); clipped at 0 |
| Metrics | Average chosen-model MAPE 18.5%; city 13.5%; drainage 60.8% (weakest); garbage 13.4%; roads 17.0% |
| Data | 33-35 complete months (a partial first/last month is dropped) |
| Limitations | Three years = three seasons; intervals are uncalibrated; ward x category series are noisy; outputs ignore planned events and policy changes |

## 10. Resource needs, infrastructure risk, what-if

Deterministic formulas, not learned models (`app/services/forecasting.py`, formulas in its docstring):
* **Resource need** = forecast complaints x crew-days per complaint (assumed defaults, overridable per request); capacity = staff x 22 working days x 60% field share.
* **Ward risk (0-100)** = 30% forecast load per 10k residents + 25% growth (last 6 vs previous 6 months) + 25% Service Gap Index + 20% infrastructure stress (roads + drainage + water complaints per road-km), each min-max scaled across wards.
* **What-if**: capacity = recent monthly resolutions x (1 + crew change)^0.8; backlog_t+1 = max(0, backlog_t + demand - capacity); SLA moves by 0.5 points per point of utilisation change. Assumptions are returned with every result. Not validated against real interventions.

## 11. Emerging-issue detector and topic discovery

* **Spike detection** (`app/services/emerging.py`): per category x ward, 7-day bins; recent window = last 14 days vs the previous 8 weeks; flagged when z >= 3, ratio >= 1.8 and >= 8 complaints; wards of one category are merged into an issue, severity = z x (1 + log10 volume). Found all 3 planted active bursts (one of two planted wards for the dengue surge).
* **Topics**: TF-IDF + NMF (12 topics) on the last 28 days, growth = share in the last 14 days / share in the 14 before. Replaces BERTopic to avoid heavy local models; keyword-based and weaker across scripts.
* **Summaries**: written by the fast Groq model from supplied facts only, with a deterministic template fallback.

## 12. Service Gap Index

SGI = 100 x (0.35 breach rate + 0.20 delay ratio + 0.20 backlog share + 0.15 repeat rate + 0.10 dissatisfaction), each component min-max scaled across wards. Full definition and rationale: [SERVICE_GAP_INDEX.md](SERVICE_GAP_INDEX.md). Recovered the four planted underperforming wards exactly.

## 13. LLM components (Groq)

| Component | Model setting | Inputs | Safeguards |
|---|---|---|---|
| Ask-the-data assistant | `LLM_MODEL` (gpt-oss-120b), tool calling | Question + 11 validated tools returning compact aggregates | PII stripped, tool args validated, role-scoped data, 4-round limit, JSON-planner fallback, never writes SQL |
| RAG answers | `LLM_MODEL` | Top-4 policy chunks (HF embeddings + pgvector) | Answer only from excerpts, numbered citations, "not found" when empty |
| Policy brief | `LLM_MODEL` | A ~1.5k-token facts table from the analytics engine | Prompt forbids un-given numbers/baselines; facts include `data_notes` (right-censoring) |
| Issue summaries, what-if explanation | `LLM_FAST_MODEL` | Computed facts / scenario result | Numbers quoted exactly; template fallback |

Every response carries `{ai_generated, model, provider, cached, demo_mode}`; all calls are logged (tokens, latency, cache hit, status) and cached by a hash of (purpose, model, prompt). With no `GROQ_API_KEY` the app runs on a deterministic mock provider and shows a demo-mode banner.
