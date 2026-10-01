"""Provider-agnostic LLM interface. Groq and the offline mock both implement it."""

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

Message = dict[str, Any]  # {"role": "system"|"user"|"assistant"|"tool", "content": str, ...}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMError(RuntimeError):
    """Any failure talking to the LLM provider."""


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def chat(
        self, messages: Sequence[Message], *, model: str | None = None, json_mode: bool = False,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> LLMResponse: ...

    def complete(
        self, prompt: str, *, system: str | None = None, model: str | None = None, json_mode: bool = False,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> LLMResponse:
        msgs: list[Message] = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        return self.chat(msgs, model=model, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens)

    @abstractmethod
    def chat_with_tools(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]], *, model: str | None = None,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    def stream(
        self, messages: Sequence[Message], *, model: str | None = None, temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]: ...
