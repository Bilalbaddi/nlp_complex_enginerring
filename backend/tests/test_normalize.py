from datetime import date, datetime

import pytest

from app.ingestion import normalize as N
from app.ingestion.readers import FileFormatError, canonical_header, read_rows
from app.ingestion.validators import validate_complaint, validate_feedback, validate_performance
from app.nlp.language import detect_language


def test_parse_datetime_formats_assume_ist() -> None:
    for text in ["2025-03-12 09:15", "12/03/2025 09:15", "12-03-2025", "12 Mar 2025", "March 12, 2025", "2025-03-12T09:15:00+05:30"]:
        d = N.parse_datetime(text)
        assert (d.year, d.month, d.day) == (2025, 3, 12) and d.tzinfo is not None
    assert N.parse_datetime("2025-03-12 09:15").utcoffset().total_seconds() == 19800  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        N.parse_datetime("not a date")
    assert N.parse_datetime(datetime(2025, 1, 1)).tzinfo is not None


def test_parse_month() -> None:
    assert N.parse_month("2024-05") == date(2024, 5, 1)
    assert N.parse_month("2024-05-17") == date(2024, 5, 1)
    assert N.parse_month("May 2024") == date(2024, 5, 1)
    assert N.parse_month(datetime(2024, 5, 17)) == date(2024, 5, 1)


@pytest.mark.parametrize(
    "raw,expected",
    [("Water Supply", "water_supply"), ("WATER", "water_supply"), ("Roads/Potholes", "roads"), ("pothole", "roads"),
     ("street lights", "streetlights"), ("Public Health", "public_health"), ("noise pollution", "noise"),
     ("water supply issue", "water_supply"), ("something odd", None), ("", None)],
)
def test_normalize_category(raw: str, expected: str | None) -> None:
    assert N.normalize_category(raw) == expected


def test_status_and_phone() -> None:
    assert N.normalize_status("In Progress") == "in_progress" and N.normalize_status("CLOSED") == "closed"
    assert N.normalize_status("") == "open" and N.normalize_status("weird") is None
    assert N.clean_phone("+91 98765-43210") == "9876543210" and N.clean_phone("12345") is None


def test_ward_and_department_resolvers() -> None:
    wards = N.build_ward_resolver([(1, "W01", "Shivaji Nagar"), (7, "W07", "Sahyadri Heights")])
    for text in ["Shivaji Nagar", "  shivaji   NAGAR ", "W01", "Ward 1", "ward-01", "1"]:
        assert wards.resolve(text) == 1, text
    assert wards.resolve("Ward 7") == 7 and wards.resolve("Ward 99") is None and wards.resolve("") is None
    depts = N.build_department_resolver([(2, "DSD", "Drainage & Sewerage Department", "drainage")])
    for text in ["DSD", "Drainage & Sewerage Department", "drainage sewerage", "drainage"]:
        assert depts.resolve(text) == 2, text


def test_detect_language() -> None:
    assert detect_language("Street light not working near the temple") == "en"
    assert detect_language("नल से गंदा और बदबूदार पानी आ रहा है, कृपया तुरंत कार्रवाई करें") == "hi"
    assert detect_language("नळातून गढूळ पाणी येत आहे, कृपया तात्काळ कार्यवाही करावी") == "mr"
    assert detect_language("Nal me paani nahi aa raha hai kal se") == "hinglish"


def test_validate_complaint_accepts_and_normalises() -> None:
    r = validate_complaint({"description": "Big pothole near the school", "submitted_at": "12/03/2025 10:00", "ward": "Ward 7",
                            "category": "Roads/Potholes", "status": "pending", "latitude": "20.001", "longitude": "73.79",
                            "citizen_phone": "98765 43210"})
    assert r.data and not r.errors
    assert r.data["category"] == "roads" and r.data["status"] == "in_progress" and r.data["citizen_phone"] == "9876543210"
    assert r.data["language"] == "en" and r.data["latitude"] == 20.001


@pytest.mark.parametrize(
    "raw,fragment",
    [({"description": "", "submitted_at": "2025-01-01"}, "description is required"),
     ({"description": "Water leak on the road", "submitted_at": "garbage"}, "submitted_at"),
     ({"description": "Water leak on the road"}, "submitted_at is required"),
     ({"description": "Water leak on the road", "submitted_at": "2025-01-01", "latitude": "95", "longitude": "10"}, "out of range"),
     ({"description": "Water leak on the road", "submitted_at": "2025-01-01", "latitude": "10"}, "together"),
     ({"description": "Water leak on the road", "submitted_at": "2025-01-05", "resolved_at": "2025-01-01"}, "earlier"),
     ({"description": "Water leak on the road", "submitted_at": "2999-01-01"}, "outside the allowed range"),
     ({"description": "Water leak on the road", "submitted_at": "2025-01-01", "status": "zzz"}, "unknown status")],
)
def test_validate_complaint_rejects(raw: dict[str, str], fragment: str) -> None:
    r = validate_complaint(raw)
    assert r.data is None and any(fragment in e for e in r.errors), r.errors


def test_validate_complaint_warnings_not_errors() -> None:
    r = validate_complaint({"description": "Water leak on the road", "submitted_at": "2025-01-01", "category": "odd", "citizen_phone": "123"})
    assert r.data and r.data["category"] is None and r.data["citizen_phone"] is None and len(r.warnings) == 2


def test_validate_feedback_and_performance() -> None:
    assert validate_feedback({"rating": "5", "submitted_at": "2025-02-01", "text": "Good service"}).data
    assert validate_feedback({"rating": "9", "submitted_at": "2025-02-01"}).data is None
    assert validate_feedback({"submitted_at": "2025-02-01"}).data is None
    p = validate_performance({"department": "WSD", "period": "2025-02", "complaints_received": "10", "sla_breaches": "2"})
    assert p.data and p.data["period"] == date(2025, 2, 1) and p.data["complaints_resolved"] == 0
    assert validate_performance({"department": "WSD", "period": "x", "complaints_received": "1"}).data is None
    assert validate_performance({"period": "2025-02", "complaints_received": "1"}).data is None


def test_readers_csv_json_xlsx() -> None:
    csv_bytes = "Complaint ID;Text;Lat\nA1;Pothole here;20.1\n;;\nA2;Garbage;20.2\n".encode()
    rows = read_rows(csv_bytes, "x.csv")
    assert rows == [{"external_id": "A1", "text": "Pothole here", "latitude": "20.1"}, {"external_id": "A2", "text": "Garbage", "latitude": "20.2"}]
    assert canonical_header("Complaint Date") == "submitted_at" and canonical_header("Lng") == "longitude"
    assert read_rows(b'{"data": [{"Complaint ID": "A1", "Text": "x"}]}', "x.json")[0]["external_id"] == "A1"
    assert len(read_rows(b'{"a": 1}\n{"a": 2}\n', "x.jsonl")) == 2
    with pytest.raises(FileFormatError):
        read_rows(b"{oops", "x.json")
    with pytest.raises(FileFormatError):
        read_rows(b"x", "x.exe")
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Description", "Date"])
    ws.append(["Water leak", datetime(2025, 3, 1, 8, 0)])
    buf = io.BytesIO()
    wb.save(buf)
    xl = read_rows(buf.getvalue(), "x.xlsx")
    assert xl[0]["description"] == "Water leak" and validate_complaint({**xl[0], "ward": "W01"}).data
    with pytest.raises(FileFormatError):
        read_rows(b"not really excel", "x.xlsx")
