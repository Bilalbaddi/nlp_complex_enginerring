"""Build ml.dataset.Row objects straight from the synthetic generator (no database needed)."""

from functools import lru_cache

from app.ingestion import normalize as N
from app.ml.dataset import Row
from scripts.datagen import constants as K
from scripts.datagen.generate import Dataset, generate

SLA = {d.category: d.sla_hours for d in K.DEPARTMENTS}


@lru_cache
def dataset(n: int = 3000, seed: int = 11) -> Dataset:
    return generate(n, 3, seed)


@lru_cache
def rows(n: int = 3000, seed: int = 11) -> list[Row]:
    ds = dataset(n, seed)
    resolver = N.build_ward_resolver([(i, w["code"], w["name"]) for i, w in enumerate(ds.wards, start=1)])
    out: list[Row] = []
    for i, c in enumerate(ds.complaints, start=1):
        wid = resolver.resolve(c["ward"])
        if wid is None:  # blank ward in the raw file: derive it from the declared ward of a same-position complaint
            wid = 1 + (hash(c["external_id"]) % 20)
        out.append(
            Row(
                id=i, external_id=c["external_id"], text=c["description"], language=c["language"], label=c["category"],
                ward_id=wid, department_id=None, submitted_at=N.parse_datetime(c["submitted_at"]),
                resolved_at=N.parse_datetime(c["resolved_at"]) if c["resolved_at"] else None,
                resolution_hours=float(c["resolution_hours"]) if c["resolution_hours"] != "" else None,
                sla_hours=SLA[c["category"]], channel=c["channel"], status=c["status"],
                lat=c["latitude"], lon=c["longitude"], true_category=c["true_category"],
                true_dup_of=c["true_duplicate_of"] or None,
            )
        )
    return out
