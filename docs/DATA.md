# Synthetic dataset and ingestion

Real municipal data is unavailable, so the project generates a realistic, **fictional** mid-sized Indian city,
"Sahyadripur" (about 900,000 residents, 20 wards, 10 departments). Generation is deterministic (seeded numpy + Python
`random`), free, and uses templates only: **no LLM is involved**.

```
python scripts/tasks.py generate --complaints 50000     # files only (data/generated/)
python scripts/tasks.py seed [--reset] [--complaints N] # generate if needed + load into Supabase
```
Size comes from `SEED_COMPLAINT_COUNT` (default 50,000), `SEED_YEARS` (3), `SEED_RANDOM_SEED` (42).
Period: 1 Oct 2023 to 29 Sep 2026. Measured storage: 25,000 complaints with feedback, performance rows and indexes
use about 44 MB in Postgres (500 MB free tier); embeddings add about 0.8 KB per complaint.

## Files
| Path | Committed | Content |
|---|---|---|
| `data/wards.geojson` | yes | 20 ward polygons (jittered lattice, shared edges) with population, area, road km, water connections |
| `data/departments.csv` | yes | 10 departments with category and SLA hours |
| `data/policies/` | yes | 6 PDFs + 2 DOCX policy and report documents (used by the RAG assistant) |
| `data/samples/` | yes | small clean and deliberately messy files for demos and tests |
| `data/generated/` | no | `complaints.csv`, `feedback.csv`, `service_performance.csv`, `ground_truth.json` |

## Complaint columns
`external_id, submitted_at, description, language, category, ward, latitude, longitude, address, channel, status,
resolved_at, resolution_hours, department, citizen_name, citizen_phone` plus three **evaluation-only** columns that the
ingestion ignores: `true_category` (the real category; `category` carries about 3% label noise), `true_duplicate_of`
(id of the complaint this one near-duplicates) and `burst` (injected emerging-issue key).

Descriptions are English (about 55%), Marathi, Hinglish (romanised Hindi) and Hindi, composed from problem, location,
duration and urgency fragments, with typos and terse variants. Ward names appear in messy forms (`Shivaji Nagar`,
`W01`, `Ward 1`, upper/lower case, extra spaces) and about 3% are blank, so those rows must be located spatially.
Citizen names and phone numbers are random and fictional.

## Planted effects (ground truth, also in `ground_truth.json`)
Later phases are evaluated on whether they rediscover these.

* **Seasonality:** drainage complaints are about 4x higher in July and August than in the dry season; roads/potholes
  rise in Jul-Oct; public health in Jul-Oct; water and electricity in Apr-Jun; streetlights in winter; noise and garbage
  around Ganesh/Diwali (Sep-Nov). Volume grows 8% per year.
* **Chronic hotspots:** two spatial hotspots per ward and category for drainage, garbage, roads, water and streetlights.
* **Injected bursts** (extra complaints, spatially and temporally concentrated):
  `contam` water contamination (W07, May 2024), `strike` garbage collection breakdown (W12-W13, Mar 2025),
  `lights` street-light outage cluster (W02, Nov 2025), `flood` severe waterlogging (W09-W10, Jul 2025), and three
  **emerging now** issues: `dengue` (W05-W06, last 45 days, rising), `potholes` (Ring Road, W15/W16/W20, last 30 days,
  rising), `encroach` (market gate, W08, last 20 days).
* **Underperformance:** wards W05, W12, W17 (and W15) resolve complaints 1.4-1.9x slower; W03, W14, W20 are faster.
  Departments: Encroachment, Drainage and Roads are slowest; Solid Waste and Electricity fastest. Roads resolution
  time worsens 15% per year; monsoon slows drainage and roads by about 25-30%. A few ward x department pairs are extra slow.
* **Backlog:** low single-digit percent of old complaints never resolve, more in the weak wards and departments.
* **Near duplicates:** 3% of complaints re-report an earlier one (same ward and category, within 2 days, about 20 m away).
* **Feedback:** ratings fall as resolution time exceeds the SLA and in weak wards; text in four languages.
* **SLA compliance** across the whole dataset is about 61% (drainage 43%, roads 36%, garbage 84%).

## Ingestion pipeline
`app/ingestion/`: readers (CSV incl. `;`/tab delimiters, Excel, JSON, JSON Lines) -> header aliasing -> validators
(per-row errors and warnings) -> normalisation (many date formats assumed IST, ward/department/category/status aliases,
phone cleaning, language detection) -> ward assignment (name/code, else PostGIS point-in-polygon) -> deduplication
(same complaint id, or same text + ward + day + citizen; within the file and against the database) -> multi-row bulk
insert -> ingestion log (`ingestion_logs`: counts, rejected rows with reasons, duplicate and warning samples).
Loading is idempotent. Uploaded files are kept in a private Storage bucket; processing runs as a background job.
Documents are extracted (PDF, DOCX, TXT/MD), chunked (about 900 characters with overlap) and stored; identical
documents are detected by content hash.
