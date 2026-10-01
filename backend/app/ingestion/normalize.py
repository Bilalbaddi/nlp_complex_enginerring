"""Normalisation of dates, ward names, departments, categories, statuses, phones and text."""

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))

CATEGORIES = [
    "water_supply", "drainage", "roads", "garbage", "streetlights",
    "encroachment", "public_health", "electricity", "parks", "noise",
]

_CATEGORY_ALIASES: dict[str, str] = {
    "water supply": "water_supply", "water": "water_supply", "tap": "water_supply", "paani": "water_supply", "drinking water": "water_supply",
    "drainage": "drainage", "drain": "drainage", "sewage": "drainage", "sewer": "drainage", "gutter": "drainage", "sewerage": "drainage", "waterlogging": "drainage",
    "roads": "roads", "road": "roads", "roads potholes": "roads", "potholes": "roads", "pothole": "roads", "road repair": "roads", "footpath": "roads",
    "garbage": "garbage", "waste": "garbage", "trash": "garbage", "solid waste": "garbage", "sanitation": "garbage", "garbage collection": "garbage",
    "streetlights": "streetlights", "streetlight": "streetlights", "street light": "streetlights", "street lights": "streetlights", "lighting": "streetlights",
    "encroachment": "encroachment", "illegal construction": "encroachment", "hawkers": "encroachment", "illegal parking": "encroachment",
    "public health": "public_health", "health": "public_health", "mosquito": "public_health", "stray dogs": "public_health", "vector control": "public_health",
    "electricity": "electricity", "power": "electricity", "electric": "electricity", "power cut": "electricity",
    "parks": "parks", "park": "parks", "garden": "parks", "gardens": "parks", "parks gardens": "parks",
    "noise": "noise", "noise pollution": "noise", "loudspeaker": "noise",
}
_STATUS_ALIASES = {
    "open": "open", "new": "open", "registered": "open", "submitted": "open",
    "assigned": "assigned", "allocated": "assigned",
    "in progress": "in_progress", "in_progress": "in_progress", "inprogress": "in_progress", "pending": "in_progress", "ongoing": "in_progress", "wip": "in_progress",
    "resolved": "resolved", "fixed": "resolved", "done": "resolved", "completed": "resolved",
    "closed": "closed", "close": "closed",
    "rejected": "rejected", "invalid": "rejected", "declined": "rejected",
}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y %H:%M", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
]


def clean_text(value: Any) -> str:
    """NFC-normalise, drop control characters, collapse whitespace."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())


def key(value: Any) -> str:
    """Comparison key: lower-case, punctuation to spaces, collapsed (Devanagari letters are kept)."""
    text = clean_text(value).casefold()
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(text.split())


def parse_datetime(value: Any) -> datetime:
    """Parse many date formats; naive values are taken as IST. Raises ValueError when unparseable."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = clean_text(value)
        if not text:
            raise ValueError("empty date")
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            dt = None  # type: ignore[assignment]
            for fmt in _DATE_FORMATS:
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                raise ValueError(f"unrecognised date '{text}'") from None
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt


def parse_month(value: Any) -> date:
    """First day of the month for '2024-05', '2024-05-17', 'May 2024', or a date."""
    if isinstance(value, (datetime, date)):
        return date(value.year, value.month, 1)
    text = clean_text(value)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    m = re.fullmatch(r"([A-Za-z]{3})[A-Za-z]*[ -](\d{4})", text)
    if m and m.group(1).lower() in _MONTHS:
        return date(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
    d = parse_datetime(text)
    return date(d.year, d.month, 1)


def normalize_category(value: Any) -> str | None:
    text = key(value)
    if not text:
        return None
    text = text.replace(" ", "_") if text.replace(" ", "_") in CATEGORIES else text
    if text in CATEGORIES:
        return text
    if text in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[text]
    for alias, cat in _CATEGORY_ALIASES.items():  # e.g. "water supply issue" contains "water supply"
        if len(alias) > 4 and alias in text:
            return cat
    return None


def normalize_status(value: Any) -> str | None:
    text = key(value)
    return _STATUS_ALIASES.get(text) if text else "open"


def clean_phone(value: Any) -> str | None:
    """Return a 10-digit Indian mobile number, or None when it does not look like one."""
    digits = re.sub(r"\D", "", clean_text(value))
    if len(digits) > 10 and digits.startswith(("91", "0")):
        digits = digits[-10:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


class Resolver:
    """Maps free-text names to ids using normalised aliases."""

    def __init__(self) -> None:
        self._map: dict[str, int] = {}

    def add(self, alias: Any, ident: int) -> None:
        k = key(alias)
        if k:
            self._map.setdefault(k, ident)

    def resolve(self, value: Any) -> int | None:
        return self._map.get(key(value))


def build_ward_resolver(wards: list[tuple[int, str, str]]) -> Resolver:
    """wards: (id, code, name). Accepts 'Shivaji Nagar', 'W07', 'Ward 7', 'ward-07', '7'."""
    r = Resolver()
    for wid, code, name in wards:
        n = int(re.sub(r"\D", "", code) or 0)
        for alias in (name, code, f"ward {n}", f"ward {n:02d}", f"w{n}", str(n), f"{n:02d}"):
            r.add(alias, wid)
    return r


def build_department_resolver(depts: list[tuple[int, str, str, str | None]]) -> Resolver:
    """depts: (id, code, name, category). Accepts code, full name, name without 'Department', or the category."""
    r = Resolver()
    for did, code, name, category in depts:
        for alias in (code, name, re.sub(r"\bdepartment\b", "", name, flags=re.I), category or "", (category or "").replace("_", " ")):
            r.add(alias, did)
    return r
