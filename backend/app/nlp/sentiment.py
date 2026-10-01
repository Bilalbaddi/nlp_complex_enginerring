"""Sentiment in [-1, 1] for English, Hindi, Marathi and Hinglish without any model download.

English uses VADER extended with municipal-domain terms; the other languages use a compact valence lexicon with
negation and mixed-sentiment handling. Complaints are negative by nature, so the score mostly
measures distress intensity; it is evaluated on citizen feedback, where the star rating gives an independent label.
"""

import math
import re
from functools import lru_cache

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from app.nlp.language import detect_language
from app.nlp.preprocess import tokens

_DOMAIN_EN = {
    "pothole": -2.0, "overflowing": -2.0, "overflow": -1.8, "stink": -2.2, "stinking": -2.2, "foul": -2.2, "dirty": -2.0,
    "leaking": -1.6, "blocked": -1.6, "choked": -1.8, "dangerous": -2.4, "unsafe": -2.2, "hazard": -2.4, "dark": -1.2,
    "accident": -2.6, "flooding": -2.2, "waterlogging": -2.2, "sparking": -2.6, "unhygienic": -2.4, "ignored": -2.0,
    "delayed": -1.8, "unresolved": -2.2, "reminders": -1.0, "promised": -0.6, "partially": -1.0, "slow": -2.0,
    "longer": -1.2, "prompt": 2.0, "promptly": 2.0, "quickly": 1.8, "courteous": 2.2, "resolved": 1.6, "fixed": 1.5,
    "satisfied": 2.2, "dengue": -2.4, "vomiting": -2.2, "diarrhoea": -2.2, "contaminated": -2.4, "mosquito": -1.6,
    "illegal": -1.4, "encroachment": -1.4, "problem": -0.2, "issue": -0.2,
}

_POS = {
    "thank", "thanks", "धन्यवाद", "dhanyavaad", "shukriya", "जल्दी", "jaldi", "turant", "तुरंत", "त्वरित", "तत्पर", "लवकर",
    "संतुष्ट", "समाधानी", "खुश", "khush", "accha", "अच्छा", "अच्छी", "सुलझ", "सुटली", "सुलझी", "ठीक", "theek", "good", "great",
    "quickly", "prompt", "solve", "solved",
}
_NEG = {
    "धीमा", "धीमी", "संथ", "खराब", "kharab", "गंदा", "ganda", "गंदी", "बदबू", "badbu", "बदबूदार", "दुर्गंधी", "दुर्गंधीयुक्त", "गढूळ",
    "टूटा", "टूटी", "टूटे", "toota", "toote", "toot", "तुटले", "तुटलेले", "खतरा", "खतरनाक", "khatra", "khatarnak", "धोकादायक",
    "बीमार", "bimar", "आजारी", "हादसे", "अपघात", "उल्टी", "उलट्या", "दस्त", "जुलाब", "ulti", "dast", "डेंगू", "डेंग्यू", "dengue",
    "हमला", "hamla", "अंधेरा", "अंधार", "andhera", "slow", "धीरे", "कचरा", "kachra", "ढेर", "dher", "गड्ढा", "गड्ढे", "gadda",
    "gadde", "खड्डा", "खड्डे", "परेशानी", "pareshani", "त्रास", "त्रासदायक", "शोर", "shor", "चिंगारी", "chingari", "ठिणग्या",
    "मच्छर", "machhar", "डास", "अवैध", "अनधिकृत", "illegal", "ज्यादा", "zyada", "जास्त", "longer",
}
_NEGATORS = {"नहीं", "नही", "नाही", "nahi", "nahin", "not", "no", "never", "न"}

@lru_cache
def _vader() -> SentimentIntensityAnalyzer:
    a = SentimentIntensityAnalyzer()
    a.lexicon.update(_DOMAIN_EN)
    return a


def _lexicon_score(toks: list[str]) -> float:
    """Sum of word valences; negators flip positives; text with both positive and negative cues is damped."""
    score = 0.0
    has_pos = has_neg = False
    for i, t in enumerate(toks):
        val = 1.0 if t in _POS else (-1.0 if t in _NEG else 0.0)
        if not val:
            continue
        window = toks[max(0, i - 2) : i + 3]  # Hindi/Marathi put the negator after the word: "समाधान नहीं हुआ"
        if val > 0 and any(w in _NEGATORS for w in window):
            val = -1.0
        has_pos, has_neg = has_pos or val > 0, has_neg or val < 0
        score += val
    if any(t in _NEGATORS for t in toks) and not has_pos:
        score -= 1.0  # an unmatched "not / नहीं / nahi" still signals that something did not happen
    if has_pos and has_neg:
        score *= 0.3  # mixed ("fixed, but it took longer than promised") is close to neutral
    return score


def sentiment(text: str, language: str | None = None) -> float:
    """Return a compound score in [-1, 1]; 0 when the text carries no sentiment signal."""
    if not text or not text.strip():
        return 0.0
    lang = language or detect_language(text)
    if lang == "en":
        v = float(_vader().polarity_scores(text)["compound"])
        lex = _vader().lexicon
        vals = [lex.get(t, 0.0) for t in tokens(text)]
        if any(x >= 1.0 for x in vals) and any(x <= -1.0 for x in vals):
            v *= 0.3  # clear positive AND clear negative words: mixed sentiment is close to neutral
        if -0.25 < v < 0.3 and re.search(r"\b(?:not|never|did not|didn't|no response|nobody)\b", text.casefold()):
            v -= 0.35  # VADER only flips the word right after "not"; "did not visit" would stay neutral
        return max(-1.0, v)
    raw = _lexicon_score(tokens(text))
    if lang == "hinglish":  # also pick up English words that appear in romanised text
        raw += 2.0 * float(_vader().polarity_scores(text)["compound"])
    return raw / math.sqrt(raw * raw + 4.0)


def label(score: float) -> str:
    return "negative" if score <= -0.25 else ("positive" if score >= 0.25 else "neutral")
