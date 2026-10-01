"""Row-level validation for complaints, feedback and service performance records.

Each validator returns a RowResult: `data` (normalised values) or None when the row is rejected, plus messages.
Rejections explain exactly what is wrong; warnings mean the row was loaded but something was dropped or guessed.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.ingestion import normalize as N
from app.nlp.language import detect_language

LANGS = {"en", "hi", "mr", "hinglish"}
MIN_DATE = datetime(2000, 1, 1, tzinfo=N.IST)


@dataclass
class RowResult:
    data: dict[str, Any] | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and v != v)


def _get(raw: dict[str, Any], *names: str) -> Any:
    for n in names:
        if not _blank(raw.get(n)):
            return raw[n]
    return None


def _float(v: Any, name: str, errors: list[str]) -> float | None:
    if _blank(v):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        errors.append(f"{name} '{v}' is not a number")
        return None


def _date(v: Any, name: str, errors: list[str], required: bool = False) -> datetime | None:
    if _blank(v):
        if required:
            errors.append(f"{name} is required")
        return None
    try:
        d = N.parse_datetime(v)
    except ValueError as exc:
        errors.append(f"{name}: {exc}")
        return None
    if d < MIN_DATE or d > datetime.now(timezone.utc) + timedelta(days=1):
        errors.append(f"{name} {d.date()} is outside the allowed range")
        return None
    return d


def validate_complaint(raw: dict[str, Any]) -> RowResult:
    errors: list[str] = []
    warnings: list[str] = []

    description = N.clean_text(_get(raw, "description", "text"))
    if len(description) < 5:
        errors.append("description is required (at least 5 characters)")
    elif len(description) > 5000:
        errors.append("description is longer than 5000 characters")

    submitted = _date(_get(raw, "submitted_at"), "submitted_at", errors, required=True)
    resolved = _date(_get(raw, "resolved_at"), "resolved_at", errors)
    if submitted and resolved and resolved < submitted:
        errors.append("resolved_at is earlier than submitted_at")

    lat, lon = _float(_get(raw, "latitude"), "latitude", errors), _float(_get(raw, "longitude"), "longitude", errors)
    if (lat is None) != (lon is None):
        errors.append("latitude and longitude must be given together")
    elif lat is not None and lon is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        errors.append(f"coordinates ({lat}, {lon}) are out of range")

    hours = _float(_get(raw, "resolution_hours"), "resolution_hours", errors)
    if hours is not None and hours < 0:
        errors.append("resolution_hours cannot be negative")
    if hours is None and submitted and resolved and not errors:
        hours = round((resolved - submitted).total_seconds() / 3600, 1)

    status_raw = _get(raw, "status")
    status = N.normalize_status(status_raw)
    if status is None:
        errors.append(f"unknown status '{status_raw}'")

    cat_raw = _get(raw, "category")
    category = N.normalize_category(cat_raw)
    if cat_raw is not None and category is None:
        warnings.append(f"unknown category '{cat_raw}', loaded uncategorised")

    lang_raw = N.key(_get(raw, "language"))
    language = lang_raw if lang_raw in LANGS else (detect_language(description) if description else None)

    phone_raw = _get(raw, "citizen_phone")
    phone = N.clean_phone(phone_raw)
    if phone_raw is not None and phone is None:
        warnings.append("citizen_phone is not a valid mobile number and was dropped")

    if errors:
        return RowResult(None, errors, warnings)
    return RowResult(
        {
            "external_id": N.clean_text(_get(raw, "external_id")) or None,
            "description": description,
            "submitted_at": submitted,
            "ward_text": N.clean_text(_get(raw, "ward")) or None,
            "department_text": N.clean_text(_get(raw, "department")) or None,
            "category": category,
            "status": status,
            "language": language,
            "channel": (N.clean_text(_get(raw, "channel")) or None),
            "latitude": lat,
            "longitude": lon,
            "address": (N.clean_text(_get(raw, "address")) or None),
            "resolved_at": resolved,
            "resolution_hours": hours,
            "citizen_name": N.clean_text(_get(raw, "citizen_name")) or None,
            "citizen_phone": phone,
        },
        [],
        warnings,
    )


def validate_feedback(raw: dict[str, Any]) -> RowResult:
    errors: list[str] = []
    warnings: list[str] = []
    rating_raw = _get(raw, "rating")
    rating: int | None = None
    try:
        rating = int(float(str(rating_raw)))
        if not 1 <= rating <= 5:
            errors.append("rating must be between 1 and 5")
    except (TypeError, ValueError):
        errors.append("rating is required and must be a number from 1 to 5")
    submitted = _date(_get(raw, "submitted_at"), "submitted_at", errors, required=True)
    text = N.clean_text(_get(raw, "text", "feedback_text")) or None
    if text and len(text) > 5000:
        errors.append("text is longer than 5000 characters")
    lang_raw = N.key(_get(raw, "language"))
    language = lang_raw if lang_raw in LANGS else (detect_language(text) if text else None)
    if errors:
        return RowResult(None, errors, warnings)
    return RowResult(
        {
            "complaint_external_id": N.clean_text(_get(raw, "complaint_external_id")) or None,
            "ward_text": N.clean_text(_get(raw, "ward")) or None,
            "department_text": N.clean_text(_get(raw, "department")) or None,
            "rating": rating, "text": text, "language": language,
            "source": N.clean_text(_get(raw, "channel", "source")) or None,
            "submitted_at": submitted,
        },
        [],
        warnings,
    )


def validate_performance(raw: dict[str, Any]) -> RowResult:
    errors: list[str] = []
    dept = N.clean_text(_get(raw, "department"))
    if not dept:
        errors.append("department is required")
    period = None
    try:
        period = N.parse_month(_get(raw, "period"))
    except (ValueError, TypeError):
        errors.append(f"period '{_get(raw, 'period')}' is not a valid month")
    ints: dict[str, int | None] = {}
    for name in ("complaints_received", "complaints_resolved", "sla_breaches", "staff_count"):
        v = _float(_get(raw, name), name, errors)
        if v is not None and v < 0:
            errors.append(f"{name} cannot be negative")
        ints[name] = int(v) if v is not None else None
    if ints["complaints_received"] is None:
        errors.append("complaints_received is required")
    avg = _float(_get(raw, "avg_resolution_hours"), "avg_resolution_hours", errors)
    budget = _float(_get(raw, "budget_spent"), "budget_spent", errors)
    if errors:
        return RowResult(None, errors)
    return RowResult(
        {
            "department_text": dept, "ward_text": N.clean_text(_get(raw, "ward")) or None, "period": period,
            "complaints_received": ints["complaints_received"] or 0, "complaints_resolved": ints["complaints_resolved"] or 0,
            "sla_breaches": ints["sla_breaches"] or 0, "avg_resolution_hours": avg,
            "staff_count": ints["staff_count"], "budget_spent": budget,
        }
    )
