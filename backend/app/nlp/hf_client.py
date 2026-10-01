"""Minimal client for the Hugging Face Inference API (feature extraction). Nothing is downloaded locally.

Handles: client-side throttling, HTTP 429 (Retry-After / exponential backoff), 503 model cold-start
(`estimated_time`), timeouts, and permanent errors (401/403 auth, 402 quota) which are never retried.
"""

import logging
import threading
import time
from typing import Any

import httpx
import numpy as np

from app.core.config import get_settings

log = logging.getLogger(__name__)


class HFError(RuntimeError):
    """Any failure talking to Hugging Face."""


class HFAuthError(HFError):
    """The token is missing, invalid or lacks permission. Retrying cannot help."""


class HFQuotaError(HFError):
    """The account's free inference credits are used up (HTTP 402). Retrying cannot help until they reset."""


class HFClient:
    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        s = get_settings()
        self.token = token if token is not None else s.huggingfacehub_api_token
        self.model = model or s.embedding_model
        self.base_url = (base_url or s.hf_api_base).rstrip("/")
        self.max_retries = s.hf_max_retries
        self.min_interval = 60.0 / max(1, s.hf_requests_per_minute)
        self._client = client or httpx.Client(timeout=s.hf_timeout_s)
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_call = 0.0

    @property
    def url(self) -> str:
        return f"{self.base_url}/{self.model}/pipeline/feature-extraction"

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()

    def feature_extraction(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 array, one sentence embedding per input text."""
        if not self.token:
            raise HFAuthError("HUGGINGFACEHUB_API_TOKEN is not set")
        body = {"inputs": texts, "options": {"wait_for_model": True}}
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        last = "no attempt made"
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                r = self._client.post(self.url, json=body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                self._sleep(min(2.0**attempt, 30))
                continue
            if r.status_code == 200:
                return self._parse(r.json(), len(texts))
            detail = r.text[:200].replace("\n", " ")
            if r.status_code in (401, 403):
                raise HFAuthError(f"Hugging Face rejected the token (HTTP {r.status_code}): {detail}")
            if r.status_code == 402:
                raise HFQuotaError(f"Hugging Face inference credits are exhausted (HTTP 402): {detail}")
            if r.status_code == 429:
                delay = float(r.headers.get("retry-after", 0) or 0) or min(2.0 ** (attempt + 1), 60)
            elif r.status_code == 503:  # model is loading
                try:
                    delay = float(r.json().get("estimated_time", 10))
                except (ValueError, AttributeError):
                    delay = 10.0
                delay = min(max(delay, 2.0), 45.0)
            elif r.status_code >= 500:
                delay = min(2.0**attempt, 30)
            else:
                raise HFError(f"Hugging Face returned HTTP {r.status_code}: {detail}")
            last = f"HTTP {r.status_code}: {detail}"
            log.warning("HF %s (attempt %d/%d), waiting %.0fs", last, attempt + 1, self.max_retries + 1, delay)
            self._sleep(delay)
        raise HFError(f"Hugging Face request failed after {self.max_retries + 1} attempts: {last}")

    @staticmethod
    def _parse(data: Any, n: int) -> np.ndarray:
        arr = np.asarray(data, dtype=np.float32)
        if arr.ndim == 3:  # token-level output (n, tokens, dim): mean-pool
            arr = arr.mean(axis=1)
        if arr.ndim == 1 and n == 1:
            arr = arr[None, :]
        if arr.ndim != 2 or arr.shape[0] != n:
            raise HFError(f"Unexpected embedding response shape {arr.shape} for {n} inputs")
        return arr
