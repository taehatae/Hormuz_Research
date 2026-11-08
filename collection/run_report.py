"""Stage report from the collection database (no network).

  python collection/run_report.py --stage smoke
Writes <output_dir>/reports/<stage>_report.md and prints it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

from common import load_config, project_path


def md(df: pd.DataFrame) -> str:
    if df.empty:
        return "(없음)"
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in df.itertuples(index=False):
        lines.append("| " + " | ".join("" if pd.isna(v) else (f"{v:,.3f}" if isinstance(v, float) else str(v))
                                       for v in r) + " |")
    return "\n".join(lines)


def counts(s: pd.Series, name: str) -> pd.DataFrame:
    c = s.fillna("(none)").astype(str).value_counts().rename_axis(name).reset_index(name="count")
    c["share_pct"] = (100 * c["count"] / c["count"].sum()).round(1)
    return c


def robots_class(note: str | None, reason: str | None) -> str:
    if reason == "robots_disallow":
        return "disallow (요청 안 함)"
    if not isinstance(note, str) or not note:
        return "(robots 확인 전 건너뜀)"
    if note == "robots_rules":
        return "rules 적용 (허용)"
    if note.startswith("robots_absent"):
        return "없음 (4xx)"
    return "못 받아옴 (5xx/timeout/오류)"


def dist(s: pd.Series) -> dict:
    s = s.dropna().astype(float)
    if s.empty:
        return {"n": 0}
    q = s.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    return {"n": int(len(s)), "p05": round(q[0.05], 3), "p25": round(q[0.25], 3), "median": round(q[0.5], 3),
            "p75": round(q[0.75], 3), "p95": round(q[0.95], 3)}


def _title(cand, aid, fallback=None):
    return cand.at[aid, "title"] if aid in cand.index else fallback


def _start(bodies, aid, n=200):
    return repr((bodies.get(aid, "") or "")[:n])


def extended_sections(a, bodies, cand, ev, con, cfg, summ) -> list[str]:
    L = []
    ok = a[a["fetch_status"] == "ok"].copy()
    succ = a[a["extract_status"] == "success"].copy()
    n = len(a)

    # ---- domains ----
    dom = (a[a["request_sent"].fillna(0).astype(int) == 1].groupby("domain_key")
           .agg(requests=("article_id", "size"), success=("extract_status", lambda s: int((s == "success").sum())),
                main_failure=("failure_reason", lambda s: s.dropna().value_counts().index[0] if s.notna().any() else "")))
    dom["success_rate_pct"] = (100 * dom["success"] / dom["requests"]).round(1)
    d5 = dom[dom["requests"] >= 3]
    L += ["## 도메인별 성공률 (요청 3건 이상)", "",
          f"요청 3건 이상 도메인 {len(d5)}개 (요청 1건 이상 {len(dom)}개).", "",
          "### 상위 15", "", md(d5.sort_values(["success_rate_pct", "requests"], ascending=[False, False]).head(15).reset_index()), "",
          "### 하위 15", "", md(d5.sort_values(["success_rate_pct", "requests"], ascending=[True, False]).head(15).reset_index()), ""]
    allev = pd.read_sql("SELECT * FROM events WHERE kind='domain_skipped_after_failures'", con)
    allrows = pd.read_sql("SELECT domain_key, failure_reason, request_sent FROM attempts", con)
    skip_rows = allrows[allrows["failure_reason"] == "domain_skipped_after_failures"].groupby("domain_key").size()
    if len(allev):
        sk = allev.rename(columns={"detail": "domain_key"})[["domain_key", "stage", "at"]]
        sk["skipped_records_so_far"] = sk["domain_key"].map(skip_rows).fillna(0).astype(int)
        sk["block_reasons"] = sk["domain_key"].map(
            allrows[allrows["request_sent"] == 1].groupby("domain_key")["failure_reason"]
            .agg(lambda s: ", ".join(f"{k}×{v}" for k, v in s.value_counts().items())))
    else:
        sk = pd.DataFrame()
    L += ["## 자동 건너뛴 도메인 (연속 5건 blocked/paywall)", "", f"{len(sk)}개", "", md(sk), ""]

    # ---- language ----
    ne = succ[succ["language_detected"] != "en"]
    L += ["## 성공 건 중 language_detected ≠ en", "", f"{len(ne)}건 / 성공 {len(succ)}건", ""]
    L += [f"- [{r['language_detected']}] {r['domain_key']} — {_title(cand, r['article_id'], r['title_extracted'])!r} — "
          f"본문 {_start(bodies, r['article_id'], 150)}" for _, r in ne.head(10).iterrows()] + [""]

    # ---- words ratio ----
    lowr = succ[succ["words_ratio_to_nc"].astype(float) < 0.5]
    L += ["## 성공 건 중 words_ratio_to_nc < 0.5", "", f"{len(lowr)}건 / 성공 {len(succ)}건 "
          f"({100 * len(lowr) / max(len(succ), 1):.1f}%)", ""]
    ex = lowr.sample(min(10, len(lowr)), random_state=2026) if len(lowr) else lowr
    L += [f"- {r['domain_key']} | 비율 {r['words_ratio_to_nc']} ({int(r['body_word_count'])}단어) | snippet "
          f"{'포함' if r['snippet_contained'] == 1 else r['snippet_match']} | {_start(bodies, r['article_id'])}"
          for _, r in ex.iterrows()] + [""]
    ok["ratio"] = ok["words_ratio_to_nc"].astype(float)
    ext = pd.concat([ok.nsmallest(5, "ratio"), ok.nlargest(5, "ratio")])
    L += ["### words_ratio_to_nc 극단값 10건 (본문을 받은 건, 최저 5 + 최고 5)", ""]
    L += [f"- {r['ratio']:.3f} | {r['domain_key']} | {r['extract_status']}/{r['failure_reason']} | "
          f"{int(r['body_word_count'] or 0)}단어 | {_start(bodies, r['article_id'])}" for _, r in ext.iterrows()] + [""]

    # ---- mentions ----
    hm = succ["hormuz_mention_count"].astype(int)
    bins = pd.cut(hm, [0, 1, 2, 3, 5, 10, 10_000], labels=["1", "2", "3", "4-5", "6-10", "11+"])
    sm = succ["strait_of_hormuz_count"].astype(int)
    L += ["## hormuz_mention_count (성공 건)", "",
          md(bins.value_counts().sort_index().rename_axis("hormuz_mentions").reset_index(name="count")), "",
          f"- strait_of_hormuz_count = 0인 성공 건: {int((sm == 0).sum())}건 (hormuz만 언급, 예: 'Hormuz' 단독·호르무즈 섬 등)", "",
          "### 언급 1회뿐인 성공 기사 10건", ""]
    one = succ[hm == 1]
    one = one.sample(min(10, len(one)), random_state=2026) if len(one) else one
    L += [f"- {r['domain_key']} — {_title(cand, r['article_id'], r['title_extracted'])!r} ({int(r['body_word_count'])}단어)"
          for _, r in one.iterrows()] + [""]

    # ---- no_hormuz / paywall ----
    nh = a[a["failure_reason"] == "no_hormuz"]
    nh = nh.sample(min(10, len(nh)), random_state=2026) if len(nh) else nh
    L += ["## no_hormuz 실패 예시 10건", ""]
    L += [f"- {r['domain_key']} — {_title(cand, r['article_id'], r['title_extracted'])!r} | {int(r['body_word_count'])}단어, "
          f"비율 {r['words_ratio_to_nc']} | {_start(bodies, r['article_id'])}" for _, r in nh.iterrows()] + [""]
    pw = a[a["failure_reason"].isin(["paywall", "boilerplate_only"])]
    L += ["## 페이월·안내 문구 판정이 걸린 사례", "", f"{len(pw)}건", ""]
    L += [f"- {r['failure_reason']} | {r['domain_key']} | {int(r['body_word_count'])}단어 | paywall_share {r['paywall_share']}, "
          f"boilerplate_share {r['boilerplate_share']} | {_start(bodies, r['article_id'], 250)}" for _, r in pw.iterrows()] + [""]
    part = succ[succ["paywall_share"].astype(float) > 0]
    L += [f"- 성공 건 중 페이월 문구가 일부 단락에 있는 건(비중 50% 미만): {len(part)}건", ""]
    L += [f"  - {r['domain_key']} | paywall_share {r['paywall_share']} | 비율 {r['words_ratio_to_nc']}" for _, r in part.head(10).iterrows()] + [""]

    # ---- speed ----
    el = summ.get("elapsed_sec") or 0
    allc = pd.read_sql("SELECT extract_status FROM attempts", con)
    tot_succ = int((allc["extract_status"] == "success").sum())
    rate = len(succ) / max(n, 1)
    remain = cfg["sampling"]["target_success_total"] - tot_succ
    need_records = remain / max(rate, 1e-9)
    sec_per = el / max(n, 1)
    L += ["## 처리 속도와 본 실행 예상", "",
          f"- 이 단계: {n}건 / {el:,.0f}초 = 기록당 {sec_per:.2f}초, 시간당 약 {3600 / max(sec_per, 1e-9):,.0f}건 "
          f"(동시 작업 {cfg['fetch']['max_workers']}개)",
          f"- 누적 성공 {tot_succ}건, 목표까지 {remain:,}건. 이 단계 성공률({100 * rate:.1f}%) 유지 시 약 {need_records:,.0f}건 시도 → "
          f"약 **{need_records * sec_per / 3600:,.1f}시간**",
          "- 본 실행 후반에는 후보가 적은 주·도메인 상한·자동 건너뛰기로 속도와 성공률이 달라질 수 있다.", ""]

    # ---- criteria sensitivity (all stages so far) ----
    allx = pd.read_sql("SELECT * FROM attempts WHERE fetch_status='ok'", con)
    base_ok = allx[~allx["failure_reason"].isin(["paywall", "boilerplate_only"]) & allx["body_word_count"].notna()]
    wc = base_ok["body_word_count"].astype(float)
    hh = base_ok["has_hormuz"].fillna(0).astype(int) == 1
    nodup = base_ok["failure_reason"] != "exact_duplicate"
    cur = (base_ok["extract_status"] == "success")
    rows = []
    for t in (100, 150, 200, 250):
        rows.append({"criterion": f"단어 수 ≥ {t} (+ hormuz 포함)", "successes": int(((wc >= t) & hh & nodup).sum())})
    rows += [
        {"criterion": "현재 기준", "successes": int(cur.sum())},
        {"criterion": "현재 + snippet 포함 또는 유사도 ≥ 0.8", "successes": int((cur & ((base_ok["snippet_contained"] == 1) | (base_ok["snippet_match"].astype(float) >= 0.8) | base_ok["snippet_match"].isna())).sum())},
        {"criterion": "현재 + words_ratio_to_nc ≥ 0.5", "successes": int((cur & (base_ok["words_ratio_to_nc"].astype(float).fillna(1) >= 0.5)).sum())},
        {"criterion": "현재 + language_detected = en", "successes": int((cur & (base_ok["language_detected"] == "en")).sum())},
        {"criterion": "현재 + hormuz 2회 이상", "successes": int((cur & (base_ok["hormuz_mention_count"].astype(float) >= 2)).sum())},
        {"criterion": "현재 + 위 snippet·비율·en 세 가지 모두", "successes": int((cur & ((base_ok["snippet_contained"] == 1) | (base_ok["snippet_match"].astype(float) >= 0.8) | base_ok["snippet_match"].isna()) & (base_ok["words_ratio_to_nc"].astype(float).fillna(1) >= 0.5) & (base_ok["language_detected"] == "en")).sum())},
    ]
    sens = pd.DataFrame(rows)
    sens["vs_current_pct"] = (100 * sens["successes"] / max(int(cur.sum()), 1)).round(1)
    L += ["## 성공 기준 민감도 (누적 전체 단계, 기록된 지표로 재계산, 요청 없음)", "",
          f"본문을 받은 {len(allx)}건 중 페이월·안내 문구 판정 제외 {len(base_ok)}건 기준.", "", md(sens), ""]
    return L


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--examples", type=int, default=3)
    ap.add_argument("--extended", action="store_true", help="pilot-style sections and criteria sensitivity")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = project_path(cfg["output_dir"])
    con = sqlite3.connect(out / cfg["runs"]["db"])
    a = pd.read_sql("SELECT * FROM attempts WHERE stage=?", con, params=(args.stage,))
    bodies = pd.read_sql("SELECT * FROM bodies", con).set_index("article_id")["body"]
    ev = pd.read_sql("SELECT * FROM events WHERE stage=?", con, params=(args.stage,))
    robots = pd.read_sql("SELECT * FROM robots", con)
    cand = pd.read_csv(project_path(cfg["input_csv"]), usecols=["article_id", "title", "nc_content_snippet"],
                       dtype=str, keep_default_na=False).set_index("article_id")

    n = len(a)
    sent = int(a["request_sent"].fillna(0).astype(int).sum())
    succ = a[a["extract_status"] == "success"]
    end = ev[ev["kind"] == "stage_end"]
    summ = json.loads(end["detail"].iloc[-1]) if len(end) else {}
    start_t = pd.to_datetime(ev[ev["kind"] == "stage_start"]["at"]).min()
    end_t = pd.to_datetime(end["at"]).max() if len(end) else None
    stage_origins = set(a["url"].str.extract(r"^(https?://[^/]+)")[0])
    rb = robots[robots["origin"].isin(stage_origins)]

    a["robots_class"] = [robots_class(nt, r) for nt, r in zip(a["robots_note"], a["failure_reason"])]
    a["outcome"] = a["failure_reason"].where(a["extract_status"] != "success", "success")
    skipped = a[a["request_sent"].fillna(0).astype(int) == 0]

    lines = [f"# {args.stage} 단계 결과", "",
             f"- 기록 수: **{n}** / 실제 기사 HTTP 요청이 나간 건: **{sent}** / 요청 없이 건너뛴 건: **{len(skipped)}**",
             f"- robots.txt 요청: 이 단계에서 확인한 사이트(origin) {len(rb)}곳 (사이트당 1회, 결과는 DB에 캐시)",
             f"- 층: {a['stratum'].value_counts().to_dict()} / 주 {a['week_id'].nunique()}개에 분산",
             f"- 소요 시간: {summ.get('elapsed_sec')}초 (시작 {start_t}, 종료 {end_t}), 기록당 평균 "
             f"{(summ.get('elapsed_sec') or 0) / max(n, 1):.2f}초 (동시 작업 {cfg['fetch']['max_workers']}개 기준)",
             f"- 실제 요청 건의 기사 요청 소요 시간(재시도·대기 포함) 중앙값 {a['elapsed_sec'].median():.2f}초, "
             f"최대 {a['elapsed_sec'].max():.2f}초",
             f"- 종료 사유: {summ.get('stop_reason')}", "",
             "## 요청 없이 건너뛴 건", "", md(counts(skipped["failure_reason"], "reason")), "",
             "## HTTP 상태 코드", "", md(counts(a["http_status"].astype("Int64").astype(str).replace("<NA>", None),
                                               "http_status")), "",
             "## fetch_status", "", md(counts(a["fetch_status"], "fetch_status")), "",
             "## 최종 결과 (성공 / failure_reason)", "", md(counts(a["outcome"], "outcome")), "",
             f"- 성공 {len(succ)}건 / 기록 {n}건 = **{100 * len(succ) / max(n, 1):.1f}%**, "
             f"요청 대비 {100 * len(succ) / max(sent, 1):.1f}%, 본문을 받은 건 대비 "
             f"{100 * len(succ) / max((a['fetch_status'] == 'ok').sum(), 1):.1f}%", "",
             "## robots.txt 처리 (기록 기준)", "", md(counts(a["robots_class"], "robots")), "",
             "사이트(origin) 기준:", "", md(counts(rb["note"], "robots_note")), "",
             "## 품질 지표 분포 (기록만, 성공 기준 아님)", ""]
    ext = a[a["body_word_count"].notna() & (a["fetch_status"] == "ok")]
    qrows = []
    for label, g in (("성공", ext[ext["extract_status"] == "success"]), ("실패", ext[ext["extract_status"] != "success"])):
        qrows.append({"group": label, "metric": "words_ratio_to_nc", **dist(g["words_ratio_to_nc"])})
        qrows.append({"group": label, "metric": "snippet_match", **dist(g["snippet_match"])})
        qrows.append({"group": label, "metric": "body_word_count", **dist(g["body_word_count"])})
    lines += [md(pd.DataFrame(qrows)), ""]
    sc = ext["snippet_contained"].map({1: "포함", 0: "미포함"}).fillna("snippet 없음/짧음")
    lines += ["snippet 앞부분 포함 여부(본문을 받은 건):", "",
              md(pd.crosstab(sc, ext["extract_status"]).reset_index()), "",
              "language_detected (본문을 받은 건):", "", md(counts(ext["language_detected"], "lang")), "",
              "## 도메인별 결과 (요청 2건 이상)", ""]
    dom = a.groupby("domain_key").agg(records=("article_id", "size"),
                                       success=("extract_status", lambda s: int((s == "success").sum())))
    lines += [md(dom[dom["records"] >= 2].sort_values("records", ascending=False).reset_index()), ""]
    lines += [f"## 성공 기사 예시 {args.examples}건", ""]
    for _, r in succ.sample(min(args.examples, len(succ)), random_state=2026).iterrows():
        body = bodies.get(r["article_id"], "") or ""
        lines += [f"### {cand.at[r['article_id'], 'title'] if r['article_id'] in cand.index else r['title_extracted']}",
                  f"- {r['url']} ({r['date']}, {r['domain_key']}, {int(r['body_word_count'])}단어, "
                  f"words_ratio_to_nc={r['words_ratio_to_nc']}, lang={r['language_detected']})",
                  f"- snippet_match: 포함={bool(r['snippet_contained']) if pd.notna(r['snippet_contained']) else None}, "
                  f"유사도={r['snippet_match']}",
                  f"- nc snippet 앞부분: {cand.at[r['article_id'], 'nc_content_snippet'][:150]!r}",
                  f"- 본문 앞 300자: {body[:300]!r}", ""]
    if args.extended:
        lines += extended_sections(a, bodies, cand, ev, con, cfg, summ)
    rep = "\n".join(lines)
    (out / "reports").mkdir(exist_ok=True)
    (out / "reports" / f"{args.stage}_report.md").write_text(rep, encoding="utf-8")
    print(rep)


if __name__ == "__main__":
    main()
