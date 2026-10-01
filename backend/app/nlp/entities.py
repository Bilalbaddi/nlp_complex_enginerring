"""Rule/gazetteer-based extraction of locations, roads and landmarks from complaint text (English, Hindi, Marathi,
Hinglish). Place names in Indian complaint text are usually written in Latin script even inside Devanagari
sentences, so the extractor looks for Title-Case phrases that contain a place-type keyword, plus known ward names.
"""

import re
from collections.abc import Iterable

_ROAD_WORDS = r"(?:Road|Marg|Chowk|Lane|Gali|Street|Path|Highway|Circle)"
_LANDMARK_WORDS = (
    r"(?:Mandir|Temple|School|Hospital|Market|Stand|Tank|Hall|Chowki|Pump|Gate|Office|Anganwadi|Masjid|Gurudwara|"
    r"Station|Park|Garden|Bridge|College|Bazaar|Complex|Church)"
)
_AREA_WORDS = r"(?:Nagar|Colony|Peth|Wadi|Estate|Heights|Valley|Vihar|Society|Layout|Wasti)"
_TYPE = rf"(?:{_ROAD_WORDS}|{_LANDMARK_WORDS}|{_AREA_WORDS})"
# a run of Title-Case / ALL-CAPS words, numbers and "No." that ends in (or contains) a type keyword
_WORD = r"(?:[A-Z][A-Za-z.]*|No\.?|\d+)"
_PHRASE = re.compile(rf"\b({_WORD}(?:\s+{_WORD}){{0,4}})\b")
_ROAD_RE = re.compile(_ROAD_WORDS)
_LANDMARK_RE = re.compile(_LANDMARK_WORDS)
_AREA_RE = re.compile(_AREA_WORDS)
_STOP_FIRST = {"The", "A", "An", "Please", "Kindly", "Request", "This", "Nobody"}
# a single type word on its own ("Road surface washed away") is a sentence start, not a place, unless distinctive
_BARE_OK = {"Gurudwara", "Anganwadi", "Masjid", "Mandir", "Chowki"}


def _trim(phrase: str) -> str:
    """Drop leading sentence-starting words ("Street light ... near Ganesh Mandir" keeps only the place)."""
    words = phrase.split()
    while words and words[0] in _STOP_FIRST and len(words) > 1:
        words.pop(0)
    return " ".join(words)


def extract_entities(text: str, known_places: Iterable[str] = ()) -> dict[str, list[str]]:
    """Return {"roads": [...], "landmarks": [...], "areas": [...]} with duplicates removed, in order of appearance.

    `known_places` (e.g. ward names) are matched case-insensitively and reported as areas.
    """
    roads: list[str] = []
    landmarks: list[str] = []
    areas: list[str] = []

    def add(bucket: list[str], value: str) -> None:
        value = value.strip(" .,")
        if value and value not in bucket:
            bucket.append(value)

    for m in _PHRASE.finditer(text or ""):
        phrase = _trim(m.group(1))
        if not phrase or not re.search(_TYPE, phrase):
            continue
        if " " not in phrase and phrase not in _BARE_OK:
            continue
        if _ROAD_RE.search(phrase):
            add(roads, phrase)
        elif _LANDMARK_RE.search(phrase):
            add(landmarks, phrase)
        elif _AREA_RE.search(phrase):
            add(areas, phrase)
    lowered = (text or "").casefold()
    for place in known_places:
        if place and place.casefold() in lowered and place not in areas:
            add(areas, place)
    return {"roads": roads, "landmarks": landmarks, "areas": areas}
