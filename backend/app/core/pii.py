"""PII helpers: masking for analytics/API views, and stripping from free text before it leaves the system (LLM calls)."""

import re
from collections.abc import Iterable

# Indian mobile numbers with optional +91 / 91 / 0 prefix and common separators (spaces, dashes, dots).
_PHONE = re.compile(r"(?<![\w])(?:\+?91[\s.-]?|0)?[6-9](?:[\s.-]?\d){9}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def mask_name(name: str | None) -> str | None:
    """'Rajesh Kulkarni' -> 'R***** K*******' (first letter of each word kept)."""
    if not name:
        return name
    return " ".join(w[0] + "*" * (len(w) - 1) if len(w) > 1 else w for w in name.split())


def mask_phone(phone: str | None) -> str | None:
    """'9876543210' -> '******3210' (last four digits kept)."""
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 6:
        return "*" * len(phone)
    return "*" * (len(digits) - 4) + digits[-4:]


def strip_pii(text: str, known_names: Iterable[str] = ()) -> str:
    """Replace phone numbers, e-mails, Aadhaar/PAN-like ids (and any `known_names`) with placeholders."""
    out = _EMAIL.sub("[EMAIL]", text)
    out = _AADHAAR.sub("[ID]", out)  # before phones: a 12-digit id must not be read as a phone number
    out = _PAN.sub("[ID]", out)
    out = _PHONE.sub("[PHONE]", out)
    for name in known_names:
        if name and len(name) > 2:
            out = re.sub(re.escape(name), "[NAME]", out, flags=re.IGNORECASE)
    return out
