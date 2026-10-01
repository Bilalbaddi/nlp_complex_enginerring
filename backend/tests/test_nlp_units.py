import httpx
import numpy as np
import pytest

from app.nlp.embeddings import HashingEmbedder, HFEmbedder, get_embedder
from app.nlp.entities import extract_entities
from app.nlp.hf_client import HFAuthError, HFClient, HFError, HFQuotaError
from app.nlp.preprocess import normalize_text
from app.nlp.sentiment import label, sentiment
from app.nlp.urgency import days_mentioned, level_for, urgency_score


# ------------------------------------------------------------------ Hugging Face client (mocked transport only)
def _client(handler, **kw) -> HFClient:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    c = HFClient(token="hf_test", model="m/x", base_url="https://hf.test", client=httpx.Client(transport=httpx.MockTransport(handler)),
                 sleep=sleeps.append, **kw)
    c.min_interval = 0
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


def test_hf_success_parses_sentence_and_token_level_outputs() -> None:
    seen = {}

    def ok(req: httpx.Request) -> httpx.Response:
        seen["auth"], seen["url"], seen["body"] = req.headers["authorization"], str(req.url), req.content
        return httpx.Response(200, json=[[0.1] * 384, [0.2] * 384])

    v = _client(ok).feature_extraction(["a", "b"])
    assert v.shape == (2, 384) and seen["auth"] == "Bearer hf_test"
    assert seen["url"] == "https://hf.test/m/x/pipeline/feature-extraction" and b"wait_for_model" in seen["body"]
    tok = _client(lambda r: httpx.Response(200, json=[[[1.0, 3.0], [3.0, 5.0]]])).feature_extraction(["a"])
    assert tok.tolist() == [[2.0, 4.0]]  # token-level output is mean-pooled
    with pytest.raises(HFError):
        _client(lambda r: httpx.Response(200, json=[[1.0, 2.0]])).feature_extraction(["a", "b", "c"])  # wrong row count


def test_hf_retries_rate_limit_and_model_loading_then_succeeds() -> None:
    calls = []

    def flaky(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, text="slow down")
        if len(calls) == 2:
            return httpx.Response(503, json={"error": "loading", "estimated_time": 7.0})
        return httpx.Response(200, json=[[0.5] * 4])

    c = _client(flaky)
    assert c.feature_extraction(["x"]).shape == (1, 4) and len(calls) == 3
    assert c.sleeps == [3.0, 7.0]  # type: ignore[attr-defined]  # honoured Retry-After and estimated_time


def test_hf_permanent_errors_are_not_retried() -> None:
    n = {"c": 0}

    def deny(req: httpx.Request) -> httpx.Response:
        n["c"] += 1
        return httpx.Response(401, text="Invalid username or password.")

    with pytest.raises(HFAuthError):
        _client(deny).feature_extraction(["x"])
    assert n["c"] == 1
    with pytest.raises(HFQuotaError):
        _client(lambda r: httpx.Response(402, text="credits depleted")).feature_extraction(["x"])
    with pytest.raises(HFAuthError):
        HFClient(token="", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))).feature_extraction(["x"])
    with pytest.raises(HFError):
        _client(lambda r: httpx.Response(400, text="bad input")).feature_extraction(["x"])


def test_hf_gives_up_after_max_retries() -> None:
    c = _client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(HFError, match="failed after"):
        c.feature_extraction(["x"])
    assert len(c.sleeps) == c.max_retries + 1  # type: ignore[attr-defined]


def test_hf_embedder_batches_normalises_and_checks_dimension() -> None:
    sizes: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        n = len(json.loads(req.content)["inputs"])
        sizes.append(n)
        return httpx.Response(200, json=[[3.0, 4.0] + [0.0] * 382] * n)

    e = HFEmbedder(_client(handler))
    e.batch = 4
    out = e.embed([f"t{i}" for i in range(10)])
    assert sorted(sizes) == [2, 4, 4] and out.shape == (10, 384)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0) and np.allclose(out[0, :2], [0.6, 0.8])
    bad = HFEmbedder(_client(lambda r: httpx.Response(200, json=[[1.0] * 10])))
    with pytest.raises(HFError, match="384"):
        bad.embed(["x"])


def test_provider_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "embedding_provider", "auto")  # the test environment pins "hashing"; this test is about auto-selection
    monkeypatch.setattr(s, "huggingfacehub_api_token", "")
    assert isinstance(get_embedder(), HashingEmbedder)
    monkeypatch.setattr(s, "huggingfacehub_api_token", "hf_x")
    assert isinstance(get_embedder(), HFEmbedder) and isinstance(get_embedder("hashing"), HashingEmbedder)


