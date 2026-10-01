"""Write Phase 3 predictions for every complaint using the already-registered active models (no retraining).

Usage (from backend/): python -m scripts.predict_all
"""

from app.db.session import SessionLocal
from app.ml import enrich
from app.ml.dataset import load_rows_from_csv


def main() -> None:
    rows = load_rows_from_csv()
    print(f"Loaded {len(rows):,} complaints from the generated CSV", flush=True)
    with SessionLocal() as db:
        predictor = enrich.load_predictor(db)
    if predictor is None:
        raise SystemExit("No active category model; run `python scripts/tasks.py train` first")
    res = enrich.enrich_rows_and_save(predictor, rows, progress=lambda d, t: print(f"  saved {d:,}/{t:,}", flush=True))
    print(res, flush=True)


if __name__ == "__main__":
    main()
