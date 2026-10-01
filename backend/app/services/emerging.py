"""Emerging issue detection.

Spike detection (category x ward): complaints are counted per 7-day bin counted back from the newest complaint
(bin 0 = last 7 days). For each series the RECENT window (bins 0-1, 14 days) is compared with a BASELINE of the 8 bins
before it (2-9):

    z = (recent_weekly_mean - baseline_mean) / max(baseline_std, sqrt(baseline_mean), 1)

A series is flagged when z >= Z_MIN, the ratio recent/baseline >= RATIO_MIN and recent volume >= MIN_RECENT.
Flagged wards of the same category are merged into one issue (severity = max z, weighted by volume). Trend direction
compares the latest week with the one before. This replaces BERTopic: topics are discovered with TF-IDF + NMF on the
last 28 days of complaint text, and a topic's growth is its share of recent (14d) vs previous (14d) complaints.
Summaries are written by the fast Groq model (cached) and fall back to a deterministic template.
"""

import logging
import re
from typing import Any

import numpy as np
import pandas as pd
from sklearn.decomposition import NMF
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.pii import strip_pii
from app.services import cache
from app.services.cube import Cube

log = logging.getLogger(__name__)
Z_MIN, RATIO_MIN, MIN_RECENT = 3.0, 1.8, 8
CACHE_KEY = "analytics:emerging:v1"
_EXTRA_STOP = set("""hain hai rahe raha rahi hoga hota hui hua kar karo kare nahi nahin ko ka ki ke se me mein par aur bhi yeh woh
please kindly request urgent immediately complained complaint responded nobody earlier promised have has been this that
हैं है में रहे रहा रही का की के को से पर और भी यह नहीं आहे आहेत नाही जवळ आणि या ला च मध्ये""".split())
STOP = sorted(set(ENGLISH_STOP_WORDS) | _EXTRA_STOP)

_WEEKS_SQL = text(
    """
    WITH asof AS (SELECT max(submitted_at) AS t FROM complaints)
    SELECT c.category, c.ward_id, floor(extract(epoch FROM (a.t - c.submitted_at)) / 604800)::int AS k, count(*) AS n
    FROM complaints c, asof a
    WHERE c.ward_id IS NOT NULL AND c.category IS NOT NULL AND c.submitted_at > a.t - interval '10 weeks'
    GROUP BY 1, 2, 3
    """
)
_SAMPLES_SQL = text(
    """
    WITH asof AS (SELECT max(submitted_at) AS t FROM complaints)
    SELECT category, ward_id, id, description, submitted_at FROM (
      SELECT c.category, c.ward_id, c.id, c.description, c.submitted_at,
             row_number() OVER (PARTITION BY c.category, c.ward_id ORDER BY c.submitted_at DESC) AS rn
      FROM complaints c, asof a
      WHERE c.submitted_at > a.t - interval '14 days' AND c.category = ANY(CAST(:cats AS text[]))) x
    WHERE rn <= 2
    """
)


def _series(rows: list[list[Any]]) -> dict[tuple[str, int], np.ndarray]:
    out: dict[tuple[str, int], np.ndarray] = {}
    for cat, ward, k, n in rows:
        out.setdefault((cat, ward), np.zeros(10))[int(k)] = n
    return out


def detect_spikes(rows: list[list[Any]]) -> list[dict[str, Any]]:
    flagged: dict[str, list[dict[str, Any]]] = {}
    for (cat, ward), v in _series(rows).items():
        recent, base = v[:2], v[2:10]
        recent_sum = float(recent.sum())
        if recent_sum < MIN_RECENT:
            continue
        base_mean = float(base.mean())
        std = max(float(base.std(ddof=1)), np.sqrt(base_mean), 1.0)
        z = (float(recent.mean()) - base_mean) / std
        ratio = float(recent.mean()) / max(base_mean, 0.5)
        if z >= Z_MIN and ratio >= RATIO_MIN:
            flagged.setdefault(cat, []).append({"ward_id": ward, "z": round(z, 1), "ratio": round(ratio, 1),
                                                "recent": int(recent_sum), "baseline_weekly": round(base_mean, 1),
                                                "k0": int(v[0]), "k1": int(v[1])})
    issues = []
    for cat, ws in flagged.items():
        ws.sort(key=lambda w: -w["z"])
        k0, k1 = sum(w["k0"] for w in ws), sum(w["k1"] for w in ws)
        trend = "rising" if k0 > k1 * 1.15 else ("falling" if k0 < k1 * 0.85 else "steady")
        total = sum(w["recent"] for w in ws)
        issues.append({"category": cat, "severity": round(float(ws[0]["z"]) * (1 + np.log10(total)), 1), "trend": trend,
                       "recent_complaints": total, "wards": ws})
    return sorted(issues, key=lambda i: -i["severity"])


def _template_summary(i: dict[str, Any]) -> str:
    names = ", ".join(w["name"] for w in i["wards"][:3])
    return (f"{i['category'].replace('_', ' ').title()} complaints are {i['trend']} in {names}: {i['recent_complaints']} in the "
            f"last 14 days, up to {i['wards'][0]['ratio']}x the usual weekly level.")


