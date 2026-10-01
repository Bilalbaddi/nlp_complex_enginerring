import csv
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import Complaint, Document, DocumentChunk, Feedback, IngestionLog, ServicePerformance
from app.db.session import SessionLocal
from app.ingestion import pipeline
from app.ingestion.documents import DocumentError, chunk_pages, extract_pages, ingest_document
from app.ingestion.readers import read_rows
from tests.conftest import DATA_DIR

SAMPLES = DATA_DIR / "samples"
POLICIES = DATA_DIR / "policies"


def _rows(name: str) -> list[dict[str, str]]:
    return read_rows((SAMPLES / name).read_bytes(), name)


def _ingest(kind: str, name: str) -> pipeline.IngestReport:
    with SessionLocal() as db:
        entry = pipeline.start_log(db, kind, name, None)
        return pipeline.INGESTORS[kind](db, _rows(name), entry=entry)


def test_clean_complaints_load_with_wards_departments_and_geometry(city: None) -> None:
    report = _ingest("complaints", "complaints_clean_sample.csv")
    assert (report.total, report.ok, report.rejected, report.duplicates) == (300, 300, 0, 0)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Complaint)) == 300
        assert db.scalar(select(func.count()).where(Complaint.ward_id.is_(None))) == 0  # blank wards assigned spatially
        assert db.scalar(select(func.count()).where(Complaint.location.is_(None))) == 0
        assert db.scalar(select(func.count()).where(Complaint.department_id.is_(None))) == 0
        assert db.scalar(select(func.count()).where(Complaint.sla_hours.is_(None))) == 0
        assert set(db.scalars(select(Complaint.language).distinct())) <= {"en", "hi", "mr", "hinglish"}
        log = db.scalars(select(IngestionLog)).one()
        assert (log.status, log.rows_total, log.rows_ok, log.finished_at is not None) == ("success", 300, 300, True)


def test_spatial_assignment_matches_declared_ward(city: None) -> None:
    """Rows that name a ward and rows with a blank ward but the same coordinates must resolve consistently."""
    _ingest("complaints", "complaints_clean_sample.csv")
    with SessionLocal() as db:
        from sqlalchemy import text

        bad = db.execute(
            text("SELECT count(*) FROM complaints c JOIN wards w ON w.id = c.ward_id WHERE NOT ST_Contains(w.geom, c.location)")
        ).scalar()
    assert bad == 0


def test_reingest_is_idempotent(city: None) -> None:
    _ingest("complaints", "complaints_clean_sample.csv")
    again = _ingest("complaints", "complaints_clean_sample.csv")
    assert (again.ok, again.duplicates, again.rejected) == (0, 300, 0)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Complaint)) == 300


def test_messy_file_reports_every_problem(city: None) -> None:
    rows = _rows("complaints_messy_sample.csv")
    report = _ingest("complaints", "complaints_messy_sample.csv")
    assert report.total == len(rows)
    assert report.rejected == 15 and report.duplicates == 6 and report.ok == len(rows) - 15 - 6
    assert report.status == "partial" and report.warning_count >= 3
    reasons = " | ".join(" ".join(r["errors"]) for r in report.rejected_rows)
    for expected in ("description is required", "submitted_at", "out of range", "unknown and no coordinates"):
        assert expected in reasons
    assert all(isinstance(r["row"], int) and r["row"] >= 2 for r in report.rejected_rows)
    with SessionLocal() as db:
        cats = set(db.scalars(select(Complaint.category).distinct()))
        assert None in cats  # the "something odd" rows are loaded uncategorised
        assert cats - {None} <= set(pipeline.N.CATEGORIES)  # every messy variant normalised to a canonical value
        log = db.scalars(select(IngestionLog)).one()
        assert log.status == "partial" and log.errors["rejected"] == 15 and len(log.errors["rejected_rows"]) == 15


def test_feedback_links_to_complaints_and_dedups(city: None) -> None:
    with SessionLocal() as db:  # link feedback to complaints that exist
        pipeline.ingest_complaints(db, _rows("complaints_clean_sample.csv"))
    fb = _rows("feedback_sample.csv")
    with SessionLocal() as db:
        first = pipeline.ingest_feedback(db, fb)
        second = pipeline.ingest_feedback(db, fb)
        assert first.ok == len(fb) - first.duplicates and first.rejected == 0
        assert second.ok == 0 and second.duplicates == len(fb)
        assert db.scalar(select(func.count()).select_from(Feedback)) == first.ok
        assert db.scalar(select(func.count()).where(Feedback.ward_id.isnot(None))) > 0


