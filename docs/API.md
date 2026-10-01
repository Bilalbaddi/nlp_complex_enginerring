# API reference

_Generated from the running app by `backend/scripts/gen_api_docs.py`. Interactive docs with request/response schemas: `http://localhost:8000/docs` (OpenAPI JSON at `/openapi.json`)._

All paths are under `/api/v1`. Every response uses the envelope `{ "success": bool, "data": ..., "error": { "code", "message", "details" } | null }`; list endpoints return `data: { items: [...], meta: { page, page_size, total, pages } }`. Authenticate with `Authorization: Bearer <access token>` (from `POST /auth/login`). `admin` can call everything. Ward officers and department heads are automatically scoped to their ward / department on data endpoints. Errors: 401 not signed in, 403 role not allowed, 404 not found, 409 duplicate, 413/415 bad upload, 422 validation, 429 rate limited, 503 LLM unavailable.

**58 endpoints.**

## System

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/health` | public | - | Database, PostGIS/pgvector extensions, Supabase Storage, job mode and LLM demo-mode flag. |

## Auth

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| POST | `/auth/login` | public | body: LoginRequest | Login |
| POST | `/auth/logout` | any signed-in user | - | Tokens are stateless; the client discards them. We record the event for the audit trail. |
| GET | `/auth/me` | any signed-in user | - | Me |
| POST | `/auth/refresh` | public | body: RefreshRequest | Refresh |

## Master Data

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/departments` | any signed-in user | - | List departments |
| GET | `/wards` | any signed-in user | - | List wards |
| GET | `/wards/geojson` | any signed-in user | - | Ward boundaries as a GeoJSON FeatureCollection (for map layers). Cached for an hour. |

## Complaints

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/complaints` | any signed-in user | query: page, page_size, ward_id, department_id, category, status, language, date_from, date_to, q, needs_review, sort | List complaints |
| POST | `/complaints` | any signed-in user | body: ComplaintCreate | Register one complaint (the real-time feed). Goes through the same validation and dedup as file imports. |
| GET | `/complaints/{complaint_id}` | any signed-in user | path: complaint_id | Get complaint |

## Ingestion

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/documents` | any signed-in user | query: page, page_size, doc_type | List documents |
| POST | `/documents` | analyst (+ admin) | body: UploadFile, str, Literal | Upload document |
| DELETE | `/documents/{doc_id}` | admin only | path: doc_id | Delete document |
| GET | `/documents/{doc_id}/download` | any signed-in user | path: doc_id | A short-lived signed URL (5 minutes) for the original file; buckets are private. |
| POST | `/ingest/{kind}` | analyst (+ admin) | path: kind; body: UploadFile | Upload table |
| GET | `/ingestion-logs` | analyst (+ admin) | query: page, page_size, source_type | List logs |
| GET | `/ingestion-logs/{log_id}` | analyst (+ admin) | path: log_id | Get log |

## Nlp

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| POST | `/nlp/analyze` | any signed-in user | body: AnalyzeRequest | Analyse free text with the active models (nothing is stored). Uses the offline TF-IDF model only. |
| GET | `/nlp/models` | analyst (+ admin) | query: active_only | List models |
| GET | `/nlp/review/stats` | analyst, dept head, ward officer (+ admin) | - | Review stats |
| POST | `/nlp/review/{complaint_id}` | analyst, dept head, ward officer (+ admin) | path: complaint_id; body: ReviewRequest | Human decision for a flagged complaint: set the correct category (routes it to that department) and clear the flag. |

## Analytics

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/emerging-issues` | any signed-in user | query: category, ward_id | Emerging issues |
| GET | `/emerging-issues/topics` | any signed-in user | - | Emerging topics |
| GET | `/kpis/rankings` | any signed-in user | query: by, metric, months, month, category | Kpi rankings |
| GET | `/kpis/summary` | any signed-in user | query: ward_id, category, months, month | Kpi summary |
| GET | `/kpis/trend` | any signed-in user | query: ward_id, category, date_from, date_to | Kpi trend |
| GET | `/service-gaps/backlog` | any signed-in user | query: ward_id, category | Backlog aging |
| GET | `/service-gaps/distribution` | any signed-in user | query: months, ward_id, category | Resolution distribution |
| GET | `/service-gaps/index` | any signed-in user | query: months, category | Gap index |
| GET | `/service-gaps/repeat-hotspots` | any signed-in user | query: days, min_complaints, ward_id, category | Repeat hotspots |
| GET | `/service-gaps/sla` | any signed-in user | query: by, months, ward_id, category | Sla breaches |

## Gis

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/gis/choropleth` | any signed-in user | query: metric, category, date_from, date_to | Choropleth |
| GET | `/gis/heatmap` | any signed-in user | query: category, ward_id, date_from, date_to, cell | Heatmap |
| GET | `/gis/hotspots` | any signed-in user | query: category, ward_id, date_from, date_to, eps_m, min_samples | Hotspots |
| GET | `/gis/wards/{ward_id}/summary` | any signed-in user | path: ward_id | Ward summary |
| GET | `/gis/within` | any signed-in user | query: lat, lon, radius_m, category, limit | Within |

