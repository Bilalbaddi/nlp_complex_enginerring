"""Ingestion pipelines: validate -> normalise -> resolve wards/departments -> deduplicate -> bulk insert -> audit log.

Works on lists of plain dict rows (from readers.py or an API payload). Every entry point returns an IngestReport and,
when given an IngestionLog, records the outcome there. Loading is idempotent: re-ingesting a file reports
duplicates instead of inserting them again.
"""

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, insert, select, text
from sqlalchemy.orm import Session

from app.db.models import (
    Complaint, ComplaintStatus, Department, Feedback, IngestionLog, ServicePerformance, Ward,
)
from app.ingestion import normalize as N
from app.ingestion.validators import RowResult, validate_complaint, validate_feedback, validate_performance
from app.services import cache

log = logging.getLogger(__name__)

BATCH = 1000
MAX_SAMPLES = 100  # rejected/duplicate/warning samples kept in the audit log


@dataclass
class IngestReport:
    total: int = 0
    ok: int = 0
    rejected: int = 0
    duplicates: int = 0
    rejected_rows: list[dict[str, Any]] = field(default_factory=list)
    duplicate_rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    warning_count: int = 0

    def reject(self, row_no: int, errors: list[str]) -> None:
        self.rejected += 1
        if len(self.rejected_rows) < MAX_SAMPLES:
            self.rejected_rows.append({"row": row_no, "errors": errors})

    def duplicate(self, row_no: int, why: str) -> None:
        self.duplicates += 1
        if len(self.duplicate_rows) < MAX_SAMPLES:
            self.duplicate_rows.append({"row": row_no, "reason": why})

    def warn(self, row_no: int, messages: list[str]) -> None:
        self.warning_count += len(messages)
        if len(self.warnings) < MAX_SAMPLES:
            self.warnings.append({"row": row_no, "warnings": messages})

    @property
    def status(self) -> str:
        if self.total == 0 or (self.ok == 0 and self.rejected > 0):
            return "failed"
        return "partial" if self.rejected else "success"

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total, "ok": self.ok, "rejected": self.rejected, "duplicates": self.duplicates,
            "warning_count": self.warning_count, "status": self.status,
            "rejected_rows": self.rejected_rows, "duplicate_rows": self.duplicate_rows, "warnings": self.warnings,
        }


# ------------------------------------------------------------------ audit log
def start_log(
    db: Session, source_type: str, filename: str | None, user_id: int | None, *,
    storage_bucket: str | None = None, storage_path: str | None = None, status: str = "running",
) -> IngestionLog:
    entry = IngestionLog(
        source_type=source_type, filename=filename, user_id=user_id, status=status,
        storage_bucket=storage_bucket, storage_path=storage_path,
    )
    db.add(entry)
    db.commit()
    return entry


def finish_log(db: Session, entry: IngestionLog, report: IngestReport | None = None, error: str | None = None) -> None:
    entry.finished_at = datetime.now(timezone.utc)
    if report is not None:
        entry.rows_total, entry.rows_ok = report.total, report.ok
        entry.rows_rejected, entry.rows_duplicate = report.rejected, report.duplicates
        entry.status = report.status
        entry.errors = report.as_dict()
    else:
        entry.status = "failed"
        entry.errors = {"error": error}
    db.commit()


# ------------------------------------------------------------------ reference data
@dataclass
class Refs:
    wards: N.Resolver
    depts: N.Resolver
    dept_by_category: dict[str, int]
    dept_sla: dict[int, int]

    _cached: "tuple[float, Refs] | None" = None  # process-wide, short TTL: wards/departments rarely change

    @classmethod
    def load(cls, db: Session, use_cache: bool = False) -> "Refs":
        if use_cache and cls._cached and time.monotonic() - cls._cached[0] < 60:
            return cls._cached[1]
        refs = cls._load(db)
        cls._cached = (time.monotonic(), refs)
        return refs

    @classmethod
    def invalidate(cls) -> None:
        cls._cached = None

    @classmethod
    def _load(cls, db: Session) -> "Refs":
        wards = [(w.id, w.code, w.name) for w in db.execute(select(Ward.id, Ward.code, Ward.name))]
        depts = list(db.execute(select(Department.id, Department.code, Department.name, Department.category, Department.sla_hours)))
        return cls(
            N.build_ward_resolver(wards),
            N.build_department_resolver([(d[0], d[1], d[2], d[3]) for d in depts]),
            {d[3]: d[0] for d in depts if d[3]},
            {d[0]: d[4] for d in depts},
        )


def _spatial_wards(db: Session, points: list[tuple[int, float, float]]) -> dict[int, int]:
    """Ward id for (index, lon, lat) points using PostGIS point-in-polygon, in one query."""
    if not points:
        return {}
    res = db.execute(
        text(
            "SELECT p.i, w.id FROM unnest(CAST(:idx AS int[]), CAST(:lon AS float8[]), CAST(:lat AS float8[])) "
            "AS p(i, lon, lat) JOIN wards w ON ST_Contains(w.geom, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326))"
        ),
        {"idx": [p[0] for p in points], "lon": [p[1] for p in points], "lat": [p[2] for p in points]},
    )
    return {int(i): int(w) for i, w in res}


