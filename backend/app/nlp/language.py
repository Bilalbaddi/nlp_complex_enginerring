"""Lightweight, dependency-free language detection for English / Hindi / Marathi / Hinglish complaint text."""

import re

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN_WORD = re.compile(r"[A-Za-z]+")
# Function words that appear in Marathi but not (or rarely) in Hindi, and vice versa.
_MARATHI_MARKERS = {"आहे", "आहेत", "आले", "झाले", "झाला", "झाली", "नाही", "करावी", "करा", "आम्ही", "नळ", "गल्ली", "पासून", "पासूनच", "येत", "होत", "जवळ", "समोर", "वर"}
_HINDI_MARKERS = {"है", "हैं", "में", "से", "को", "का", "की", "के", "नहीं", "रहा", "रही", "रहे", "पर", "कृपया", "पास", "सामने"}
# Common romanised Hindi words (Hinglish)
_HINGLISH = {
    "hai", "hain", "nahi", "nahin", "ki", "ka", "ke", "me", "mein", "par", "se", "ho", "hua", "hui", "kar", "karo",
    "aaj", "kal", "bahut", "paani", "pani", "kachra", "gadda", "gali", "raat", "subah", "band", "raha", "rahi",
    "rahe", "ko", "ne", "aur", "kripya", "turant", "jaldi", "bijli", "naali", "nal", "gaadi", "ghar", "dino",
}


def detect_language(text: str) -> str:
    """Return one of 'en', 'hi', 'mr', 'hinglish'."""
    if not text:
        return "en"
    dev = len(_DEVANAGARI.findall(text))
    letters = dev + sum(len(w) for w in _LATIN_WORD.findall(text))
    if letters and dev / letters > 0.3:
        tokens = set(text.replace("।", " ").replace(".", " ").split())
        mr, hi = len(tokens & _MARATHI_MARKERS), len(tokens & _HINDI_MARKERS)
        return "mr" if mr > hi else "hi"
    words = [w.lower() for w in _LATIN_WORD.findall(text)]
    if len(words) >= 3 and sum(w in _HINGLISH for w in words) >= 2:
        return "hinglish"
    return "en"
