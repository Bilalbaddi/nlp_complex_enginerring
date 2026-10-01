"""Text normalisation shared by every NLP component (keeps Devanagari; strips contact details and URLs)."""

import re
import unicodedata

from app.core.pii import strip_pii

_URL = re.compile(r"https?://\S+|www\.\S+")
TOKEN = re.compile(r"[\wऀ-ॿ]+")


def normalize_text(text: str) -> str:
    """NFC, casefold, drop URLs and phone numbers/e-mails, collapse whitespace. Never raises on odd input."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", str(text))
    t = _URL.sub(" ", t)
    t = strip_pii(t)
    return " ".join(t.casefold().split())


def tokens(text: str) -> list[str]:
    return TOKEN.findall(normalize_text(text))
