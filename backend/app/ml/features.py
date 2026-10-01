"""Shared feature helpers for the ML components."""

from datetime import datetime

import numpy as np

EPOCH = datetime(2000, 1, 1).astimezone()


def to_hours(times: list[datetime]) -> np.ndarray:
    """Hours since 2000-01-01 for each timestamp (float64)."""
    return np.array([(t - EPOCH).total_seconds() / 3600.0 for t in times], dtype=np.float64)


def recent_counts(group: np.ndarray, hours: np.ndarray, window: float = 168.0) -> np.ndarray:
    """For every row: how many EARLIER rows of the same group fall within `window` hours before it.

    `group` is an integer code per row; rows need not be sorted. Ties in time count as earlier by input order.
    """
    n = len(group)
    if n == 0:
        return np.zeros(0, dtype=np.int32)
    big = 1e7  # larger than any hour value
    key = group.astype(np.float64) * big + hours
    order = np.lexsort((np.arange(n), hours, group))
    sorted_key = key[order]
    rank = np.empty(n, dtype=np.int64)
    rank[order] = np.arange(n)
    lo = np.searchsorted(sorted_key, key - window, side="left")
    return np.maximum(rank - lo, 0).astype(np.int32)


def group_codes(*cols: np.ndarray) -> np.ndarray:
    """Combine several integer columns into one group code."""
    code = np.zeros(len(cols[0]), dtype=np.int64)
    for c in cols:
        code = code * (int(c.max()) + 2 if len(c) else 1) + (c.astype(np.int64) + 1)
    return code
