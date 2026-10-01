"""Monthly complaint-volume forecasting (statsmodels, CPU, milliseconds per series; nothing is downloaded).

Candidates per series: `naive` (mean of last 3 months), `seasonal_naive` (same month last year, level-adjusted),
`ets_flat` (damped-trend exponential smoothing, no season) and `ets_seasonal` (additive damped trend + 12-month season,
only with >= 24 points). The model with the lowest rolling-origin MAE is used. Backtest: 3 origins (the last 9, 6 and
3 months held out in turn), each forecasting 3 months ahead. MAPE ignores months with zero actual volume.

Prediction interval: mean +/- 1.96 * sigma * sqrt(h), sigma = std of in-sample one-step residuals (floored at
sqrt(mean) as complaint counts are roughly Poisson). Lower bound clipped at 0.
"""

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
from statsmodels.tsa.holtwinters import ExponentialSmoothing

warnings.filterwarnings("ignore")
CANDIDATES = ["naive", "seasonal_naive", "ets_flat", "ets_seasonal"]
ORIGINS = (9, 6, 3)
BACKTEST_H = 3


def _point(model: str, y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (h-step forecast, in-sample one-step residuals)."""
    n = len(y)
    if model == "naive":
        level = float(y[-3:].mean())
        return np.full(h, level), y[3:] - np.array([y[i - 3 : i].mean() for i in range(3, n)]) if n > 3 else np.zeros(1)
    if model == "seasonal_naive":
        if n < 14:
            raise ValueError("too short")
        scale = float(y[-6:].mean() / max(y[-18:-12].mean(), 1e-9)) if n >= 18 else 1.0
        scale = min(max(scale, 0.5), 2.0)
        fc = np.array([y[n - 12 + (i % 12)] * scale for i in range(h)])
        return fc, y[12:] - y[:-12] * scale
    seasonal = model == "ets_seasonal"
    if seasonal and n < 24:
        raise ValueError("too short")
    fit = ExponentialSmoothing(y, trend="add", damped_trend=True, seasonal="add" if seasonal else None,
                               seasonal_periods=12 if seasonal else None, initialization_method="estimated").fit()
    return np.asarray(fit.forecast(h)), np.asarray(y - fit.fittedvalues)


def _safe(model: str, y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray] | None:
    try:
        fc, res = _point(model, y, h)
        return (np.clip(fc, 0, None), res) if np.all(np.isfinite(fc)) else None
    except Exception:
        return None


@dataclass
class Fit:
    model: str
    mean: list[float]
    lo: list[float]
    hi: list[float]
    backtest: dict[str, Any]


def backtest(y: np.ndarray) -> dict[str, dict[str, float]]:
    scores: dict[str, dict[str, float]] = {}
    for m in CANDIDATES:
        errs: list[float] = []
        pct: list[float] = []
        for k in ORIGINS:
            if len(y) - k < 12:
                continue
            out = _safe(m, y[: len(y) - k], BACKTEST_H)
            if out is None:
                errs = []
                break
            actual = y[len(y) - k : len(y) - k + BACKTEST_H]
            pred = out[0][: len(actual)]
            errs += list(np.abs(actual - pred))
            pct += [abs(a - p) / a for a, p in zip(actual, pred) if a > 0]
        if errs:
            scores[m] = {"mae": round(float(np.mean(errs)), 2), "mape_pct": round(100 * float(np.mean(pct)), 1) if pct else None}
    return scores


def forecast(y: np.ndarray, h: int) -> Fit:
    scores = backtest(y)
    best = min(scores, key=lambda m: scores[m]["mae"]) if scores else "naive"
    out = _safe(best, y, h) or _safe("naive", y, h)
    assert out is not None
    fc, res = out
    sigma = max(float(np.std(res)) if len(res) > 2 else 0.0, float(np.sqrt(max(fc.mean(), 1.0))))
    width = 1.96 * sigma * np.sqrt(np.arange(1, h + 1))
    return Fit(best, [round(float(v), 1) for v in fc], [round(float(max(v, 0)), 1) for v in fc - width],
               [round(float(v), 1) for v in fc + width], {"models": scores, "chosen": best, **scores.get(best, {})})
