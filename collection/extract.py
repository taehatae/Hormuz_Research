"""Body extraction (trafilatura only) and success judgement. No network.

Success = all of (thresholds in config["extract"]):
  URL path is not a listing page (/topic/, /tag/, /tags/, /category/)  -> else non_article_page
  body is not mostly code/CSS/markup                                     -> else code_or_markup
  body is not dominated by paywall / subscription text                  -> else paywall
  body is not dominated by cookie / login / consent text                -> else boilerplate_only
  >= min_body_words words                                                -> else too_short
  words / nc_word_count >= min_words_ratio_to_nc (skipped if nc missing) -> else partial_body
  contains "hormuz" (case-insensitive)                                   -> else no_hormuz
  language_detected == required_language                                 -> else not_english
  normalised body hash differs from earlier successes                    -> else exact_duplicate
The first failing check is the failure_reason.

Recorded only (not criteria): words_ratio_to_nc, snippet_match, language_detected,
hormuz_mention_count, strait_of_hormuz_count, title_has_hormuz, page_type_suspect (live),
sentence_count, title_body_overlap, body_near_title, non_alpha_ratio, over_10000_words,
code_token_density, paywall_share, boilerplate_share.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from functools import lru_cache
from urllib.parse import urlsplit

import trafilatura
from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0
logging.getLogger("trafilatura").setLevel(logging.ERROR)

QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'",
                        "“": '"', "”": '"', "„": '"', "″": '"', "«": '"', "»": '"'})
HORMUZ_RE = re.compile(r"hormuz", re.I)
STRAIT_RE = re.compile(r"strait\s+of\s+hormuz|hormuz\s+strait", re.I)
SENT_SPLIT = re.compile(r"(?<=[.!?。])[\"'”’)]*\s+")
CODE_TOKENS = re.compile(r"//|=>|\bfunction\b|\bvar\b|\bconst\b|px;|:\s*\w+;|\{|\}|</?\w+>")
COMMON_PUNCT = set(".,;:!?'\"()-–—’‘“”%$&/…")
TOKEN = re.compile(r"[a-z0-9]+")


def norm_text(s: str) -> str:
    """Whitespace- and quote-normalised, lower-cased text (snippet matching and hashing)."""
    return re.sub(r"\s+", " ", (s or "").translate(QUOTES)).strip().lower()


@lru_cache(maxsize=8)
def _compiled(patterns: tuple) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


def mention_counts(body: str) -> tuple[int, int]:
    """(occurrences of 'hormuz', of 'strait of hormuz' or 'hormuz strait'), case-insensitive."""
    return len(HORMUZ_RE.findall(body or "")), len(STRAIT_RE.findall(body or ""))


def sentences(body: str) -> list[str]:
    """Lines split further into sentences; units containing at least one letter."""
    out = []
    for line in (body or "").split("\n"):
        for s in SENT_SPLIT.split(line.strip()):
            if any(ch.isalpha() for ch in s):
                out.append(s)
    return out


def snippet_match(snippet: str, body: str, chars: int, step: int) -> tuple[bool | None, float | None]:
    sn = norm_text(snippet)
    sn = re.sub(r"(\.\.\.|…)$", "", sn).strip()[:chars]
    if len(sn) < 20:
        return None, None
    nb = norm_text(body)
    if sn in nb:
        return True, 1.0
    best = 0.0
    n = len(sn)
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(sn)
    for i in range(0, max(1, len(nb) - n + 1), step):
        sm.set_seq1(nb[i:i + n])
        if sm.real_quick_ratio() <= best or sm.quick_ratio() <= best:
            continue
        best = max(best, sm.ratio())
        if best >= 0.999:
            break
    return False, round(best, 4)


def boilerplate_shares(units: list[str], ec: dict) -> tuple[float, float]:
    """Word share of sentences matching paywall patterns / any boilerplate pattern."""
    total = sum(len(u.split()) for u in units) or 1
    pay = _compiled(tuple(ec["paywall_patterns"]))
    boil = _compiled(tuple(ec["paywall_patterns"] + ec["boilerplate_patterns"]))
    pw = sum(len(u.split()) for u in units if pay.search(u))
    bw = sum(len(u.split()) for u in units if boil.search(u))
    return pw / total, bw / total


def code_token_density(body: str) -> float:
    return 100 * len(CODE_TOKENS.findall(body or "")) / max(len((body or "").split()), 1)


def non_alpha_ratio(body: str) -> float:
    ns = [c for c in (body or "") if not c.isspace()]
    if not ns:
        return 0.0
    return sum(not (c.isascii() and c.isalnum() or c in COMMON_PUNCT) for c in ns) / len(ns)


def title_metrics(title: str, body: str, head_words: int) -> tuple[float | None, bool | None]:
    tt = list(dict.fromkeys(t for t in TOKEN.findall((title or "").lower()) if len(t) >= 3))
    if not tt:
        return None, None
    btoks = TOKEN.findall((body or "").lower())
    head = set(btoks[:head_words])
    overlap = sum(t in head for t in tt) / len(tt)
    near = len(btoks) <= len(TOKEN.findall((title or "").lower())) * 1.5 + 5 and overlap >= 0.8
    return round(overlap, 4), bool(near)


def page_type(url: str, final_url: str | None, ec: dict) -> str | None:
    paths = {urlsplit(u).path.lower() for u in (url, final_url) if u}
    if any(p_ in path for path in paths for p_ in ec["non_article_path_patterns"]):
        return "non_article"
    if any(p_ in path for path in paths for p_ in ec["live_path_patterns"]):
        return "live"
    return None


def extract_and_judge(html: bytes, url: str, final_url: str | None, title: str, snippet: str,
                      nc_word_count: str, ec: dict, success_hashes: set) -> dict:
    out = {"extract_status": "failed", "failure_reason": None, "body": None, "body_word_count": 0,
           "has_hormuz": None, "words_ratio_to_nc": None, "snippet_contained": None, "snippet_match": None,
           "language_detected": None, "body_sha1": None, "title_extracted": None,
           "paywall_share": None, "boilerplate_share": None,
           "hormuz_mention_count": None, "strait_of_hormuz_count": None,
           "title_has_hormuz": bool(HORMUZ_RE.search(title or "")), "page_type_suspect": None,
           "sentence_count": None, "title_body_overlap": None, "body_near_title": None,
           "non_alpha_ratio": None, "over_10000_words": None, "code_token_density": None}
    ptype = page_type(url, final_url, ec)
    out["page_type_suspect"] = "live" if ptype == "live" else None
    try:
        res = trafilatura.extract(html, url=final_url or url, include_comments=ec["include_comments"],
                                  favor_precision=ec["favor_precision"], with_metadata=ec["with_metadata"],
                                  output_format="json")
    except Exception as e:  # trafilatura can raise on malformed input
        out["failure_reason"] = "non_article_page" if ptype == "non_article" else f"extractor_error:{type(e).__name__}"
        return out
    if not res:
        out["failure_reason"] = "non_article_page" if ptype == "non_article" else "no_text_extracted"
        return out
    data = json.loads(res)
    body = (data.get("text") or "").strip()
    out["title_extracted"] = data.get("title")
    out["body"] = body
    words = len(body.split())
    out["body_word_count"] = words
    out["has_hormuz"] = ec["required_keyword"].lower() in body.lower()
    out["hormuz_mention_count"], out["strait_of_hormuz_count"] = mention_counts(body)
    nc = None
    try:
        nc = float(nc_word_count)
        out["words_ratio_to_nc"] = round(words / nc, 4) if nc > 0 else None
    except (TypeError, ValueError):
        pass
    out["snippet_contained"], out["snippet_match"] = snippet_match(
        snippet, body, ec["snippet_match_chars"], ec["snippet_window_step"])
    try:
        out["language_detected"] = detect(body[: ec["language_detect_chars"]]) if words else None
    except LangDetectException:
        out["language_detected"] = None
    out["body_sha1"] = hashlib.sha1(norm_text(body).encode("utf-8")).hexdigest() if body else None
    units = sentences(body)
    pw, bw = boilerplate_shares(units, ec) if units else (0.0, 0.0)
    out["paywall_share"], out["boilerplate_share"] = round(pw, 3), round(bw, 3)
    out["sentence_count"] = len(units)
    out["title_body_overlap"], out["body_near_title"] = title_metrics(title, body, ec["title_overlap_head_words"])
    out["non_alpha_ratio"] = round(non_alpha_ratio(body), 4)
    out["over_10000_words"] = words > ec["over_words_flag"]
    out["code_token_density"] = round(code_token_density(body), 3)

    thr = ec["boilerplate_share_threshold"]
    ratio = out["words_ratio_to_nc"]
    if ptype == "non_article":
        reason = "non_article_page"
    elif out["code_token_density"] >= ec["code_token_density_per_100_words"]:
        reason = "code_or_markup"
    elif (words < ec["min_body_words"] and pw > 0) or pw >= thr:
        reason = "paywall"
    elif bw >= thr:
        reason = "boilerplate_only"
    elif words < ec["min_body_words"]:
        reason = "too_short"
    elif ratio is not None and ratio < ec["min_words_ratio_to_nc"]:
        reason = "partial_body"
    elif not out["has_hormuz"]:
        reason = "no_hormuz"
    elif out["language_detected"] != ec["required_language"]:
        reason = "not_english"
    elif out["body_sha1"] in success_hashes:
        reason = "exact_duplicate"
    else:
        out["extract_status"] = "success"
        return out
    out["failure_reason"] = reason
    return out
