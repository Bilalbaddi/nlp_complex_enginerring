"""Generate the synthetic Sahyadripur dataset (deterministic; templates + seeded randomness, no LLM).

Usage (from backend/):
    python -m scripts.generate_data                       # size from SEED_COMPLAINT_COUNT in .env (default 25000)
    python -m scripts.generate_data --complaints 50000 --years 3 --seed 7
    python -m scripts.generate_data --complaints 2000 --out ../data/generated_small

Writes small, committed reference files to data/ (wards.geojson, departments.csv, policies/, samples/) and the
large generated tables to data/generated/ (git-ignored).
"""

import argparse
import csv
import json
import random
import time
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import ROOT_DIR, get_settings
from scripts.datagen import constants as K
from scripts.datagen.documents import write_policy_documents
from scripts.datagen.generate import Dataset, generate
from scripts.datagen.geo import wards_geojson

COMPLAINT_FIELDS = [
    "external_id", "submitted_at", "description", "language", "category", "ward", "latitude", "longitude", "address",
    "channel", "status", "resolved_at", "resolution_hours", "department", "citizen_name", "citizen_phone",
    "true_category", "true_duplicate_of", "burst",
]
FEEDBACK_FIELDS = ["complaint_external_id", "ward", "department", "rating", "text", "language", "channel", "submitted_at"]
PERF_FIELDS = [
    "department", "ward", "period", "complaints_received", "complaints_resolved", "sla_breaches",
    "avg_resolution_hours", "staff_count", "budget_spent",
]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def messy_sample(rows: list[dict[str, Any]], seed: int, n: int = 200) -> list[dict[str, Any]]:
    """A small file with realistic data-quality problems, for the ingestion demo and validator tests."""
    py = random.Random(seed)
    out = [dict(r) for r in rows[:n]]
    cat_variants = {"water_supply": ["Water Supply", "WATER", "water"], "roads": ["Roads/Potholes", "pothole", "Road"],
                    "garbage": ["Garbage", "waste", "GARBAGE"], "drainage": ["Drainage", "sewage", "Drain"]}
    status_variants = {"closed": ["Closed", "CLOSED"], "in_progress": ["In Progress", "pending"], "open": ["Open", "new"]}
    for r in out:
        r["true_duplicate_of"] = r["burst"] = ""
        r["category"] = py.choice(cat_variants.get(r["category"], [r["category"]])) if py.random() < 0.5 else r["category"]
        r["status"] = py.choice(status_variants.get(r["status"], [r["status"]])) if py.random() < 0.5 else r["status"]
        if py.random() < 0.4 and r["submitted_at"]:  # mixed date formats (all still parseable)
            y, mth, rest = r["submitted_at"][:10].split("-")
            r["submitted_at"] = py.choice([f"{rest}/{mth}/{y} {r['submitted_at'][11:16]}", f"{int(rest)} {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(mth)-1]} {y}"])
            r["resolved_at"] = ""
    bad = py.sample(range(len(out)), 24)
    for k, i in enumerate(bad):
        r = out[i]
        if k < 5:
            r["description"] = ""  # missing text -> rejected
        elif k < 9:
            r["submitted_at"] = "not a date"  # -> rejected
        elif k < 12:
            r["latitude"], r["longitude"] = 950.0, 200.0  # impossible coordinates -> rejected
        elif k < 15:
            r["ward"], r["latitude"], r["longitude"] = "Ward 99", "", ""  # unknown ward and no coordinates -> rejected
        elif k < 18:
            r["category"] = "something odd"  # unknown category -> loaded uncategorised (classifier will label it)
        elif k < 21:
            out.append(dict(out[i]))  # exact duplicate id -> duplicate
        else:
            d = dict(out[i]); d["external_id"] = d["external_id"] + "-X"; out.append(d)  # same text/ward/day, new id
    return out


def write_reference_files(data_dir: Path, ds: Dataset, seed: int) -> None:
    (data_dir / "wards.geojson").write_text(json.dumps(wards_geojson(ds.wards), indent=1), encoding="utf-8")
    write_csv(
        data_dir / "departments.csv", ["code", "name", "category", "sla_hours"],
        [{"code": d.code, "name": d.name, "category": d.category, "sla_hours": d.sla_hours} for d in K.DEPARTMENTS],
    )
    write_csv(data_dir / "samples" / "complaints_messy_sample.csv", COMPLAINT_FIELDS, messy_sample(ds.complaints, seed))
    write_csv(data_dir / "samples" / "complaints_clean_sample.csv", COMPLAINT_FIELDS[:-3], ds.complaints[:300])
    write_csv(data_dir / "samples" / "feedback_sample.csv", FEEDBACK_FIELDS, ds.feedback[:200])
    # per-ward rows come first and city-wide rows last, so take from both ends
    write_csv(data_dir / "samples" / "service_performance_sample.csv", PERF_FIELDS, ds.performance[:150] + ds.performance[-50:])
    write_policy_documents(data_dir / "policies")


def main() -> None:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--complaints", type=int, default=s.seed_complaint_count)
    ap.add_argument("--years", type=int, default=s.seed_years)
    ap.add_argument("--seed", type=int, default=s.seed_random_seed)
    ap.add_argument("--end-date", type=date.fromisoformat, default=K.DEFAULT_END_DATE)
    ap.add_argument("--data-dir", type=Path, default=ROOT_DIR / "data")
    ap.add_argument("--out", type=Path, default=None, help="where to write the large tables (default <data-dir>/generated)")
    ap.add_argument("--reference", action="store_true", help="also (re)write the committed reference files in data/")
    args = ap.parse_args()

    t0 = time.time()
    ds = generate(args.complaints, args.years, args.seed, args.end_date)
    out = args.out or args.data_dir / "generated"
    write_csv(out / "complaints.csv", COMPLAINT_FIELDS, ds.complaints)
    write_csv(out / "feedback.csv", FEEDBACK_FIELDS, ds.feedback)
    write_csv(out / "service_performance.csv", PERF_FIELDS, ds.performance)
    (out / "ground_truth.json").write_text(json.dumps(ds.truth, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.reference or not (args.data_dir / "wards.geojson").exists():
        write_reference_files(args.data_dir, ds, args.seed)
    print(
        f"Generated {len(ds.complaints):,} complaints, {len(ds.feedback):,} feedback, {len(ds.performance):,} "
        f"performance rows in {time.time() - t0:.1f}s -> {out}"
    )


if __name__ == "__main__":
    main()
