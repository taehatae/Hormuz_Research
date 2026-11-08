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
    if not note:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--examples", type=int, default=3)
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
    rep = "\n".join(lines)
    (out / "reports").mkdir(exist_ok=True)
    (out / "reports" / f"{args.stage}_report.md").write_text(rep, encoding="utf-8")
    print(rep)


if __name__ == "__main__":
    main()
