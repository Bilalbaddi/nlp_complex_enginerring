"""Embed complaints via the Hugging Face Inference API and store them in pgvector. Resumable.

Usage: python -m scripts.embed_backfill [--limit N] [--max-minutes M]
"""

import argparse
import time

from app.ml.embed_store import backfill_embeddings, count_missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="embed at most N complaints this run")
    ap.add_argument("--max-minutes", type=float, default=None)
    args = ap.parse_args()
    t0 = time.time()
    print(f"{count_missing():,} complaints without an embedding")

    def progress(done: int, total: int) -> None:
        rate = done / max(time.time() - t0, 1e-9)
        eta = (total - done) / rate / 60 if rate else 0
        print(f"  {done:,}/{total:,}  ({rate:.0f}/s, ~{eta:.0f} min left)", flush=True)

    res = backfill_embeddings(limit=args.limit, max_seconds=args.max_minutes * 60 if args.max_minutes else None, progress=progress)
    print(f"done={res['done']:,} remaining={res['remaining']:,} stopped: {res['stopped']} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
