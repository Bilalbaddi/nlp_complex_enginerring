"""Transparent urgency/priority scoring (0-100) from rule-based features, optionally blended with an ML-predicted
SLA-breach risk. Every point of the score is explained in `reasons`, so officers can audit it.

Weights are constants here on purpose: there is no ground truth for "true urgency", so the scoring is a documented
policy (health/safety first), not a learned function. The breach-risk model (ml/breach.py) is the learned part.
"""

import re
from dataclasses import dataclass, field

# category base severity: the potential harm of the issue type
CATEGORY_SEVERITY = {
    "electricity": 25, "public_health": 25, "drainage": 18, "water_supply": 18, "roads": 15,
    "garbage": 12, "streetlights": 12, "encroachment": 8, "noise": 6, "parks": 5,
}

_CRITICAL = [
    "accident", "injur", "sparking", "spark", "live wire", "wire fallen", "fallen on the road", "electric wire",
    "open manhole", "caved in", "collapse", "dengue", "vomiting", "diarrhoea", "outbreak", "contaminated", "bite",
    "attacking", "attack", "fire", "chain snatching", "ambulance", "drowning",
    "हादसे", "चिंगारी", "तार", "मैनहोल", "डेंगू", "उल्टी", "दस्त", "हमला", "आग", "एम्बुलेंस", "घायल",
    "अपघात", "ठिणग्या", "मॅनहोल", "डेंग्यू", "उलट्या", "जुलाब", "हल्ला", "रुग्णवाहिका", "जखमी",
    "chingari", "taar", "manhole", "ulti", "dast", "hamla", "aag", "ghayal",
]
_VULNERABLE = [
    "children", "child", "senior citizen", "elderly", "school", "hospital", "pregnant",
    "बच्चों", "बच्चे", "बुजुर्ग", "अस्पताल", "लहान मुले", "मुले", "वृद्ध", "रुग्णालय",
    "bachchon", "bachche", "buzurgon",
]
_ESCALATION = [
    "complained earlier", "nobody responded", "no response", "reminder", "again and again", "still not",
    "पहले भी शिकायत", "कोई जवाब नहीं", "यापूर्वीही तक्रार", "प्रतिसाद मिळाला नाही", "pehle bhi complaint", "koi response nahi",
]
_URGENT_WORDS = ["urgent", "immediate", "at the earliest", "तुरंत", "जल्द से जल्द", "तात्काळ", "लवकरात लवकर", "turant", "jaldi se jaldi"]
_DAYS = re.compile(r"(\d+)\s*(?:days|day|dino|दिनों|दिन|दिवस)|(?:पिछले|गेल्या|pichhle)\s*(\d+)", re.IGNORECASE)
_WEEK = re.compile(r"\b(?:week|हफ्ते|हफ़्ते|आठवडा|आठवडाभर|hafte)\b", re.IGNORECASE)


@dataclass
class UrgencyResult:
    score: float
    level: str
    reasons: list[str] = field(default_factory=list)


def level_for(score: float) -> str:
    return "critical" if score >= 70 else ("high" if score >= 50 else ("medium" if score >= 30 else "low"))


def _hits(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]


def days_mentioned(text: str) -> int:
    best = 0
    for m in _DAYS.finditer(text):
        best = max(best, int(m.group(1) or m.group(2)))
    return max(best, 7 if _WEEK.search(text) else 0)


def urgency_score(
    text: str,
    category: str | None,
    sentiment: float = 0.0,
    *,
    cluster_count: int = 0,
    breach_risk: float | None = None,
) -> UrgencyResult:
    """Score one complaint.

    cluster_count: complaints of the same category in the same ward in the past 7 days (an emerging-issue signal).
    breach_risk: probability (0-1) that the complaint will miss its SLA, from the ML model; adds up to 15 points.
    """
    t = (text or "").casefold()
    score = float(CATEGORY_SEVERITY.get(category or "", 10))
    reasons = [f"category '{category or 'unknown'}' base severity {score:.0f}"]

    crit = _hits(t, _CRITICAL)
    if crit:
        pts = min(40, 25 + 5 * (len(crit) - 1))
        score += pts
        reasons.append(f"health/safety terms ({', '.join(crit[:3])}) +{pts}")
    vul = _hits(t, _VULNERABLE)
    if vul:
        score += 12
        reasons.append(f"vulnerable groups or sensitive places ({vul[0]}) +12")
    days = days_mentioned(t)
    if days >= 3:
        pts = min(12, 4 + days)
        score += pts
        reasons.append(f"problem persisting ~{days} days +{pts}")
    if _hits(t, _ESCALATION):
        score += 8
        reasons.append("earlier complaint ignored +8")
    if _hits(t, _URGENT_WORDS):
        score += 6
        reasons.append("citizen asks for urgent action +6")
    if sentiment < -0.3:
        pts = round(min(8.0, -sentiment * 10), 1)
        score += pts
        reasons.append(f"distressed tone +{pts}")
    if cluster_count >= 5:
        pts = min(10, cluster_count)
        score += pts
        reasons.append(f"{cluster_count} similar complaints in this ward this week +{pts}")
    score = min(score, 85.0)
    if breach_risk is not None:
        pts = round(15 * max(0.0, min(1.0, breach_risk)), 1)
        score += pts
        reasons.append(f"SLA breach risk {breach_risk:.0%} +{pts}")
    score = round(min(score, 100.0), 1)
    return UrgencyResult(score, level_for(score), reasons)