def _chunks(items: list[Any], n: int = BATCH) -> list[list[Any]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def _hash(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ complaints
def complaint_hash(description: str, ward_id: int | None, submitted: datetime, phone: str | None, name: str | None) -> str:
    """Same text + ward + calendar day + citizen => the same complaint submitted twice."""
    return _hash(N.key(description), ward_id, submitted.astimezone(N.IST).date(), phone or "", N.key(name or ""))


def ingest_complaints(
    db: Session, rows: list[dict[str, Any]], *, entry: IngestionLog | None = None,
    progress: Callable[[int, int], None] | None = None, cache_refs: bool = False,
) -> IngestReport:
    report = IngestReport(total=len(rows))
    refs = Refs.load(db, use_cache=cache_refs)
    table_empty = not db.scalar(select(exists().where(Complaint.id.isnot(None))))
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    pending: list[tuple[int, dict[str, Any]]] = []

    def flush() -> None:
        if pending:
            _insert_complaints(db, pending, refs, report, table_empty, seen_ids, seen_hashes)
            pending.clear()

    for n, raw in enumerate(rows, start=2):  # row 1 is the header in a file
        res = validate_complaint(raw)
        if res.data is None:
            report.reject(n, res.errors)
            continue
        if res.warnings:
            report.warn(n, res.warnings)
        pending.append((n, res.data))
        if len(pending) >= BATCH:
            flush()
            if progress:
                progress(n, len(rows))
    flush()
    cache.delete_prefix("analytics:")
    if entry is not None:
        finish_log(db, entry, report)
    return report


def _insert_complaints(
    db: Session, batch: list[tuple[int, dict[str, Any]]], refs: Refs, report: IngestReport,
    table_empty: bool, seen_ids: set[str], seen_hashes: set[str],
) -> None:
    # 1) resolve wards: by name/code, else spatially from coordinates
    ward_ids: dict[int, int | None] = {}
    need_spatial: list[tuple[int, float, float]] = []
    early_rejects: set[int] = set()
    for n, d in batch:
        wid = refs.wards.resolve(d["ward_text"]) if d["ward_text"] else None
        if wid is None:
            if d["latitude"] is not None:
                need_spatial.append((n, d["longitude"], d["latitude"]))
                if d["ward_text"]:
                    report.warn(n, [f"unknown ward '{d['ward_text']}', assigned from coordinates"])
            else:
                report.reject(n, [f"ward '{d['ward_text']}' is unknown and no coordinates were given" if d["ward_text"] else "ward is missing and no coordinates were given"])
                early_rejects.add(n)
        ward_ids[n] = wid
    for n, wid in _spatial_wards(db, need_spatial).items():
        ward_ids[n] = wid
    for n, _, _ in need_spatial:
        if ward_ids.get(n) is None and n not in early_rejects:
            report.reject(n, ["coordinates are outside every ward"])
            early_rejects.add(n)

    # 2) departments, hashes, dedup
    candidates: list[tuple[int, dict[str, Any], str]] = []
    for n, d in batch:
        if n in early_rejects:
            continue
        h = complaint_hash(d["description"], ward_ids[n], d["submitted_at"], d["citizen_phone"], d["citizen_name"])
        ext = d["external_id"]
        if (ext and ext in seen_ids) or h in seen_hashes:
            report.duplicate(n, "same complaint id" if ext and ext in seen_ids else "same text, ward, day and citizen")
            continue
        seen_hashes.add(h)
        if ext:
            seen_ids.add(ext)
        candidates.append((n, d, h))

    if not table_empty and candidates:
        ids = [c[1]["external_id"] for c in candidates if c[1]["external_id"]]
        hashes = [c[2] for c in candidates]
        db_ids = set(db.scalars(select(Complaint.external_id).where(Complaint.external_id.in_(ids)))) if ids else set()
        db_hashes = set(db.scalars(select(Complaint.dedup_hash).where(Complaint.dedup_hash.in_(hashes))))
        kept = []
        for n, d, h in candidates:
            if d["external_id"] in db_ids or h in db_hashes:
                report.duplicate(n, "already in the database")
            else:
                kept.append((n, d, h))
        candidates = kept

    # 3) insert
    values: list[dict[str, Any]] = []
    for n, d, h in candidates:
        dept_id = refs.depts.resolve(d["department_text"]) if d["department_text"] else None
        if d["department_text"] and dept_id is None:
            report.warn(n, [f"unknown department '{d['department_text']}', routed by category instead"])
        if dept_id is None and d["category"]:
            dept_id = refs.dept_by_category.get(d["category"])
        values.append(
            {
                "external_id": d["external_id"], "description": d["description"], "language": d["language"],
                "category": d["category"], "status": ComplaintStatus(d["status"]), "channel": d["channel"],
                "ward_id": ward_ids[n], "department_id": dept_id, "address": d["address"],
                "location": f"SRID=4326;POINT({d['longitude']} {d['latitude']})" if d["latitude"] is not None else None,
                "submitted_at": d["submitted_at"], "resolved_at": d["resolved_at"],
                "resolution_hours": d["resolution_hours"], "sla_hours": refs.dept_sla.get(dept_id) if dept_id else None,
                "citizen_name": d["citizen_name"], "citizen_phone": d["citizen_phone"], "dedup_hash": h,
            }
        )
    if values:
        db.execute(insert(Complaint.__table__).values(values))  # one multi-row statement = one round trip
        db.commit()
    report.ok += len(values)


# ------------------------------------------------------------------ feedback
def ingest_feedback(db: Session, rows: list[dict[str, Any]], *, entry: IngestionLog | None = None) -> IngestReport:
    report = IngestReport(total=len(rows))
    refs = Refs.load(db)
    seen: set[str] = set()
    for chunk in _chunks(list(enumerate(rows, start=2))):
        valid: list[tuple[int, dict[str, Any]]] = []
        for n, raw in chunk:
            res = validate_feedback(raw)
            if res.data is None:
                report.reject(n, res.errors)
            else:
                valid.append((n, res.data))
        ext_ids = [d["complaint_external_id"] for _, d in valid if d["complaint_external_id"]]
        cmap: dict[str, tuple[int, int | None, int | None]] = {}
        if ext_ids:
            for cid, ext, wid, did in db.execute(
                select(Complaint.id, Complaint.external_id, Complaint.ward_id, Complaint.department_id).where(Complaint.external_id.in_(ext_ids))
            ):
                cmap[ext] = (cid, wid, did)
        cand: list[tuple[int, dict[str, Any], str]] = []
        for n, d in valid:
            ext = d["complaint_external_id"]
            if ext and ext not in cmap:
                report.warn(n, [f"complaint '{ext}' not found; feedback stored without a link"])
            h = _hash(ext or "", d["rating"], N.key(d["text"] or ""), d["submitted_at"].isoformat(), d["ward_text"] or "", d["department_text"] or "")
            if h in seen:
                report.duplicate(n, "same feedback appears twice in this file")
                continue
            seen.add(h)
            cand.append((n, d, h))
        if cand:
            existing = set(db.scalars(select(Feedback.dedup_hash).where(Feedback.dedup_hash.in_([c[2] for c in cand]))))
        else:
            existing = set()
        values = []
        for n, d, h in cand:
            if h in existing:
                report.duplicate(n, "already in the database")
                continue
            cid, cw, cd = cmap.get(d["complaint_external_id"] or "", (None, None, None))
            values.append(
                {
                    "complaint_id": cid, "rating": d["rating"], "text": d["text"], "language": d["language"],
                    "source": d["source"], "submitted_at": d["submitted_at"], "dedup_hash": h,
                    "ward_id": (refs.wards.resolve(d["ward_text"]) if d["ward_text"] else None) or cw,
                    "department_id": (refs.depts.resolve(d["department_text"]) if d["department_text"] else None) or cd,
                }
            )
        if values:
            db.execute(insert(Feedback.__table__).values(values))
            db.commit()
        report.ok += len(values)
    cache.delete_prefix("analytics:")
    if entry is not None:
        finish_log(db, entry, report)
    return report


# ------------------------------------------------------------------ service performance
def ingest_performance(db: Session, rows: list[dict[str, Any]], *, entry: IngestionLog | None = None) -> IngestReport:
    report = IngestReport(total=len(rows))
    refs = Refs.load(db)
    existing = {
        (d, w, p) for d, w, p in db.execute(select(ServicePerformance.department_id, ServicePerformance.ward_id, ServicePerformance.period))
    }
    values: list[dict[str, Any]] = []
    for n, raw in enumerate(rows, start=2):
        res: RowResult = validate_performance(raw)
        if res.data is None:
            report.reject(n, res.errors)
            continue
        d = res.data
        dept = refs.depts.resolve(d["department_text"])
        if dept is None:
            report.reject(n, [f"unknown department '{d['department_text']}'"])
            continue
        ward = refs.wards.resolve(d["ward_text"]) if d["ward_text"] else None
        if d["ward_text"] and ward is None:
            report.reject(n, [f"unknown ward '{d['ward_text']}'"])
            continue
        k = (dept, ward, d["period"])
        if k in existing:
            report.duplicate(n, "a record for this department, ward and month already exists")
            continue
        existing.add(k)
        values.append(
            {
                "department_id": dept, "ward_id": ward, "period": d["period"],
                "complaints_received": d["complaints_received"], "complaints_resolved": d["complaints_resolved"],
                "sla_breaches": d["sla_breaches"], "avg_resolution_hours": d["avg_resolution_hours"],
                "staff_count": d["staff_count"], "budget_spent": d["budget_spent"],
            }
        )
    for chunk in _chunks(values):
        db.execute(insert(ServicePerformance.__table__).values(chunk))
        db.commit()
    report.ok = len(values)
    cache.delete_prefix("analytics:")
    if entry is not None:
        finish_log(db, entry, report)
    return report


INGESTORS: dict[str, Callable[..., IngestReport]] = {
    "complaints": ingest_complaints,
    "feedback": ingest_feedback,
    "performance": ingest_performance,
}
