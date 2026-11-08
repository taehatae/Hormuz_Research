"""Body extraction (trafilatura only) and success judgement. No network.

Success = all of: >= min_body_words words; contains the required keyword
(case-insensitive); not dominated by paywall/subscription/cookie/login text;
normalised body hash not equal to an earlier success.
Recorded but not used for success: words_ratio_to_nc, snippet_match, language_detected.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from functools import lru_cache

import trafilatura
from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0
logging.getLogger("trafilatura").setLevel(logging.ERROR)

QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'",
                        "“": '"', "”": '"', "„": '"', "″": '"', "«": '"', "»": '"'})


def norm_text(s: str) -> str:
    """Whitespace- and quote-normalised, lower-cased text (used for snippet matching and hashing)."""
    return re.sub(r"\s+", " ", (s or "").translate(QUOTES)).strip().lower()


@lru_cache(maxsize=8)
def _compiled(patterns: tuple) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


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


def boilerplate_shares(body: str, ec: dict) -> tuple[float, float]:
    """Word share of paragraphs matching paywall patterns / any boilerplate pattern."""
    paras = [p for p in body.split("\n") if p.strip()]
    total = sum(len(p.split()) for p in paras) or 1
    pay = _compiled(tuple(ec["paywall_patterns"]))
    boil = _compiled(tuple(ec["paywall_patterns"] + ec["boilerplate_patterns"]))
    pw = sum(len(p.split()) for p in paras if pay.search(p))
    bw = sum(len(p.split()) for p in paras if boil.search(p))
    return pw / total, bw / total


def extract_and_judge(html: bytes, url: str, snippet: str, nc_word_count: str, ec: dict,
                      success_hashes: set) -> dict:
    out = {"extract_status": "failed", "failure_reason": None, "body": None, "body_word_count": 0,
           "has_hormuz": None, "words_ratio_to_nc": None, "snippet_contained": None, "snippet_match": None,
           "language_detected": None, "body_sha1": None, "title_extracted": None,
           "paywall_share": None, "boilerplate_share": None}
    try:
        res = trafilatura.extract(html, url=url, include_comments=ec["include_comments"],
                                  favor_precision=ec["favor_precision"], with_metadata=ec["with_metadata"],
                                  output_format="json")
    except Exception as e:  # trafilatura can raise on malformed input
        out["failure_reason"] = f"extractor_error:{type(e).__name__}"
        return out
    if not res:
        out["failure_reason"] = "no_text_extracted"
        return out
    data = json.loads(res)
    body = (data.get("text") or "").strip()
    out["title_extracted"] = data.get("title")
    out["body"] = body
    words = len(body.split())
    out["body_word_count"] = words
    out["has_hormuz"] = ec["required_keyword"].lower() in body.lower()
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
    pw, bw = boilerplate_shares(body, ec) if body else (0.0, 0.0)
    out["paywall_share"], out["boilerplate_share"] = round(pw, 3), round(bw, 3)

    thr = ec["boilerplate_share_threshold"]
    paywall_hit = pw > 0
    if (words < ec["min_body_words"] and paywall_hit) or pw >= thr:
        reason = "paywall"
    elif bw >= thr:
        reason = "boilerplate_only"
    elif words < ec["min_body_words"]:
        reason = "too_short"
    elif not out["has_hormuz"]:
        reason = "no_hormuz"
    elif out["body_sha1"] in success_hashes:
        reason = "exact_duplicate"
    else:
        out["extract_status"] = "success"
        return out
    out["failure_reason"] = reason
    return out
