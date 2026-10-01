"""Generate (if needed) and load the synthetic dataset into the database via the real ingestion pipeline.

Usage (from backend/, or `python scripts/tasks.py seed [options]`):
    python -m scripts.seed                    # SEED_COMPLAINT_COUNT complaints (default 25000), idempotent
    python -m scripts.seed --complaints 5000  # smaller dataset
    python -m scripts.seed --reset            # wipe complaints/feedback/performance/documents first
"""

import argparse
import json
import mimetypes
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import ROOT_DIR, get_settings
from app.db.models import Department, Document
from app.db.session import SessionLocal
from app.ingestion import pipeline
from app.ingestion.documents import ingest_document
from app.ingestion.readers import read_rows
from app.services import storage
from scripts import generate_data
from scripts.datagen import constants as K
from scripts.datagen.generate import generate

RESET_TABLES = "document_chunks, documents, feedback, service_performance, complaints, ingestion_logs"


def load_departments(db: Session) -> None:
    for d in K.DEPARTMENTS:
        row = db.scalar(select(Department).where(Department.code == d.code))
        if row is None:
            db.add(Department(code=d.code, name=d.name, category=d.category, sla_hours=d.sla_hours))
        else:
            row.name, row.category, row.sla_hours = d.name, d.category, d.sla_hours
    db.commit()


def load_wards(db: Session, geojson_path: Path) -> int:
    fc = json.loads(geojson_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = [
        {**f["properties"], "geom": json.dumps(f["geometry"])} for f in fc["features"]
    ]
    db.execute(
        text(
            "INSERT INTO wards (code, name, population, area_sq_km, road_km, water_connections, geom) "
            "VALUES (:code, :name, :population, :area_sq_km, :road_km, :water_connections, "
            "ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))) "
            "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, population = EXCLUDED.population, "
            "area_sq_km = EXCLUDED.area_sq_km, road_km = EXCLUDED.road_km, "
            "water_connections = EXCLUDED.water_connections, geom = EXCLUDED.geom"
        ),
        rows,
    )
    db.commit()
    return len(rows)


def ensure_generated(out: Path, data_dir: Path, n: int, years: int, seed: int, regenerate: bool) -> None:
    truth = out / "ground_truth.json"
    if not regenerate and truth.exists() and (out / "complaints.csv").exists():
        t = json.loads(truth.read_text(encoding="utf-8"))
        if (t.get("complaints"), t.get("years"), t.get("seed")) == (n, years, seed):
            print(f"Using existing generated data in {out}")
            return
    print(f"Generating {n:,} complaints ({years} years, seed {seed}) ...")
    ds = generate(n, years, seed)
    generate_data.write_csv(out / "complaints.csv", generate_data.COMPLAINT_FIELDS, ds.complaints)
    generate_data.write_csv(out / "feedback.csv", generate_data.FEEDBACK_FIELDS, ds.feedback)
    generate_data.write_csv(out / "service_performance.csv", generate_data.PERF_FIELDS, ds.performance)
    truth.write_text(json.dumps(ds.truth, indent=2, ensure_ascii=False), encoding="utf-8")
    generate_data.write_reference_files(data_dir, ds, seed)


def ingest_file(db: Session, kind: str, path: Path) -> None:
    t0 = time.time()
    rows = read_rows(path.read_bytes(), path.name)
    entry = pipeline.start_log(db, kind, path.name, None)
    report = pipeline.INGESTORS[kind](db, rows, entry=entry)
    print(
        f"  {kind:<12} {report.ok:>7,} loaded, {report.duplicates:,} duplicates, {report.rejected:,} rejected, "
        f"{report.warning_count:,} warnings  ({time.time() - t0:.0f}s)"
    )
    for r in report.rejected_rows[:3]:
        print(f"    rejected row {r['row']}: {'; '.join(r['errors'])}")


def load_documents(db: Session, folder: Path) -> None:
    use_storage = get_settings().storage_configured
    bucket = get_settings().storage_bucket_documents
    for path in sorted(folder.glob("*")):
        if path.suffix.lower() not in (".pdf", ".docx"):
            continue
        title = path.stem.replace("_", " ").title()
        data = path.read_bytes()
        path_in_storage = None
        if use_storage:
            path_in_storage = storage.upload(
                bucket, f"seed/{path.name}", data, mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            )
        doc, created = ingest_document(
            db, data, path.name, title=title, doc_type="report" if "report" in path.stem else "policy",
            storage_bucket=bucket if use_storage else None, storage_path=path_in_storage,
        )
        print(f"  document     {'loaded ' if created else 'exists '} {doc.num_chunks:>3} chunks  {title}")


def main() -> None:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--complaints", type=int, default=s.seed_complaint_count)
    ap.add_argument("--years", type=int, default=s.seed_years)
    ap.add_argument("--seed", type=int, default=s.seed_random_seed)
    ap.add_argument("--data-dir", type=Path, default=ROOT_DIR / "data")
    ap.add_argument("--reset", action="store_true", help="delete existing complaints, feedback, performance and documents first")
    ap.add_argument("--regenerate", action="store_true", help="regenerate the CSV files even if they match")
    ap.add_argument("--no-documents", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    out = args.data_dir / "generated"
    ensure_generated(out, args.data_dir, args.complaints, args.years, args.seed, args.regenerate)
    with SessionLocal() as db:
        if args.reset:
            db.execute(text(f"TRUNCATE {RESET_TABLES} RESTART IDENTITY CASCADE"))
            db.commit()
            print("Existing domain data removed.")
        load_departments(db)
        print(f"Loaded {len(K.DEPARTMENTS)} departments and {load_wards(db, args.data_dir / 'wards.geojson')} wards.")
        pipeline.Refs.invalidate()
        ingest_file(db, "complaints", out / "complaints.csv")
        ingest_file(db, "feedback", out / "feedback.csv")
        ingest_file(db, "performance", out / "service_performance.csv")
        if not args.no_documents:
            load_documents(db, args.data_dir / "policies")
        db.execute(text("ANALYZE"))
        db.commit()
    print(f"Seed finished in {time.time() - t0:.0f}s. Run `python scripts/tasks.py db-size` to check storage use.")


if __name__ == "__main__":
    main()