def test_feedback_rejects_bad_rating(city: None) -> None:
    with SessionLocal() as db:
        rep = pipeline.ingest_feedback(db, [{"rating": "7", "submitted_at": "2025-01-01"}, {"rating": "4", "submitted_at": "2025-01-01", "text": "ok"}])
    assert (rep.ok, rep.rejected) == (1, 1) and "between 1 and 5" in rep.rejected_rows[0]["errors"][0]


def test_performance_load_and_duplicates(city: None) -> None:
    rows = _rows("service_performance_sample.csv")
    with SessionLocal() as db:
        first = pipeline.ingest_performance(db, rows)
        assert first.ok == len(rows) and first.rejected == 0
        assert db.scalar(select(func.count()).where(ServicePerformance.ward_id.is_(None))) > 0  # city-wide rows
        second = pipeline.ingest_performance(db, rows)
        assert (second.ok, second.duplicates) == (0, len(rows))
        bad = pipeline.ingest_performance(db, [{"department": "Nope", "period": "2025-01", "complaints_received": "3"}])
        assert bad.rejected == 1 and "unknown department" in bad.rejected_rows[0]["errors"][0]


def test_generated_csv_columns_are_all_ingestable(city: None) -> None:
    """Extra evaluation-only columns (true_category etc.) must be ignored, not break loading."""
    with (SAMPLES / "complaints_messy_sample.csv").open(encoding="utf-8") as f:
        assert {"true_category", "true_duplicate_of", "burst"} <= set(next(csv.reader(f)))


# ------------------------------------------------------------------ documents
@pytest.mark.parametrize("name", sorted(p.name for p in POLICIES.glob("*") if p.suffix in (".pdf", ".docx")))
def test_extract_and_chunk_every_sample_document(name: str) -> None:
    pages = extract_pages((POLICIES / name).read_bytes(), name)
    chunks = chunk_pages(pages)
    assert chunks and all(0 < len(c.content) <= 1100 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert "Sahyadripur" in " ".join(t for _, t in pages) or "Municipal" in " ".join(t for _, t in pages)


def test_chunker_respects_size_and_overlap() -> None:
    text = " ".join(f"Sentence number {i} explains a rule about drainage response." for i in range(120))
    chunks = chunk_pages([(1, text)], size=400, overlap=80)
    assert len(chunks) > 5 and all(len(c.content) <= 480 for c in chunks)
    tail = chunks[0].content.split()[-3:]
    assert " ".join(tail) in chunks[1].content  # overlap carries context forward


def test_document_ingest_dedups_by_content_and_rejects_garbage(city: None) -> None:
    data = (POLICIES / "solid_waste_management_policy.pdf").read_bytes()
    with SessionLocal() as db:
        doc, created = ingest_document(db, data, "swm.pdf", title="SWM Policy")
        doc2, created2 = ingest_document(db, data, "copy.pdf", title="Copy")
        assert created and not created2 and doc.id == doc2.id
        assert db.scalar(select(func.count()).select_from(DocumentChunk)) == doc.num_chunks == db.get(Document, doc.id).num_chunks  # type: ignore[union-attr]
    with pytest.raises(DocumentError):
        extract_pages(b"not a pdf", "x.pdf")
    with pytest.raises(DocumentError):
        extract_pages(b"   ", "x.txt")
    with pytest.raises(DocumentError):
        extract_pages(b"data", "x.exe")


def test_sample_files_exist_and_wards_geojson_is_valid() -> None:
    import json

    fc = json.loads((DATA_DIR / "wards.geojson").read_text(encoding="utf-8"))
    assert len(fc["features"]) == 20 and all(f["geometry"]["type"] == "Polygon" for f in fc["features"])
    assert {p.name for p in POLICIES.glob("*")} >= {"solid_waste_management_policy.pdf", "annual_municipal_performance_report_2024_25.docx"}
    assert isinstance(Path(DATA_DIR / "departments.csv").read_text(), str)
