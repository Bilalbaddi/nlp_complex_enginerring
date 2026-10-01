"""Offline MockLLMProvider: deterministic, template-based answers so the whole app works without an API key."""

import json
import re
from collections.abc import Iterator, Sequence
from typing import Any

from app.llm.base import LLMProvider, LLMResponse, Message, ToolCall

_KEYWORDS = {
    "water_supply": ["water", "pani", "paani", "tap", "pipeline", "pressure"],
    "drainage": ["drain", "sewer", "waterlog", "overflow", "gutter", "naala", "nala"],
    "roads": ["road", "pothole", "sadak", "footpath", "crater"],
    "garbage": ["garbage", "waste", "kachra", "dustbin", "trash"],
    "streetlights": ["streetlight", "street light", "lamp", "pole", "dark"],
    "encroachment": ["encroach", "hawker", "illegal", "parking"],
    "public_health": ["mosquito", "dengue", "disease", "hospital", "stagnant", "fogging"],
    "electricity": ["electric", "power", "voltage", "transformer", "bijli"],
    "parks": ["park", "garden", "playground", "bench"],
    "noise": ["noise", "loud", "speaker", "music", "dj"],
}


def guess_category(text: str) -> str:
    t = text.lower()
    scores = {c: sum(k in t for k in ks) for c, ks in _KEYWORDS.items()}
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] else "roads"


class MockLLMProvider(LLMProvider):
    name = "mock"
    model = "mock-demo"

    def _resp(self, text: str) -> LLMResponse:
        return LLMResponse(text=text, model=self.model, provider=self.name, prompt_tokens=0, completion_tokens=len(text) // 4)

    def chat(
        self, messages: Sequence[Message], *, model: str | None = None, json_mode: bool = False,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> LLMResponse:
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if json_mode:
            cat = guess_category(last)
            return self._resp(json.dumps({"category": cat, "confidence": 0.5, "reason": "keyword match (demo mode)"}))
        first = re.sub(r"\s+", " ", last).strip()[:240]
        return self._resp(
            "[Demo mode: no Groq key is configured, so this is a template answer, not AI analysis.]\n"
            f"Summary of the request: {first}"
        )

    def chat_with_tools(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]], *, model: str | None = None,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> LLMResponse:
        if any(m["role"] == "tool" for m in messages):  # results are in: just present them
            data = [m["content"] for m in messages if m["role"] == "tool"][-1]
            return self._resp(f"[Demo mode] Tool results:\n{data[:1500]}")
        q = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "").lower()
        for t in tools:
            f = t["function"]
            if any(w in q for w in re.split(r"[_\s]+", f["name"]) if len(w) > 3):
                r = self._resp("")
                r.tool_calls = [ToolCall("mock-1", f["name"], {})]
                return r
        return self._resp("[Demo mode] I can answer questions about KPIs, SLA, emerging issues and forecasts once a Groq key is set.")

    def stream(
        self, messages: Sequence[Message], *, model: str | None = None, temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        for w in self.chat(messages).text.split(" "):
            yield w + " "