def test_hashing_embedder_is_deterministic_and_similarity_aware() -> None:
    e = HashingEmbedder()
    a, b, c = e.embed(["Street light not working near Ganesh Mandir", "Street lights not working near Ganesh Mandir",
                       "Garbage has not been collected for days"])
    assert np.allclose(e.embed(["Street light not working near Ganesh Mandir"])[0], a)
    assert float(a @ b) > 0.6 > float(a @ c) and abs(np.linalg.norm(a) - 1) < 1e-5
    assert e.embed([""]).shape == (1, 384)


# ------------------------------------------------------------------ text processing
def test_normalize_text_strips_contact_details_and_keeps_devanagari() -> None:
    t = normalize_text("Call 9876543210 or mail a@b.com   नल से पानी  https://x.y/z ")
    assert "9876543210" not in t and "a@b.com" not in t and "http" not in t and "नल से पानी" in t
    assert normalize_text("") == "" and normalize_text(None) == ""  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text,expected",
    [("Problem resolved quickly, thank you", "positive"), ("Prompt action by the ward staff, very satisfied", "positive"),
     ("Very slow response from the department", "negative"), ("Officials did not visit, only the status was marked closed", "negative"),
     ("Complaint was not resolved even after several reminders", "negative"),
     ("समस्या जल्दी सुलझ गई, धन्यवाद", "positive"), ("कई बार याद दिलाने के बाद भी शिकायत का समाधान नहीं हुआ", "negative"),
     ("समस्या लवकर सुटली, धन्यवाद", "positive"), ("Kai baar yaad dilane ke baad bhi complaint solve nahi hui", "negative"),
     ("Issue was fixed but it took longer than promised", "neutral")],
)
def test_sentiment_labels(text: str, expected: str) -> None:
    assert label(sentiment(text)) == expected, sentiment(text)


def test_sentiment_bounds_and_empty() -> None:
    assert sentiment("") == 0.0 and sentiment("   ") == 0.0
    for t in ["dengue vomiting accident dangerous stink", "thank you excellent quick prompt good", "नहीं नहीं नहीं"]:
        assert -1.0 <= sentiment(t) <= 1.0


# ------------------------------------------------------------------ entities
def test_entity_extraction_across_languages() -> None:
    wards = ["Old City", "Shivaji Nagar"]
    e = extract_entities("Road surface washed away on Temple Road near Water Tank and Municipal School No. 12 in Old City area", wards)
    assert e["roads"] == ["Temple Road"] and e["landmarks"] == ["Water Tank", "Municipal School No. 12"] and e["areas"] == ["Old City"]
    assert extract_entities("सड़क टूटी है Ganesh Mandir के पास Station Road पर")["landmarks"] == ["Ganesh Mandir"]
    assert extract_entities("रस्त्यावर खड्डा आहे MG Road जवळ")["roads"] == ["MG Road"]
    assert extract_entities("Street light not working") == {"roads": [], "landmarks": [], "areas": []}  # no false positives
    assert extract_entities("", wards) == {"roads": [], "landmarks": [], "areas": []}
    assert extract_entities("complaint from shivaji nagar", wards)["areas"] == ["Shivaji Nagar"]  # gazetteer is case-insensitive


# ------------------------------------------------------------------ urgency
def test_urgency_is_explained_and_ordered_sensibly() -> None:
    bad = urgency_score("Electric wire fallen on the road, sparking near the school. Children at risk, nobody responded for 6 days. Urgent!",
                        "electricity", -0.8)
    mild = urgency_score("Park gate is locked during the day", "parks", 0.0)
    assert bad.level in ("critical", "high") and mild.level == "low" and bad.score > mild.score + 40
    assert any("health/safety" in r for r in bad.reasons) and any("vulnerable" in r for r in bad.reasons)
    assert bad.score <= 100 and mild.score >= 0


def test_urgency_hindi_marathi_hinglish_and_breach_risk() -> None:
    hi = urgency_score("ट्रांसफॉर्मर से चिंगारी निकल रही है, बच्चों के लिए खतरा है", "electricity", -0.5)
    assert hi.level in ("critical", "high")
    base = urgency_score("Street light not working", "streetlights").score
    assert urgency_score("Street light not working", "streetlights", breach_risk=1.0).score == pytest.approx(base + 15, abs=0.1)
    assert urgency_score("Street light not working", "streetlights", cluster_count=8).score > base


def test_days_mentioned_and_levels() -> None:
    assert days_mentioned("since 5 days") == 5 and days_mentioned("for the past 9 days") == 9
    assert days_mentioned("पिछले 4 दिनों से") == 4 and days_mentioned("no water for a week") == 7 and days_mentioned("today") == 0
    assert [level_for(x) for x in (10, 30, 50, 70)] == ["low", "medium", "high", "critical"]
