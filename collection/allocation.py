"""Weekly quota allocation within a stratum (no network).

1. Each stratum is cut into consecutive blocks of `block_days` days starting at
   the stratum start; the last block is truncated at the stratum end.
2. The stratum target is split over the blocks in proportion to each block's
   candidate count:  quota_w = clip(lambda * N_w, F_w, N_w), where
   F_w = sum_d min(daily_floor, N_d) is the block's guaranteed daily floors and
   N_w its candidate count; lambda makes the quotas sum to the target. Integer
   rounding is by largest remainder within the [F_w, N_w] bounds.
3. Inside a block the daily floors are secured first; the rest of the block's
   quota (the "remainder") is filled from the block's candidates shuffled with
   a fixed seed (that ordering is done at run time, not here).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def week_blocks(start: str, end: str, block_days: int) -> list[list]:
    days = list(pd.date_range(start, end, freq="D").date)
    return [days[i:i + block_days] for i in range(0, len(days), block_days)]


def allocate(weights: np.ndarray, target: int, lower: np.ndarray,
             upper: np.ndarray) -> tuple[np.ndarray, dict]:
    w = np.asarray(weights, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    info = {"target": int(target), "candidates": int(upper.sum()),
            "floor_total": int(lower.sum()), "feasible": True, "lambda": None, "shortfall": 0}

    if upper.sum() <= target:  # not enough candidates: take everything
        info["feasible"] = False
        info["shortfall"] = int(target - upper.sum())
        return upper.astype(int), info
    if lower.sum() > target:
        raise ValueError("floors exceed the stratum target")

    def total(lam: float) -> float:
        return np.clip(lam * w, lower, upper).sum()

    lo, hi = 0.0, 1.0
    while total(hi) < target:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if total(mid) < target:
            lo = mid
        else:
            hi = mid
    lam = hi
    cont = np.clip(lam * w, lower, upper)
    info["lambda"] = lam

    q = np.maximum(np.floor(cont).astype(int), lower.astype(int))
    remaining = int(target - q.sum())
    order = np.argsort(-(cont - np.floor(cont)), kind="stable")
    i = 0
    while remaining > 0:
        k = order[i % len(order)]
        if q[k] < upper[k]:
            q[k] += 1
            remaining -= 1
        i += 1
        if i > 10 * len(order) + target:
            raise RuntimeError("rounding did not converge")
    for k in order[::-1]:
        if remaining >= 0:
            break
        if q[k] > lower[k]:
            q[k] -= 1
            remaining += 1
    return q, info


def nearest_block_order(idx: int, n: int, previous_first: bool = True) -> list[int]:
    """Other blocks of the same stratum, nearest first, alternating sides."""
    out = []
    for dist in range(1, n):
        sides = (idx - dist, idx + dist) if previous_first else (idx + dist, idx - dist)
        out.extend(j for j in sides if 0 <= j < n)
    return out


def simulate_cross_block(quota: np.ndarray, capacity: np.ndarray,
                         previous_first: bool = True) -> tuple[np.ndarray, np.ndarray, int]:
    """Move each block's deficit (quota - capacity) to the nearest blocks with spare
    capacity. Returns (moved_out_per_block, received_per_block, unresolved_total)."""
    quota = np.asarray(quota, dtype=float)
    spare = np.maximum(np.asarray(capacity, dtype=float) - quota, 0.0)
    deficit = np.maximum(quota - np.asarray(capacity, dtype=float), 0.0)
    received = np.zeros_like(quota)
    unresolved = 0.0
    for i in np.argsort(-deficit, kind="stable"):
        need = deficit[i]
        for j in nearest_block_order(int(i), len(quota), previous_first):
            if need <= 0:
                break
            take = min(need, spare[j])
            spare[j] -= take
            received[j] += take
            need -= take
        unresolved += need
    return deficit, received, int(round(unresolved))


def headroom(count: float, quota: float) -> float:
    return math.inf if quota == 0 else count / quota
