# SG-DSS: Smart Governance Intelligence & Decision Support System

An AI-driven decision support platform for municipal authorities. It consolidates citizen complaints, feedback, policy documents and service-performance data; classifies and analyses them with NLP, ML and an LLM; detects emerging civic issues and service gaps; forecasts demand and resource needs; and serves role-based dashboards, GIS maps and an AI assistant.

Built as an academic *Complex Engineering Problem*. All data is **synthetic** (a fictional city, "Sahyadripur": 20 wards, 10 departments, 50,000 complaints over 3 years in English, Hindi, Marathi and Hinglish).

| | |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2 + Alembic, Pydantic v2, scikit-learn, statsmodels |
| Data | Supabase: PostgreSQL 16 + PostGIS + pgvector, and Storage (the **only** stateful service) |
| AI | Groq (`gpt-oss-120b` / `gpt-oss-20b`) for the assistant, briefs and summaries; Hugging Face Inference API for embeddings. **No model is downloaded locally** |
| Frontend | Next.js 14 (App Router), TypeScript (strict), Tailwind, Recharts, react-leaflet + OpenStreetMap |
| Auth | JWT with role-based access control (5 roles), bcrypt, audit log, PII masking, per-user LLM rate limit |

## What it does

| Module | Delivered |
|---|---|
| M1 Ingestion | CSV/Excel/JSON complaints, feedback and SLA files; PDF/DOCX policy documents; validation, normalisation, deduplication, audit log; real-time complaint endpoint |
| M2 Synthetic data | Deterministic generator (seed 42) with seasonality, hotspots, injected bursts, underperforming wards; see [docs/DATA.md](docs/DATA.md) |
| M3 NLP | Category classifier (TF-IDF vs embeddings vs Groq zero-shot), sentiment, urgency, routing, near-duplicates, entities, human-review queue |
| M4 Emerging issues | Spike detection per category x ward, topic discovery, AI summaries |
| M5 Service gaps | SLA breaches, resolution-time distribution, backlog aging, repeat hotspots, [Service Gap Index](docs/SERVICE_GAP_INDEX.md) |
| M6 GIS | Heatmap, DBSCAN hotspots, ward choropleths for any KPI, radius search, month time slider |
| M7 Forecasting | 1-6 month volume forecasts with intervals, backtests (MAE/MAPE), crew-day estimates, ward risk, what-if scenarios |
| M8 KPIs | Resolution rate, SLA %, resolution time, satisfaction, per-10k rate, repeat rate, efficiency score, MoM / YoY, rankings |
| M9 LLM | RAG over policy documents with citations, tool-calling "Ask the data" assistant (no SQL), monthly policy brief, what-if explanation |
| M10 Dashboards | 13 pages, role-specific views, light/dark mode, responsive, live feed, CSV / PDF reports |
| M11 Security | RBAC on every endpoint, audit log, PII masking and stripping before any external call, row-level security, validation |

Documentation: [Architecture](docs/ARCHITECTURE.md) · [Database / ER diagram](docs/DATABASE.md) · [API reference](docs/API.md) · [Model cards](docs/MODEL_CARDS.md) · [Evaluation report](docs/EVALUATION.md) · [Service Gap Index](docs/SERVICE_GAP_INDEX.md) · [Synthetic data](docs/DATA.md) · `CLAUDE.md` (decisions and build notes).

## Prerequisites

* Python 3.11+ and Node.js 20+ (no Docker, Redis or Celery needed)
* A free **Supabase** project (database + storage)
* A free **Groq API key** (the AI features; without one the app runs in *demo mode* with template text)
* A free **Hugging Face token** (embeddings for RAG and duplicate search; without one the app falls back to keyword search)

### Getting the keys