def _llm_summary(i: dict[str, Any]) -> dict[str, Any]:
    from app.llm import service

    facts = {"category": i["category"], "trend": i["trend"], "complaints_last_14_days": i["recent_complaints"],
             "wards": [{"name": w["name"], "recent": w["recent"], "normal_weekly": w["baseline_weekly"], "x_normal": w["ratio"]} for w in i["wards"][:4]],
             "sample_complaints": [strip_pii(s["text"])[:160] for s in i["samples"][:3]]}
    prompt = service.load_prompt("emerging_issue_summary").format(facts=facts)
    r = service.generate("emerging_summary", prompt, kind="fast", max_tokens=400, cache_ttl=6 * 3600)
    return {"text": r.text.strip(), **service.label(r)}


def build(db: Session, cube: Cube, with_llm: bool = True) -> dict[str, Any]:
    rows = [list(r) for r in db.execute(_WEEKS_SQL).all()]
    issues = detect_spikes(rows)[:8]
    if issues:
        samples = db.execute(_SAMPLES_SQL, {"cats": [i["category"] for i in issues]}).all()
        for i in issues:
            wset = {w["ward_id"] for w in i["wards"]}
            i["samples"] = [{"id": s[2], "ward": (cube.ward_by_id.get(s[1]) or {}).get("name"), "text": strip_pii(s[3]),
                             "submitted_at": s[4].isoformat()} for s in samples if s[0] == i["category"] and s[1] in wset][:4]
            for w in i["wards"]:
                w["name"] = (cube.ward_by_id.get(w["ward_id"]) or {}).get("name")
                w["code"] = (cube.ward_by_id.get(w["ward_id"]) or {}).get("code")
    for n, i in enumerate(issues):
        i["id"] = f"{i['category']}:{'-'.join(str(w['ward_id']) for w in i['wards'][:3])}"
        i["summary"] = {"text": _template_summary(i), "ai_generated": False}
        if with_llm and n < 5:
            try:
                i["summary"] = _llm_summary(i)
            except Exception as exc:  # LLM down / rate limited: keep the deterministic summary
                log.warning("emerging summary fallback: %s", exc)
    return {"as_of": cube.asof.date().isoformat(), "thresholds": {"z_min": Z_MIN, "ratio_min": RATIO_MIN, "min_recent": MIN_RECENT},
            "issues": issues}


def feed(db: Session, cube: Cube, *, category: str | None = None, ward_id: int | None = None) -> dict[str, Any]:
    data, _ = cache.get_or_set(CACHE_KEY, 1800, lambda: build(db, cube))
    issues = [i for i in data["issues"] if (not category or i["category"] == category)
              and (ward_id is None or any(w["ward_id"] == ward_id for w in i["wards"]))]
    return {**data, "issues": issues}


_TOPIC_SQL = text(
    "WITH asof AS (SELECT max(submitted_at) AS t FROM complaints) "
    "SELECT c.description, c.submitted_at > a.t - interval '14 days' AS recent, c.ward_id FROM complaints c, asof a "
    "WHERE c.submitted_at > a.t - interval '28 days'"
)


def topics(db: Session, *, n_topics: int = 12) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        rows = db.execute(_TOPIC_SQL).all()
        if len(rows) < 50:
            return {"topics": [], "n_complaints": len(rows)}
        docs = [re.sub(r"\s+", " ", strip_pii(r[0])) for r in rows]
        recent = np.array([r[1] for r in rows])
        vec = TfidfVectorizer(max_df=0.3, min_df=5, stop_words=STOP, ngram_range=(1, 2), max_features=4000, token_pattern=r"(?u)[\wऀ-ॿ]{3,}")
        x = vec.fit_transform(docs)
        nmf = NMF(n_components=n_topics, init="nndsvda", random_state=0, max_iter=300)
        w = nmf.fit_transform(x)
        assign = w.argmax(axis=1)
        terms = np.array(vec.get_feature_names_out())
        out = []
        for t in range(n_topics):
            m = assign == t
            rec, prev = int((m & recent).sum()), int((m & ~recent).sum())
            n_rec, n_prev = max(int(recent.sum()), 1), max(int((~recent).sum()), 1)
            growth = (rec / n_rec) / max(prev / n_prev, 1e-3) if prev or rec else 0.0
            idx = np.where(m)[0][:3]
            out.append({"topic": t, "terms": terms[nmf.components_[t].argsort()[::-1][:6]].tolist(), "recent": rec, "previous": prev,
                        "growth": round(min(growth, 99.0), 2), "examples": [docs[i][:140] for i in idx]})
        return {"n_complaints": len(rows), "method": "TF-IDF + NMF, last 14 days vs previous 14 days",
                "topics": sorted(out, key=lambda x: -x["growth"])}

    val, _ = cache.get_or_set("analytics:topics:v1", 3600, run)
    return val
