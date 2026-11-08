"""Re-judge already fetched articles from their saved gzip HTML with the current criteria
(no network). Run after the success criteria change.

- Backs up the database first (collection.before_rejudge_<UTC timestamp>.sqlite3).
- Rows whose domain is now in the pre-exclusion list -> extract_status 'excluded',
  failure_reason 'pre_excluded_domain' (the fetched data is kept).
- Rows with saved HTML are re-extracted and re-judged in attempt order (so the
  exact-duplicate check sees earlier successes first). Old/new verdicts go to the
  rejudge_log table.
- Rows without HTML (not fetched) are unchanged.
Writes <output_dir>/reports/rejudge_report.md.

  python collection/rejudge.py
"""
from __future__ import annotations

import gzip
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from collect import JUDGE_VERSION
from common import load_candidates, load_config, project_path
from extract import extract_and_judge
from run_report import md
from sampling import build_frame
from store import ATTEMPT_COLUMNS, Store

METRIC_COLS = ["extract_status", "failure_reason", "body_word_count", "has_hormuz", "words_ratio_to_nc",
               "snippet_contained", "snippet_match", "language_detected", "body_sha1", "title_extracted",
               "paywall_share", "boilerplate_share", "hormuz_mention_count", "strait_of_hormuz_count",
               "title_has_hormuz", "page_type_suspect", "sentence_count", "title_body_overlap", "body_near_title",
               "non_alpha_ratio", "over_10000_words", "code_token_density", "judged_with"]


def simulate_skips(rows: pd.DataFrame, reasons: set, limit: int) -> dict:
    """Domains that reach `limit` consecutive failures in `reasons` (attempt order, requested rows only)."""
    consec, first = Counter(), {}
    for r in rows.itertuples():
        if r.failure_reason in ("domain_skipped_after_failures", "domain_cap", "robots_disallow", "pre_excluded_domain"):
            continue
        if r.failure_reason in reasons:
            consec[r.domain_key] += 1
            if consec[r.domain_key] >= limit and r.domain_key not in first:
                first[r.domain_key] = r.attempted_at
        else:
            consec[r.domain_key] = 0
    return first


def main() -> None:
    cfg = load_config()
    ec = cfg["extract"]
    out = project_path(cfg["output_dir"])
    db = out / cfg["runs"]["db"]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = out / f"collection.before_rejudge_{ts}.sqlite3"
    src = sqlite3.connect(db)
    dst = sqlite3.connect(backup)
    src.backup(dst)
    dst.close()
    src.close()

    store = Store(db)  # adds any new columns
    con = store.con
    con.execute("CREATE TABLE IF NOT EXISTS rejudge_log (article_id TEXT, at TEXT, old_status TEXT, old_reason TEXT, "
                "new_status TEXT, new_reason TEXT, judged_with TEXT)")
    before = pd.read_sql("SELECT * FROM attempts", con).sort_values("attempted_at", kind="stable")
    cand = load_candidates(cfg).set_index("article_id")
    old_skip_events = pd.read_sql("SELECT detail FROM events WHERE kind='domain_skipped_after_failures'", con)["detail"].tolist()

    success_hashes: set = set()
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_reextracted = 0
    for r in before.itertuples():
        c = cand.loc[r.article_id] if r.article_id in cand.index else None
        new = None
        if c is not None and c["excluded_by"]:
            new = {"extract_status": "excluded", "failure_reason": "pre_excluded_domain", "judged_with": JUDGE_VERSION}
            body = None
        elif isinstance(r.html_path, str) and (out / r.html_path).exists():
            html = gzip.decompress((out / r.html_path).read_bytes())
            ex = extract_and_judge(html, r.url, r.final_url, c["title"], c["nc_content_snippet"], c["nc_word_count"],
                                   ec, success_hashes)
            body = ex.pop("body")
            ex["judged_with"] = JUDGE_VERSION
            new = ex
            n_reextracted += 1
            if ex["extract_status"] == "success":
                success_hashes.add(ex["body_sha1"])
        if new is None:
            continue
        sets = ", ".join(f"{k}=?" for k in new)
        with store.lock:
            con.execute("BEGIN")
            con.execute(f"UPDATE attempts SET {sets} WHERE article_id=?", [*new.values(), r.article_id])
            if body:
                con.execute("INSERT OR REPLACE INTO bodies VALUES (?,?)", (r.article_id, body))
            con.execute("INSERT INTO rejudge_log VALUES (?,?,?,?,?,?,?)",
                        (r.article_id, at, r.extract_status, r.failure_reason, new["extract_status"],
                         new["failure_reason"], JUDGE_VERSION))
            con.execute("COMMIT")
    store.event("rejudge", "rejudge", json.dumps({"backup": backup.name, "reextracted": n_reextracted,
                                                  "judged_with": JUDGE_VERSION}))

    after = pd.read_sql("SELECT * FROM attempts", con).sort_values("attempted_at", kind="stable")
    report(cfg, before, after, old_skip_events, backup.name, n_reextracted, con, cand)