**Supabase** (https://supabase.com): create a project, then
1. *Connect* > **Transaction pooler** (port 6543) -> `DATABASE_URL`; the same host on **port 5432** (*Session pooler*) -> `MIGRATION_DATABASE_URL`. URL-encode special characters in the DB password (`@` -> `%40`). The direct `db.<ref>.supabase.co` host is IPv6-only on the free tier and usually unreachable.
2. *Project Settings > API*: **Project URL** -> `SUPABASE_URL`; the secret **service_role** key -> `SUPABASE_SERVICE_ROLE_KEY` (backend only, never in the frontend).
3. Enable nothing by hand: migration 0001 runs `CREATE EXTENSION postgis / vector`, and the API creates the private Storage buckets.

**Groq API key** (https://console.groq.com):
1. Sign in, open **API Keys**, click **Create API Key**, copy it (shown once).
2. Put it in `.env`: `GROQ_API_KEY=gsk_...` and set `LLM_PROVIDER=groq`.
3. Choose models from https://console.groq.com/docs/models (names change over time, so none are hardcoded). The project was built and tested with `LLM_MODEL=openai/gpt-oss-120b` (assistant, briefs) and `LLM_FAST_MODEL=openai/gpt-oss-20b` (summaries, zero-shot classification).
4. Free-tier limit: about **8,000 tokens per minute** per model. The app retries on HTTP 429 with the server's `Retry-After`, caches AI output (briefs 24 h, summaries 6 h) and keeps prompts compact; if you see "rate limited", wait a minute.

**Hugging Face token** (https://huggingface.co/settings/tokens): create a fine-grained token with permission *Make calls to Inference Providers* -> `HUGGINGFACEHUB_API_TOKEN`.

## Setup

```powershell
python scripts/tasks.py setup          # creates .env (with a generated JWT_SECRET), backend/.venv, installs Python + npm packages
# edit .env: Supabase values, GROQ_API_KEY, LLM_PROVIDER=groq, LLM_MODEL, LLM_FAST_MODEL, HUGGINGFACEHUB_API_TOKEN
python scripts/tasks.py migrate        # tables, extensions, row-level security
python scripts/tasks.py seed-users     # one demo account per role
python scripts/tasks.py seed           # ~6 min: 50,000 synthetic complaints + feedback + KPIs + 8 policy documents
python scripts/tasks.py train          # train classifiers + breach-risk + duplicates, register them, write predictions (~15 min; add --no-embeddings and/or --no-hashing to skip the slow comparison models)
python scripts/tasks.py groq-eval      # optional: Groq zero-shot classifier comparison (200 complaints)
```

`seed` accepts `--complaints 5000` for a quick demo set and `--reset` to reload. If you already have trained models and only want predictions: `python scripts/tasks.py predict`. Policy-document embeddings are created automatically the first time the assistant searches the documents (or via Admin > Jobs > "Embed new document chunks").

## Run

```powershell
python scripts/tasks.py dev-backend    # API http://localhost:8000   (interactive docs at /docs)
python scripts/tasks.py dev-frontend   # UI  http://localhost:3000   (must be port 3000: it is the allowed CORS origin)
python scripts/tasks.py check          # calls /health
```

Background jobs run inline in the API process during development (`JOBS_INLINE=true`); for a separate worker set `JOBS_INLINE=false` and run `python scripts/tasks.py worker`. The first request after start (or after a long idle) can take several seconds while the database connection pool and the analytics cache warm up.

**Demo accounts** (password `Demo@12345`; buttons on the login page fill them in):

| Account | Role | Scope |
|---|---|---|
| `commissioner@sgdss.org` | Commissioner | whole city |
| `dept.head@sgdss.org` | Department head | Drainage department |
| `ward.officer@sgdss.org` | Ward officer | Ward W05 (Ambedkar Nagar) |
| `analyst@sgdss.org` | Analyst | whole city + ingestion, model monitoring |
| `admin@sgdss.org` | Administrator | everything + users, audit log, jobs |

## Demo walkthrough (about 10 minutes)

The data ends on 29 Sep 2026, and all analytics are "as of" that date. The data contains planted effects you can rediscover.

1. **Commissioner: Overview.** Eight KPI tiles with change vs last month, a 24-month complaint trend (monsoon peaks), the **emerging issues** card, a 6-month forecast with a shaded 95% interval, and **AI policy brief** -> *Generate brief* (about 20 s the first time; labelled AI-generated with the model name).
2. **Emerging Issues.** Three live spikes: encroachment at the Old City market gate (W08), a Ring Road pothole surge (W15/W16/W20) and a public-health surge (W06). Each shows affected wards, x-times-normal ratios, sample complaints and an AI summary.
3. **GIS Map.** Colour wards by *Service Gap Index*; switch on *Hotspots*; drag the time slider. W17, W05, W12 and W15 light up red: they are the planted underperformers.
4. **Service Gaps.** The index with its components, SLA breaches by department (Roads and Drainage worst), the resolution-time histogram, backlog aging (a large 30-day-plus tail) and repeat-complaint hotspots.
5. **Forecasts.** City and per-category forecasts with backtest error; *Resource needs* (crew-days vs capacity; try overriding a ratio); *Infrastructure risk*; then *What-if scenarios*: Roads, +20% crews, run it and read the AI explanation next to the computed numbers.
6. **AI Assistant.** Ask: *"Which wards had the worst drainage performance last monsoon?"* (watch the "data sources" chip: `rank wards`, then a streamed answer). Then *Ask the policy documents*: *"What is the target time to repair a pothole?"* (answer cites the SOP with page numbers).
7. **Complaints Explorer.** Filter by language or *Needs review*; open a complaint to see the predicted category and confidence, urgency with reasons, sentiment and detected entities; confirm a category for a flagged complaint (this is audited).
8. **Department head** (Drainage): Overview shows only drainage data, SLA breaches by ward, backlog and the department's crew shortfalls. **Ward officer** (W05): Overview, map and live feed are limited to the ward; the Reports and Admin pages are not in the menu.
9. **Analyst: Data Ingestion.** Upload `data/samples/complaints_messy_sample.csv`: the audit log reports accepted, rejected and duplicate rows; try *Register one complaint* and watch it appear in the live feed. **Model Monitoring** shows the classifier comparison, confusion matrix, review queue and LLM usage (calls, tokens, latency, cache hit rate).
10. **Admin.** Users and roles, audit log (every login, review, upload), background jobs, master data. **Reports** (commissioner/analyst): build a report, *CSV*, or *Save as PDF*.

Toggle dark mode with the moon icon; resize the window to see the responsive layout.

## Commands

`python scripts/tasks.py help` lists everything:

| Command | Purpose |
|---|---|
| `setup`, `migrate`, `revision "msg"` | Environment, migrations (read generated migrations for stray `drop_*`) |
| `seed-users`, `seed`, `generate` | Demo accounts, load synthetic data, only write data files |
| `train`, `predict`, `groq-eval`, `embed` | ML training and predictions, Groq comparison, embedding backfill |
| `dev-backend`, `dev-frontend`, `worker`, `check` | Run services, health check |
| `test`, `cov` | Tests; tests + coverage for services/ml/llm/nlp/core/ingestion |
| `db-size` | Database size against the 500 MB free tier (currently ~185 MB) |

Docs generators: `cd backend && python -m scripts.gen_api_docs` (API reference) and `python -m scripts.make_eval_doc` (evaluation report, recomputed from the live database). Frontend (in `frontend/`): `npm run dev | build | typecheck | lint | test | check`.

## Tests

```powershell
python scripts/tasks.py test            # backend pytest + frontend typecheck + Vitest
python scripts/tasks.py cov             # with coverage (about 15 minutes, because the database is remote)
```

Backend tests run against your Supabase database in an isolated `sgdss_test` schema (recreated each run) with real Storage throwaway objects. **They never call Groq or Hugging Face** (a fake client or scripted provider is used). Measured coverage: services 93%, ML 79%, all measured packages 89% (target 70%). Run a single file while iterating: `python scripts/tasks.py test tests/test_cache.py`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "AI features are in demo mode" banner | `GROQ_API_KEY` empty or `LLM_PROVIDER=mock`. Set both and restart the API |
| `Groq 429` in the log, slow AI answers | Free-tier token limit; the app waits and retries. Ask shorter questions or wait a minute |
| Assistant says the AI service is unavailable | Groq key invalid or network blocked: check `/api/v1/llm/status` and the API log |
| API cannot connect to the database | Use the **pooler** hosts, not `db.<ref>.supabase.co` (IPv6-only); URL-encode the password |
| Login or first page takes 5-100 s | The remote database pool is reconnecting after idle time; later calls are fast |
| Frontend shows CORS errors | The UI must run on `http://localhost:3000` (or add your origin to `CORS_ORIGINS`) |
| Port 8000 already in use | Another local server owns it; run `uvicorn app.main:app --port 8010` and set `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS` accordingly |
| Stale behaviour after editing code on OneDrive | `uvicorn --reload` can miss edits on OneDrive folders: restart `dev-backend` |
| Demo logins stop working | `python scripts/tasks.py seed-users` |
| Maps show no tiles | OpenStreetMap tiles need internet access |

## Security and limitations (short version)

JWT tokens are kept in `localStorage` and cannot be revoked server-side; PII masking is regex-based; the in-process cache can serve data up to five minutes old; the data is synthetic and the metrics are optimistic. The full list is in [docs/EVALUATION.md](docs/EVALUATION.md#9-limitations).

## Layout

```
backend/app/    api (routers) · core (config, security, ratelimit, pii) · db (models, migrations) · ingestion · llm (providers, tools, RAG, prompts/) · ml · nlp · services · workers
backend/scripts/ seed, data generator, training, evaluation and documentation generators
backend/tests/   pytest suite (Groq mocked)
frontend/src/    app/(app)/* pages · components · lib (api client, hooks, SSE, theme, nav)
data/            ward GeoJSON, departments, samples, policy and report documents
docs/            architecture, database, API, model cards, evaluation, data, Service Gap Index
scripts/tasks.py cross-platform task runner (replaces the Makefile)
docker-compose.yml  optional local Postgres + services (not required)
```
