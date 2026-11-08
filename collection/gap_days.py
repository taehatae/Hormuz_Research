"""Rebuild the per-query collection gap-day list from the original collection log.

Source: the saved cell output of methods/hormuz_2026_news_textmining_filtered.ipynb
(the NewsCatcher collection loop). Each line "[q] A ~ B | hits=N" is one request;
ranges with hits <= split threshold were fetched (leaves). Because to_ was a bare
date, a leaf [A, B] covered A 00:00:00 .. B 00:00:00, so B after midnight is a gap
for that query. No network access.

Writes config["gap_days"]["csv"] (one row per research-period day) and a small
JSON with the consistency checks.

Usage (from project root):  python collection/gap_days.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from common import load_config, project_path

NODE = re.compile(r'^(\s*)\[(.+?)\] (\d{4}-\d\d-\d\d) ~ (\d{4}-\d\d-\d\d) \| hits=([\d,]+)')


def collection_log(nb_path: Path, marker: str) -> str:
    cells = json.loads(nb_path.read_text(encoding="utf-8"))["cells"]
    for c in cells:
        text = "".join("".join(o.get("text", [])) for o in c.get("outputs", []) if "text" in o)
        if marker in text:
            return text
    raise ValueError("collection log not found in notebook outputs")


def main() -> None:
    cfg = load_config()
    gc = cfg["gap_days"]
    log = collection_log(project_path(gc["source_notebook"]), gc["log_marker"])
    nodes = [dict(q=m.group(2), a=m.group(3), b=m.group(4), hits=int(m.group(5).replace(",", "")))
             for m in map(NODE.match, log.splitlines()) if m]
    leaves = [n for n in nodes if n["hits"] <= gc["split_threshold_hits"]]

    queries = gc["queries"]  # {"soh": "\"Strait of Hormuz\"", "hs": "\"Hormuz Strait\""}
    start, end = cfg["research_period"]["start"], cfg["research_period"]["end"]
    days = pd.date_range(start, end, freq="D")
    out = pd.DataFrame({"date": days.strftime("%Y-%m-%d")})
    checks = {"source_notebook": gc["source_notebook"], "leaf_hits_total": sum(n["hits"] for n in leaves)}
    for key, q in queries.items():
        ql = sorted((n for n in leaves if n["q"] == q), key=lambda n: n["a"])
        if not ql:
            raise ValueError(f"no leaves for query {q}")
        # leaves must tile the period without gaps or overlaps (by date label)
        for prev, nxt in zip(ql, ql[1:]):
            if pd.Timestamp(prev["b"]) + pd.Timedelta(days=1) != pd.Timestamp(nxt["a"]):
                raise ValueError(f"leaves do not tile: {prev} / {nxt}")
        if ql[0]["a"] != start or ql[-1]["b"] != end:
            raise ValueError(f"leaves for {q} do not span the research period")
        ends = {n["b"] for n in ql}
        rng = {}
        for n in ql:
            for d in pd.date_range(n["a"], n["b"]).strftime("%Y-%m-%d"):
                rng[d] = (f"{n['a']}~{n['b']}", n["hits"])
        out[f"gap_{key}"] = out["date"].isin(ends)
        out[f"{key}_leaf_range"] = out["date"].map(lambda d: rng[d][0])
        out[f"{key}_leaf_hits"] = out["date"].map(lambda d: rng[d][1])
        checks[f"{key}_leaves"] = len(ql)
        checks[f"{key}_gap_days"] = int(out[f"gap_{key}"].sum())

    if (out["gap_hs"] & ~out["gap_soh"]).any():
        raise ValueError("a day is a gap only for 'Hormuz Strait'; gap_type categories need extending")
    out["collection_gap_day"] = out["gap_soh"] | out["gap_hs"]
    out["collection_gap_type"] = "none"
    out.loc[out["gap_soh"] & ~out["gap_hs"], "collection_gap_type"] = "soh_only"
    out.loc[out["gap_soh"] & out["gap_hs"], "collection_gap_type"] = "both"
    checks["gap_type_counts"] = out["collection_gap_type"].value_counts().to_dict()

    dest = project_path(gc["csv"])
    out.to_csv(dest, index=False, encoding="utf-8")
    dest.with_suffix(".checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=1))
    print("wrote", dest)


if __name__ == "__main__":
    main()