def ex_lines(df: pd.DataFrame, bodies: pd.Series, cand: pd.DataFrame, metric: str, n: int = 5) -> list[str]:
    rows = df.head(n)
    return [f"- {r.domain_key} | {metric}={getattr(r, metric)} | {int(r.body_word_count)}단어 | "
            f"제목 {cand.at[r.article_id, 'title'][:90]!r} | 본문 {(bodies.get(r.article_id, '') or '')[:150]!r}"
            for r in rows.itertuples()]


def report(cfg, before, after, old_skips, backup, n_re, con, cand) -> None:
    out = project_path(cfg["output_dir"])
    bodies = pd.read_sql("SELECT * FROM bodies", con).set_index("article_id")["body"]
    b_out = before["failure_reason"].where(before["extract_status"] != "success", "success")
    a_out = after["failure_reason"].where(after["extract_status"] != "success", "success")
    a_out = a_out.where(after["extract_status"] != "excluded", "pre_excluded_domain")
    delta = pd.DataFrame({"before": b_out.value_counts(), "after": a_out.value_counts()}).fillna(0).astype(int)
    delta["change"] = delta["after"] - delta["before"]
    delta = delta[delta[["before", "after"]].sum(axis=1) > 0].sort_values("after", ascending=False).reset_index(names="outcome")

    m = before[["article_id", "domain_key", "stage"]].assign(before=b_out.values).merge(
        after[["article_id"]].assign(after=a_out.values), on="article_id")
    changed = m[m["before"] != m["after"]]
    trans = changed.groupby(["before", "after"]).size().rename("count").reset_index().sort_values("count", ascending=False)
    focus = changed[changed["domain_key"].isin(["monitorulapararii.ro", "theblaze.com", "thenationalpulse.com", "poncacitynews.com"])]
    focus_all = m[m["domain_key"].isin(["monitorulapararii.ro", "theblaze.com", "thenationalpulse.com", "poncacitynews.com"])
                  & m["before"].isin(["paywall", "boilerplate_only"])]

    s = after[after["extract_status"] == "success"].copy()
    wc = s["body_word_count"].astype(float)
    s100 = s[(wc >= 100) & (wc < 150)]
    nc_missing = s100["words_ratio_to_nc"].isna().sum()

    fc = cfg["fetch"]
    new_skips = simulate_skips(after, set(fc["domain_skip_failure_reasons"]), fc["domain_consecutive_block_limit"])
    skip_cmp = pd.DataFrame({"domain_key": sorted(set(old_skips) | set(new_skips))})
    skip_cmp["old_rule"] = skip_cmp["domain_key"].isin(old_skips)
    skip_cmp["new_rule"] = skip_cmp["domain_key"].isin(new_skips)

    th = s["title_has_hormuz"].fillna(0).astype(int) == 1
    b2 = s["hormuz_mention_count"].astype(float) >= 2
    rel = pd.DataFrame([
        {"group": "제목에 hormuz", "count": int(th.sum())},
        {"group": "제목에 없음", "count": int((~th).sum())},
        {"group": "제목에 등장 또는 본문 2회 이상", "count": int((th | b2).sum())},
        {"group": "제목에 없고 본문 1회", "count": int((~th & ~b2).sum())},
    ])
    rel["share_pct"] = (100 * rel["count"] / max(len(s), 1)).round(1)
    th_all = after[after["fetch_status"] == "ok"]["title_has_hormuz"].fillna(0).astype(int)

    # item 8 metrics
    sc = s["sentence_count"].astype(float)
    tov = s["title_body_overlap"].astype(float)
    nar = s["non_alpha_ratio"].astype(float)
    q1, q3 = nar.quantile([0.25, 0.75])
    nar_thr = max(q3 + 3 * (q3 - q1), 0.02)
    m8 = pd.DataFrame([
        {"criterion": "sentence_count < 3", "successes_removed": int((sc < 3).sum())},
        {"criterion": "body_near_title (본문이 제목과 거의 같음)", "successes_removed": int(s["body_near_title"].fillna(0).astype(int).sum())},
        {"criterion": "title_body_overlap = 1.0 (참고: 정상 기사에 흔함)", "successes_removed": int((tov == 1.0).sum())},
        {"criterion": "title_body_overlap < 0.2 (하위 꼬리, 참고)", "successes_removed": int((tov < 0.2).sum())},
        {"criterion": f"non_alpha_ratio > {nar_thr:.3f} (Q3+3·IQR, 최소 0.02)", "successes_removed": int((nar > nar_thr).sum())},
        {"criterion": "non_alpha_ratio > 0.05", "successes_removed": int((nar > 0.05).sum())},
        {"criterion": "over_10000_words", "successes_removed": int(s["over_10000_words"].fillna(0).astype(int).sum())},
    ])
    m8["share_of_successes_pct"] = (100 * m8["successes_removed"] / max(len(s), 1)).round(2)

    # domain cap and weekly fulfilment
    cap = int(round(cfg["sampling"]["domain_cap_share"] * cfg["sampling"]["target_success_total"]))
    dom = s.groupby("domain_key").size().sort_values(ascending=False).rename("successes").reset_index()
    dom["cap"] = cap
    dom["share_of_cap_pct"] = (100 * dom["successes"] / cap).round(1)
    _, weeks = build_frame(cfg)
    ws = s.groupby("week_id").size()
    wk = pd.DataFrame([{"week_id": w.week_id, "stratum": w.stratum, "quota": w.quota,
                        "successes": int(ws.get(w.week_id, 0))} for w in weeks])
    wk["fulfilled_pct"] = (100 * wk["successes"] / wk["quota"]).round(1)
    st = wk.groupby("stratum")[["quota", "successes"]].sum().reset_index()
    st["fulfilled_pct"] = (100 * st["successes"] / st["quota"]).round(2)

    ec = cfg["extract"]
    L = ["# 재판정 결과 (저장된 HTML, 네트워크 미사용)", "",
         f"- 기준: `{JUDGE_VERSION}` — 하한 {ec['min_body_words']}단어, language = {ec['required_language']}, "
         f"words_ratio_to_nc ≥ {ec['min_words_ratio_to_nc']}(nc 없으면 미적용), 문장 단위 안내 문구 비중, "
         f"code_or_markup(코드 토큰 ≥ {ec['code_token_density_per_100_words']}/100단어), "
         f"non_article_page({', '.join(ec['non_article_path_patterns'])}), biztoc.com 사전 제외",
         f"- 다시 추출한 건: {n_re} / 전체 기록 {len(after)} (smoke + pilot). 백업: `{backup}`", "",
         "## 1. 성공 건수 변화와 사유별 증감", "",
         f"- 성공: **{int((b_out == 'success').sum())} → {int((a_out == 'success').sum())}**", "",
         md(delta), "",
         "### 바뀐 판정 (이전 → 이후)", "", md(trans), "",
         f"- 100~149단어 성공: **{len(s100)}건**, 그중 nc_word_count가 없어 비율 기준이 적용되지 않은 건: **{int(nc_missing)}건**", "",
         "## 2. 판정 로직 수정으로 바뀐 건 (monitorulapararii, theblaze, thenationalpulse, poncacitynews)", ""]
    for r in focus_all.itertuples():
        a = after.set_index("article_id").loc[r.article_id]
        L.append(f"- {r.domain_key}: {r.before} → **{r.after}** | {int(a['body_word_count'])}단어, "
                 f"code_token_density {a['code_token_density']}, paywall_share {a['paywall_share']}, "
                 f"boilerplate_share {a['boilerplate_share']}")
    L += ["", f"그 밖에 바뀐 건 전체 {len(changed)}건 목록은 DB `rejudge_log` 테이블.", "",
          "## 3. 자동 건너뛰기 대상 도메인 (새 집계 규칙: " + ", ".join(fc["domain_skip_failure_reasons"]) + ")", "",
          md(skip_cmp), "",
          f"- 이전 규칙 {len(old_skips)}개, 새 규칙 {len(new_skips)}개, 차이: "
          f"{sorted(set(old_skips) ^ set(new_skips)) or '없음'}", "",
          "## 4. title_has_hormuz와 관련성", "",
          f"- 본문을 받은 건 중 제목에 hormuz: {int(th_all.sum())}/{len(th_all)} ({100 * th_all.mean():.1f}%)", "",
          "성공 건 기준:", "", md(rel), "",
          "## 5. 추가 품질 지표 (기록만) — 기준 적용 시 줄어드는 성공 건", "",
          md(m8), "",
          "### sentence_count < 3 예시", ""] + ex_lines(s[sc < 3], bodies, cand, "sentence_count") + [
          "", "### body_near_title 예시 (본문이 제목과 거의 같음)", ""] + \
         ex_lines(s[s["body_near_title"].fillna(0).astype(int) == 1], bodies, cand, "title_body_overlap") + [
          "", "### title_body_overlap 하위 꼬리 예시 (< 0.2, 제목과 본문이 어긋날 가능성)", ""] + \
         ex_lines(s[tov < 0.2].sort_values("title_body_overlap"), bodies, cand, "title_body_overlap") + [
          "", f"### non_alpha_ratio 상위 이상치 예시 (> {nar_thr:.3f})", ""] + \
         ex_lines(s[nar > nar_thr].sort_values("non_alpha_ratio", ascending=False), bodies, cand, "non_alpha_ratio") + [
          "", "### over_10000_words 예시", ""] + \
         ex_lines(s[s["over_10000_words"].fillna(0).astype(int) == 1], bodies, cand, "body_word_count") + [
          "", "분포 참고 (성공 건): " + json.dumps({
              "sentence_count": {k: float(v) for k, v in sc.quantile([.01, .05, .5]).items()},
              "title_body_overlap": {k: float(v) for k, v in tov.quantile([.05, .25, .5, .75]).items()},
              "non_alpha_ratio": {k: round(float(v), 4) for k, v in nar.quantile([.5, .9, .99, 1]).items()}}),
          "", "## 6. 도메인 상한 집계 (재판정 후 성공 기준, 상한 " + str(cap) + "건)", "",
          md(dom.head(15)), "",
          "## 7. 주별 충족 현황 (재판정 후)", "", md(st), "", md(wk), ""]
    rep = "\n".join(L)
    (out / "reports").mkdir(exist_ok=True)
    (out / "reports" / "rejudge_report.md").write_text(rep, encoding="utf-8")
    print(rep)


if __name__ == "__main__":
    main()
