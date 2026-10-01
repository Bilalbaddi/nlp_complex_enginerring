"""Embedding providers. `HFEmbedder` calls the Hugging Face Inference API (multilingual sentence-transformers model);
`HashingEmbedder` is an offline, deterministic fallback (character/word n-gram hashing).

IMPORTANT: vectors from different providers live in different spaces. Only HF embeddings are ever stored in
the database; the hashing embedder exists for offline comparison, tests and demos.
"""

import zlib
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from app.core.config import get_settings
from app.nlp.hf_client import HFClient, HFError
from app.nlp.preprocess import normalize_text, tokens

MAX_CHARS = 1000  # complaints are short; longer text is truncated before it is sent


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


def _l2(x: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.where(norm == 0, 1.0, norm)


class HFEmbedder:
    def __init__(self, client: HFClient | None = None) -> None:
        s = get_settings()
        self.client = client or HFClient()
        self.name = f"hf:{self.client.model}"
        self.dim = s.embedding_dim
        self.batch = s.hf_batch_size
        self.concurrency = s.hf_concurrency

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        chunks = [[(t or " ")[:MAX_CHARS] for t in texts[i : i + self.batch]] for i in range(0, len(texts), self.batch)]
        if len(chunks) == 1 or self.concurrency <= 1:
            out = [self.client.feature_extraction(c) for c in chunks]
        else:  # the client throttles globally, so parallel calls still respect the requests-per-minute budget
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                out = list(pool.map(self.client.feature_extraction, chunks))
        arr = np.vstack(out)
        if arr.shape[1] != self.dim:
            raise HFError(
                f"Model returned {arr.shape[1]}-dim vectors but EMBEDDING_DIM is {self.dim}; "
                "choose a 384-dim model or change EMBEDDING_DIM (needs a migration)"
            )
        return _l2(arr)


class HashingEmbedder:
    """Signed feature hashing of word uni/bi-grams and character 3-5-grams into `dim` dimensions."""

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or get_settings().embedding_dim
        self.name = "hashing"

    def _features(self, text: str) -> list[str]:
        toks = tokens(text)
        feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        t = f" {normalize_text(text)} "
        feats += [t[i : i + n] for n in (3, 4, 5) for i in range(len(t) - n + 1)]
        return feats

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for f in self._features(text):
                h = zlib.crc32(f.encode("utf-8"))
                out[row, h % self.dim] += 1.0 if (h >> 16) & 1 else -1.0
        return _l2(np.sign(out) * np.sqrt(np.abs(out)))  # sub-linear term frequency


def get_embedder(provider: str | None = None) -> EmbeddingProvider:
    """'hf' | 'hashing' | None/'auto' (HF when a token is configured, else hashing)."""
    s = get_settings()
    choice = provider or s.embedding_provider
    if choice == "auto":
        choice = "hf" if s.hf_configured else "hashing"
    return HFEmbedder() if choice == "hf" else HashingEmbedder()
