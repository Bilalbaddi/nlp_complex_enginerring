"""Load complaints for training/evaluation, and attach the generator's evaluation-only ground truth when available."""

import csv
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import ROOT_DIR
from app.db.session import AutocommitSession

T = TypeVar("T")
TRUTH_CSV = ROOT_DIR / "data" / "generated" / "complaints.csv"


@dataclass
class Row:
    id: int
    external_id: str | None
    text: str
    language: str | None
    label: str | None  # the category recorded on the complaint (about 3% noisy in the synthetic data)
    ward_id: int | None
    department_id: int | None
    submitted_at: datetime
    resolved_at: datetime | None
    resolution_hours: float | None
    sla_hours: int | None
    channel: str | None
    status: str
    lat: float | None
    lon: float | None
    true_category: str | None = None  # evaluation only
    true_dup_of: str | None = None  # evaluation only (external id of the original complaint)


_PAGE_SQL = text(
    "SELECT id, external_id, description, language, category, ward_id, department_id, submitted_at, resolved_at, "
    "resolution_hours, sla_hours, channel, status, ST_Y(location) AS lat, ST_X(location) AS lon "
    "FROM complaints WHERE id > :last ORDER BY id LIMIT :n"
)
PAGE = 4000


def with_retry(fn: Callable[[], T], attempts: int = 4) -> T:
    """The remote pooler occasionally drops a connection mid-query; retry on a fresh connection."""
    for k in range(attempts):
        try:
            return fn()
        except OperationalError:
            if k == attempts - 1:
                raise
            time.sleep(1.5 * (k + 1))
    raise RuntimeError("unreachable")


def load_rows() -> list[Row]:
    """All complaints, read in keyset-paged chunks (one huge result set gets cut off by the pooler), sorted by time."""
    rows: list[Row] = []
    last = 0
    while True:
        def page(last_id: int = last) -> list[Any]:
            with AutocommitSession() as db:
                return list(db.execute(_PAGE_SQL, {"last": last_id, "n": PAGE}).all())

        chunk = with_retry(page)
        if not chunk:
            break
        rows += [
            Row(r.id, r.external_id, r.description, r.language, r.category, r.ward_id, r.department_id, r.submitted_at,
                r.resolved_at, r.resolution_hours, r.sla_hours, r.channel, r.status, r.lat, r.lon)
            for r in chunk
        ]
        last = chunk[-1].id
    rows.sort(key=lambda r: (r.submitted_at, r.id))
    return rows


def attach_truth(rows: list[Row], path: Path = TRUTH_CSV) -> int:
    """Fill true_category / true_dup_of from the generated CSV. Returns how many rows were matched."""
    if not path.exists():
        return 0
    truth = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            truth[r["external_id"]] = (r.get("true_category") or None, r.get("true_duplicate_of") or None)
    n = 0
    for row in rows:
        if row.external_id in truth:
            row.true_category, row.true_dup_of = truth[row.external_id]
            n += 1
    return n


def load_embeddings(ids: list[int]) -> np.ndarray | None:
    """Stored HF embeddings for the given complaint ids as an (n, dim) float32 array; rows without one are NaN."""
    from app.core.config import get_settings

    dim = get_settings().embedding_dim
    out = np.full((len(ids), dim), np.nan, dtype=np.float32)
    pos = {cid: i for i, cid in enumerate(ids)}
    for start in range(0, len(ids), 1000):
        chunk = ids[start : start + 1000]

        def fetch(c: list[int] = chunk) -> list[Any]:
            with AutocommitSession() as db:
                return list(db.execute(
                    text("SELECT id, embedding::text FROM complaints WHERE id = ANY(:ids) AND embedding IS NOT NULL"), {"ids": c}
                ).all())

        for cid, emb in with_retry(fetch):
            out[pos[cid]] = np.fromstring(emb.strip("[]"), sep=",", dtype=np.float32)
    return out if not np.isnan(out).all() else None


def load_rows_from_csv(path: Path = TRUTH_CSV) -> list[Row]:
    """The same complaints that were seeded, read from the local generated CSV (no slow bulk download from Supabase).

    Row.id is the 1-based position in time order; results are written back to the database by external_id.
    """
    import json

    from app.ingestion import normalize as N

    geo = json.loads((ROOT_DIR / "data" / "wards.geojson").read_text(encoding="utf-8"))
    rings = {int(f["properties"]["code"][1:]): f["geometry"]["coordinates"][0] for f in geo["features"]}
    resolver = N.build_ward_resolver([(int(f["properties"]["code"][1:]), f["properties"]["code"], f["properties"]["name"]) for f in geo["features"]])
    sla = {"water_supply": 48, "drainage": 72, "roads": 120, "garbage": 24, "streetlights": 72, "encroachment": 168,
           "public_health": 48, "electricity": 24, "parks": 168, "noise": 48}

    def inside(ring: list[list[float]], x: float, y: float) -> bool:
        ins = False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                ins = not ins
        return ins

    out: list[Row] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lat, lon = float(r["latitude"]), float(r["longitude"])
            wid = resolver.resolve(r["ward"]) or next((w for w, ring in rings.items() if inside(ring, lon, lat)), None)
            cat = r["category"] or None
            out.append(Row(
                id=0, external_id=r["external_id"], text=r["description"], language=r["language"], label=cat, ward_id=wid,
                department_id=None, submitted_at=N.parse_datetime(r["submitted_at"]),
                resolved_at=N.parse_datetime(r["resolved_at"]) if r["resolved_at"] else None,
                resolution_hours=float(r["resolution_hours"]) if r["resolution_hours"] else None,
                sla_hours=sla.get(cat or ""), channel=r["channel"], status=r["status"], lat=lat, lon=lon,
                true_category=r["true_category"] or None, true_dup_of=r["true_duplicate_of"] or None,
            ))
    out.sort(key=lambda x: (x.submitted_at, x.external_id or ""))
    for i, row in enumerate(out, start=1):
        row.id = i
    return out
