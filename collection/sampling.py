"""Sampling frame for the body collection (no network).

Builds the same weekly allocation as plan.py (allocation.allocate over week blocks,
proportional to weekly candidate counts, daily floors secured first) and a fixed
attempt order per week: the week's candidates shuffled with a seed derived from
(random_seed, stratum index, week index).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from allocation import allocate, nearest_block_order, week_blocks
from common import load_candidates


@dataclass
class Week:
    idx: int                    # global index in allocation order
    stratum: str
    stratum_pos: int            # position inside its stratum
    week_id: str
    days: list                  # ISO date strings
    quota: int                  # planned successes
    floors: dict                # date -> min(daily_floor, candidates that day)
    order: list                 # candidate row positions (into frame) in attempt order
    target: int = 0             # live target (quota + transfers in - transfers out)
    transfers: list = field(default_factory=list)


def build_frame(cfg: dict) -> tuple[pd.DataFrame, list[Week]]:
    df = load_candidates(cfg)
    pop = df[(df["excluded_by"] == "") & (df["stratum"] != "")].reset_index(drop=True)
    pop["date_iso"] = pop["date"].map(lambda d: d.isoformat())
    sp = cfg["sampling"]
    seed = int(sp["random_seed"])
    weeks: list[Week] = []
    by_date = pop.groupby("date_iso").indices
    for si, s in enumerate(sp["strata"]):
        f = int(s["daily_floor"])
        blocks = week_blocks(s["start"], s["end"], int(sp["block_days"]))
        n_w = np.array([sum(len(by_date.get(d.isoformat(), [])) for d in b) for b in blocks])
        floor_w = np.array([sum(min(f, len(by_date.get(d.isoformat(), []))) for d in b) for b in blocks])
        quota, _ = allocate(n_w, s["target"], floor_w, n_w)
        for wi, b in enumerate(blocks):
            days = [d.isoformat() for d in b]
            rows = np.concatenate([by_date.get(d, np.array([], dtype=int)) for d in days]).astype(int)
            rows.sort()  # stable base order before the seeded shuffle
            rng = np.random.default_rng([seed, si, wi])
            order = rows[rng.permutation(len(rows))].tolist()
            weeks.append(Week(idx=len(weeks), stratum=s["name"], stratum_pos=wi,
                              week_id=f"{s['name']}_w{wi + 1:02d}", days=days, quota=int(quota[wi]),
                              floors={d: min(f, len(by_date.get(d, []))) for d in days},
                              order=order, target=int(quota[wi])))
    return pop, weeks


def stratum_neighbours(weeks: list[Week], w: Week, previous_first: bool) -> list[Week]:
    same = [x for x in weeks if x.stratum == w.stratum]
    return [same[j] for j in nearest_block_order(w.stratum_pos, len(same), previous_first)]
