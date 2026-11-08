"""Shared helpers for body collection v3: config, candidate loading, domain rules.

Code and config live in <project>/collection/; all data outputs go to
config["output_dir"]. No network access happens in this module (tldextract is
configured to use its bundled public-suffix snapshot only).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent  # collection -> project root


def load_config(path: Path | None = None) -> dict:
    path = path or CODE_DIR / "config.json"
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    strata = cfg["sampling"]["strata"]
    total = sum(s["target"] for s in strata)
    if total != cfg["sampling"]["target_success_total"]:
        raise ValueError(f"stratum targets sum to {total}, not target_success_total")
    return cfg


def project_path(rel: str) -> Path:
    return PROJECT_ROOT / rel


def domain_key(host: str, strip_www: bool = True) -> str:
    """Lower-cased host without port; optionally without a leading 'www.'."""
    h = (host or "").strip().lower().split(":")[0].rstrip(".")
    if strip_www and h.startswith("www."):
        h = h[4:]
    return h


@lru_cache(maxsize=1)
def _tld_extractor():
    import tldextract
    # suffix_list_urls=() -> never fetch the suffix list online; use the bundled snapshot.
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


@lru_cache(maxsize=None)
def registrable_domain(host: str) -> str:
    """Registrable domain (e.g. timesofindia.indiatimes.com -> indiatimes.com).
    Used as the unit for per-domain request spacing. Falls back to the bare host
    for IP addresses or hosts without a known suffix."""
    h = domain_key(host, strip_www=False)
    ext = _tld_extractor()(h)
    rd = ext.top_domain_under_public_suffix if hasattr(ext, "top_domain_under_public_suffix") else ext.registered_domain
    return rd or h


def excluded_pattern(host: str, patterns: list[str]) -> str:
    """Return the exclusion pattern the host matches (exact or subdomain suffix), else ''."""
    h = domain_key(host, strip_www=False)
    for p in patterns:
        p = p.lower()
        if h == p or h.endswith("." + p):
            return p
    return ""


def assign_stratum(dates: pd.Series, strata: list[dict]) -> pd.Series:
    out = pd.Series("", index=dates.index, dtype=object)
    for s in strata:
        lo, hi = pd.Timestamp(s["start"]).date(), pd.Timestamp(s["end"]).date()
        out[(dates >= lo) & (dates <= hi)] = s["name"]
    return out


def load_candidates(cfg: dict, columns: list[str] | None = None) -> pd.DataFrame:
    """Read only the configured columns as strings and add date / stratum / domain helpers."""
    cols = columns or cfg["input_columns"]
    df = pd.read_csv(
        project_path(cfg["input_csv"]), usecols=cols, dtype=str,
        keep_default_na=False, encoding="utf-8-sig",
    )
    ts = pd.to_datetime(df[cfg["date_column"]], utc=True, errors="coerce")
    df["date"] = ts.dt.date
    df["date_parse_failed"] = ts.isna()
    df["stratum"] = assign_stratum(df["date"], cfg["sampling"]["strata"])
    strip = cfg["sampling"]["domain_key_strip_www"]
    df["domain_key"] = df["domain"].map(lambda h: domain_key(h, strip))
    df["rate_limit_key"] = df["domain"].map(registrable_domain)
    df["excluded_by"] = df["domain"].map(lambda h: excluded_pattern(h, cfg["excluded_domains"]))
    return df