## Forecasting

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/forecasts/backtest` | analyst, commissioner (+ admin) | - | Rolling-origin backtest (MAE / MAPE) of every candidate model, per category. |
| GET | `/forecasts/resources` | analyst, commissioner, dept head (+ admin) | query: horizon, ratios | Forecast resources |
| GET | `/forecasts/risk` | any signed-in user | - | Forecast risk |
| GET | `/forecasts/volume` | any signed-in user | query: category, ward_id, horizon | Forecast volume |
| POST | `/scenarios/what-if` | analyst, commissioner, dept head (+ admin) | body: WhatIfRequest | What if |

## Llm

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| POST | `/assistant/ask` | any signed-in user | body: ChatRequest | Same as /assistant/chat but returns one JSON answer (no streaming). |
| POST | `/assistant/chat` | any signed-in user | body: ChatRequest | Server-Sent Events stream: `tool` events, `token` events (answer text), then `done` (data sources + AI label). |
| POST | `/insights/ask-documents` | any signed-in user | body: QuestionRequest | RAG: answer from the policy documents with numbered source citations. |
| GET | `/insights/brief` | analyst, commissioner, dept head (+ admin) | query: refresh | Monthly executive policy brief (AI-written from analytics-engine numbers). Cached for 24 h. |
| POST | `/insights/documents/embed` | analyst (+ admin) | - | Embed document chunks that have no vector yet (Hugging Face API; up to 200 per call). |
| GET | `/llm/status` | any signed-in user | - | Llm status |
| GET | `/llm/usage` | analyst (+ admin) | query: days | LLM monitoring: calls, tokens, latency and cache hit rate (from llm_call_logs). |
| POST | `/scenarios/explain` | analyst, commissioner, dept head (+ admin) | body: WhatIfRequest | Run the what-if model (Python numbers) and have the LLM explain the result. |

## Jobs

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/jobs` | admin only | query: page, page_size, status | List jobs |
| POST | `/jobs` | admin only | body: JobCreate | Create job |
| GET | `/jobs/types` | admin only | - | Job types |
| GET | `/jobs/{job_id}` | admin only | path: job_id | Get job |

## Admin

| Method | Path | Access | Parameters | What it does |
|---|---|---|---|---|
| GET | `/audit-logs` | admin only | query: page, page_size, action | List audit logs |
| GET | `/users` | admin only | query: page, page_size, role, q | List users |
| POST | `/users` | admin only | body: UserCreate | Create user |
| PATCH | `/users/{user_id}` | admin only | path: user_id; body: UserUpdate | Update user |

## Streaming: `POST /assistant/chat`

Server-Sent Events over a POST. Body `{ "message": str, "history": [{"role": "user"|"assistant", "content": str}] }`. Events (each `data: <json>`):

```
data: {"type": "tool", "name": "rank_wards", "args": {...}}        # a data function the AI called
data: {"type": "token", "text": "Worst drainage ..."}              # answer text, in chunks
data: {"type": "done", "tools_used": [...], "label": {"ai_generated": true, "model": "...", "demo_mode": false}}
data: {"type": "error", "message": "..."}                          # instead of done on failure
data: [DONE]
```

## Assistant tools (what the AI is allowed to call)

`get_kpis`, `rank_wards`, `rank_departments`, `sla_breaches`, `service_gap_index`, `backlog`, `emerging_issues`, `forecast_volume`, `resource_forecast`, `ward_risk`, `search_policy_documents`. Arguments are validated, results are truncated to 3,500 characters, and the same role scoping as the REST endpoints applies. The AI cannot run SQL or any other function.
