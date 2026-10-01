# Database

PostgreSQL 16 on Supabase with the **PostGIS** and **pgvector** extensions (enabled by migration `0001`, never by hand). Schema changes are Alembic migrations in `backend/app/db/migrations/versions/` (`0001`-`0005`). Enums are stored as strings, not native PG enums. Every table has **row-level security enabled with no policies**: only the backend's direct database role can read or write.

## Entity-relationship diagram

```mermaid
erDiagram
    DEPARTMENTS ||--o{ USERS : "employs"
    WARDS ||--o{ USERS : "assigned to"
    WARDS ||--o{ COMPLAINTS : "contains"
    DEPARTMENTS ||--o{ COMPLAINTS : "handles"
    DEPARTMENTS ||--o{ COMPLAINTS : "predicted route"
    USERS ||--o{ COMPLAINTS : "reviews"
    COMPLAINTS ||--o{ COMPLAINTS : "duplicate_of"
    COMPLAINTS ||--o{ FEEDBACK : "rated by"
    WARDS ||--o{ FEEDBACK : "about"
    DEPARTMENTS ||--o{ FEEDBACK : "about"
    DEPARTMENTS ||--o{ SERVICE_PERFORMANCE : "monthly KPIs"
    WARDS ||--o{ SERVICE_PERFORMANCE : "monthly KPIs"
    USERS ||--o{ DOCUMENTS : "uploads"
    DOCUMENTS ||--o{ DOCUMENT_CHUNKS : "split into"
    USERS ||--o{ AUDIT_LOGS : "performs"
    USERS ||--o{ INGESTION_LOGS : "runs"
    USERS ||--o{ LLM_CALL_LOGS : "triggers"
    USERS ||--o{ JOBS : "creates"

    USERS {
        int id PK
        string email UK
        string full_name
        string hashed_password
        string role "commissioner|department_head|ward_officer|analyst|admin"
        int department_id FK
        int ward_id FK
        bool is_active
        timestamptz last_login_at
    }
    DEPARTMENTS {
        int id PK
        string code UK
        string name UK
        int sla_hours
        string category UK "complaint category it serves"
    }
    WARDS {
        int id PK
        string code UK
        string name UK
        int population
        float area_sq_km
        float road_km
        int water_connections
        multipolygon geom "PostGIS, GiST index"
    }
    COMPLAINTS {
        int id PK
        string external_id UK
        text description
        string language "en|hi|mr|hinglish"
        string category "reported"
        string status
        string channel
        int ward_id FK
        int department_id FK
        point location "PostGIS, GiST index"
        timestamptz submitted_at
        timestamptz resolved_at
        float resolution_hours
        int sla_hours
        string citizen_name "PII"
        string citizen_phone "PII"
        string predicted_category "NLP"
        float category_confidence "NLP"
        int predicted_department_id FK
        float sentiment_score "NLP"
        float urgency_score "NLP 0-100"
        bool needs_review "human review queue"
        string review_reason
        timestamptz reviewed_at
        int reviewed_by_id FK
        int duplicate_of_id FK
        jsonb entities "landmarks, roads, areas"
        halfvec384 embedding "pgvector"
        string dedup_hash
    }
    FEEDBACK {
        int id PK
        int complaint_id FK
        int ward_id FK
        int department_id FK
        int rating "1-5"
        text text
        float sentiment_score
        timestamptz submitted_at
        string dedup_hash
    }
    SERVICE_PERFORMANCE {
        int id PK
        int department_id FK
        int ward_id FK "NULL = city-wide"
        date period "first day of month"
        int complaints_received
        int complaints_resolved
        int sla_breaches
        float avg_resolution_hours
        int staff_count
        float budget_spent
    }
    DOCUMENTS {
        int id PK
        string title
        string doc_type "policy|report"
        string content_hash UK
        int num_chunks
        string storage_path "Supabase Storage"
        int uploaded_by FK
    }
    DOCUMENT_CHUNKS {
        int id PK
        int document_id FK
        int chunk_index
        int page
        text content
        halfvec384 embedding "HNSW cosine index"
    }
    INGESTION_LOGS {
        int id PK
        string source_type
        string filename
        string status
        int rows_total
        int rows_ok
        int rows_rejected
        int rows_duplicate
        jsonb errors
        int user_id FK
    }
    AUDIT_LOGS {
        int id PK
        int user_id FK
        string action
        string resource_type
        string resource_id
        jsonb detail
        string ip_address
    }
    LLM_CALL_LOGS {
        int id PK
        string provider
        string model
        string purpose
        int prompt_tokens
        int completion_tokens
        int latency_ms
        bool cache_hit
        string status
        int user_id FK
    }
    MODEL_RUNS {
        int id PK
        string name
        string task
        string version
        jsonb metrics
        jsonb params
        int n_train
        int n_test
        string artifact_path "Supabase Storage"
        bool is_active
    }
    JOBS {
        int id PK
        string type
        jsonb payload
        string status "queued|running|succeeded|failed"
        int attempts
        jsonb result
        int created_by FK
    }
    CACHE_ENTRIES {
        string key PK
        jsonb value
        timestamptz expires_at
    }
    RATE_LIMITS {
        string key
        timestamptz window_start
        int count
    }
```

`model_runs`, `cache_entries` and `rate_limits` have no foreign keys (they are infrastructure tables).

## Indexes worth knowing

| Index | Purpose |
|---|---|
| `complaints(ward_id, submitted_at)`, `(category, submitted_at)`, `(department_id, status)` | Filtering and the analytics cube |
| GiST on `complaints.location` and `wards.geom` | Point-in-polygon ward assignment, radius search (`ST_DWithin`), heatmaps |
| HNSW (`halfvec_cosine_ops`) on `document_chunks.embedding` | RAG retrieval |
| none on `complaints.embedding` | Duplicate search prefilters by ward and a 72-hour window, then compares exactly (keeps storage low) |
| Partial unique `service_performance(department_id, period) WHERE ward_id IS NULL` | One city-wide row per department per month |
| `complaints.dedup_hash`, `feedback.dedup_hash` | Idempotent re-imports (duplicates are detected on text + ward + day + citizen) |

## Data lifecycle

1. `scripts/generate_data.py` writes deterministic CSV/GeoJSON/PDF/DOCX files (seed 42) under `data/generated/` (git-ignored).
2. `tasks.py seed` loads them through the real ingestion pipeline (validation, normalisation, ward assignment, dedup).
3. `tasks.py train` trains the models and `tasks.py predict` writes `predicted_category`, `category_confidence`, `sentiment_score`, `urgency_score`, `needs_review`, `entities`, `duplicate_of_id` back to `complaints`.
4. Embeddings (`complaints.embedding`, `document_chunks.embedding`) are computed through the Hugging Face API and stored as `halfvec(384)`.
5. Size at the end of the build: 50,000 complaints, 20,142 feedback, 7,290 performance rows, 8 documents (20 chunks) = **184.6 MB of the 500 MB** free tier (`tasks.py db-size`).
