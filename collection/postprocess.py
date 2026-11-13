"""Post-processing report (stage 5). No network access, nothing is deleted.

  python collection/collect.py --stage report

Writes to <output_dir>/postprocess/:
  POSTPROCESS_REPORT.md, near_duplicate_groups.csv, week_fulfilment.csv, domain_summary.csv,
  country_verification_domains.csv, country_distribution_nc.csv
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

from common import load_candidates, load_config, project_path
from run_report import md
from sampling import build_frame


# ---------------------------------------------------------------- near duplicates
def near_duplicate_groups(bodies: pd.Series, nd: dict) -> pd.DataFrame:
    """MinHash LSH over word shingles; connected components of candidate pairs whose
    estimated Jaccard >= threshold. Returns article_id, near_dup_group_id, group_size."""
    from datasketch import MinHash, MinHashLSH
    k, perm, thr = int(nd["shingle_words"]), int(nd["num_perm"]), float(nd["jaccard_threshold"])
    lsh = MinHashLSH(threshold=thr, num_perm=perm)
    sigs = {}
    tok = re.compile(r"[a-z0-9]+")
    for aid, body in bodies.items():
        w = tok.findall((body or "").lower())
        sh = {" ".join(w[i:i + k]) for i in range(max(1, len(w) - k + 1))}
        m = MinHash(num_perm=perm, seed=int(nd["seed"]))
        for s in sh:
            m.update(s.encode("utf-8"))
        sigs[aid] = m
        lsh.insert(aid, m)
    parent = {a: a for a in sigs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for aid, m in sigs.items():
        for other in lsh.query(m):
            if other != aid and sigs[aid].jaccard(sigs[other]) >= thr:
                ra, rb = find(aid), find(other)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    comp = defaultdict(list)
    for a in sigs:
        comp[find(a)].append(a)
    rows = []
    gid = 0
    for members in sorted((v for v in comp.values() if len(v) > 1), key=lambda v: (-len(v), min(v))):
        gid += 1
        rows += [{"article_id": a, "near_dup_group_id": f"nd{gid:05d}", "group_size": len(members)} for a in members]
    return pd.DataFrame(rows, columns=["article_id", "near_dup_group_id", "group_size"])


def cc_tld(domain: str) -> str:
    last = (domain or "").rsplit(".", 1)[-1].lower()
    return last if len(last) == 2 and last.isalpha() else ""


def mode_str(s: pd.Series) -> str:
    s = s.replace("", np.nan).dropna()
    return s.value_counts().index[0] if len(s) else ""


def main(cfg: dict | None = None) -> None:
    cfg = cfg or load_config()
    pc = cfg["postprocess"]
    out = project_path(cfg["output_dir"])
    dest = out / "postprocess"
    dest.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out / cfg["runs"]["db"])
    att = pd.read_sql("SELECT * FROM attempts", con)
    ev = pd.read_sql("SELECT * FROM events", con)
    cand = load_candidates(cfg)
    pop = cand[(cand["excluded_by"] == "") & (cand["stratum"] != "")].copy()
    pop["month"] = pop["date"].map(lambda d: d.isoformat()[:7])
    ci = cand.set_index("article_id")
    succ = att[att["extract_status"] == "success"].copy()
    succ = succ.join(ci[["title", "nc_country", "nc_source_name", "nc_word_count", "collection_gap_type",
                         "nc_published_date_precision"]], on="article_id")
    succ["month"] = succ["date"].str[:7]
    bodies = pd.read_sql("SELECT a.article_id, b.body FROM attempts a JOIN bodies b USING(article_id) "
                         "WHERE a.extract_status='success'", con).set_index("article_id")["body"]
    n_s = len(succ)
    target = cfg["sampling"]["target_success_total"]
    cap = int(round(cfg["sampling"]["domain_cap_share"] * target))
    L = ["# 후처리 리포트 (5단계, 네트워크 미사용)", "",
         f"- 생성 시각(UTC): {pd.Timestamp.now(tz='UTC').isoformat(timespec='seconds')}",
         f"- 성공 **{n_s:,}건** / 목표 {target:,}건 · 기록 {len(att):,}건 · 실제 기사 요청 "
         f"{int(att['request_sent'].fillna(0).astype(int).sum()):,}건 · 기록 대비 성공률 {100 * n_s / max(len(att), 1):.1f}%",
         f"- 단계별 기록: {att['stage'].value_counts().to_dict()}", ""]
    end = ev[(ev["kind"].isin(["stage_end", "anomaly_stop"]))].tail(6)
    L += ["최근 단계 종료/중단 이벤트:", "", md(end[["at", "stage", "kind", "detail"]]), ""]

    # ---------------- 1. near duplicates
    nd = near_duplicate_groups(bodies, pc["near_duplicate"])
    nd = nd.merge(succ[["article_id", "domain_key", "date", "title"]], on="article_id", how="left")
    nd.to_csv(dest / "near_duplicate_groups.csv", index=False, encoding="utf-8-sig")
    gsz = nd.drop_duplicates("near_dup_group_id")["group_size"] if len(nd) else pd.Series(dtype=int)
    cross = nd.groupby("near_dup_group_id")["domain_key"].nunique() if len(nd) else pd.Series(dtype=int)
    L += ["## 1. 근사 중복 (MinHash, 삭제 없이 그룹 ID만 부여)", "",
          f"- 기준: 단어 {pc['near_duplicate']['shingle_words']}-gram, num_perm {pc['near_duplicate']['num_perm']}, "
          f"추정 Jaccard ≥ {pc['near_duplicate']['jaccard_threshold']}, 후보 쌍의 연결 성분을 한 그룹으로",
          f"- 그룹 {len(gsz):,}개, 그룹에 속한 기사 {len(nd):,}건 ({100 * len(nd) / max(n_s, 1):.1f}%). "
          f"그룹마다 1건만 남긴다고 하면 {len(nd) - len(gsz):,}건 감소",
          f"- 여러 도메인에 걸친 그룹(통신사 재게재 등): {int((cross > 1).sum()):,}개 / 같은 도메인 안 그룹: {int((cross == 1).sum()):,}개", "",
          md(gsz.value_counts().sort_index().rename_axis("group_size").reset_index(name="groups").head(15)), "",
          "가장 큰 그룹 5개:", ""]
    for g in nd["near_dup_group_id"].drop_duplicates().head(5):
        m = nd[nd["near_dup_group_id"] == g]
        L.append(f"- {g} ({len(m)}건, 도메인 {m['domain_key'].nunique()}개): {m['title'].iloc[0]!r} — "
                 + ", ".join(m["domain_key"].value_counts().head(6).index))
    L += ["", "전체 목록: `postprocess/near_duplicate_groups.csv`", ""]

    # ---------------- 2. distributions
    _, weeks = build_frame(cfg)
    ws = succ.groupby("week_id").size()
    wk = pd.DataFrame([{"week_id": w.week_id, "stratum": w.stratum, "start": w.days[0], "end": w.days[-1],
                        "quota": w.quota, "successes": int(ws.get(w.week_id, 0))} for w in weeks])
    wk["diff"] = wk["successes"] - wk["quota"]
    wk["fulfilled_pct"] = (100 * wk["successes"] / wk["quota"]).round(1)
    tried = set(att["article_id"])
    pop["tried"] = pop["article_id"].isin(tried)
    wk_of = {d: w.week_id for w in weeks for d in w.days}
    pop["week_id"] = pop["date"].map(lambda d: wk_of.get(d.isoformat()))
    wk["untried_candidates"] = wk["week_id"].map(pop[~pop["tried"]].groupby("week_id").size()).fillna(0).astype(int)
    wk.to_csv(dest / "week_fulfilment.csv", index=False, encoding="utf-8-sig")
    st = wk.groupby("stratum")[["quota", "successes"]].sum().reset_index()
    st["fulfilled_pct"] = (100 * st["successes"] / st["quota"]).round(2)
    mon = pd.DataFrame({"sample": succ["month"].value_counts(normalize=True).mul(100).round(2),
                        "population": pop["month"].value_counts(normalize=True).mul(100).round(2)}).sort_index()
    gap = pd.DataFrame({"sample_pct": succ["collection_gap_type"].value_counts(normalize=True).mul(100).round(2),
                        "population_pct": pop["collection_gap_type"].value_counts(normalize=True).mul(100).round(2)})
    dom = succ.groupby("domain_key").agg(successes=("article_id", "size")).sort_values("successes", ascending=False)
    dom["share_pct"] = 100 * dom["successes"] / max(n_s, 1)
    dom["cum_share_pct"] = dom["share_pct"].cumsum()
    short_days = []
    dcount = succ.groupby("date").size()
    for w in weeks:
        f = cfg["sampling"]["strata"][0]["daily_floor"] if w.stratum == "pre_war" else cfg["sampling"]["strata"][1]["daily_floor"]
        for d in w.days:
            if int(dcount.get(d, 0)) < w.floors[d]:
                short_days.append({"date": d, "week_id": w.week_id, "floor": w.floors[d], "successes": int(dcount.get(d, 0))})
    L += ["## 2. 최종 분포", "", "### 층별", "", md(st), "",
          "### 주별 (할당 대비, 남은 미시도 후보)", "", md(wk), "",
          "### 월별 비중 (표본 vs 사전 제외 후 모집단, %)", "", md(mon.reset_index(names="month")), "",
          "### 수집 공백 유형별 비중 (%)", "", md(gap.reset_index(names="collection_gap_type")), "",
          f"### 도메인: {len(dom):,}개, 상위 10개 점유 {dom['share_pct'].head(10).sum():.1f}%, 상위 30개 점유 "
          f"{dom['share_pct'].head(30).sum():.1f}%, 상한({cap}건) 도달 {int((dom['successes'] >= cap).sum())}개", "",
          f"### 하루 하한 미달 날짜: {len(short_days)}일", "", md(pd.DataFrame(short_days).head(40)), ""]

    # ---------------- 3. failures and auto-skipped domains
    outc = att["failure_reason"].where(att["extract_status"] != "success", "success")
    fr = outc.fillna("(none)").value_counts().rename_axis("outcome").reset_index(name="count")
    fr["share_pct"] = (100 * fr["count"] / fr["count"].sum()).round(2)
    sk = ev[ev["kind"] == "domain_skipped_after_failures"][["detail", "stage", "at"]].rename(columns={"detail": "domain_key"})
    popdom = pop.groupby("domain_key").size()
    sk["population_articles"] = sk["domain_key"].map(popdom).fillna(0).astype(int)
    sk["population_share_pct"] = (100 * sk["population_articles"] / len(pop)).round(3)
    L += ["## 3. 실패 사유와 자동 건너뛴 도메인", "", md(fr), "",
          f"자동 건너뛴 도메인 {len(sk)}개, 원 모집단 합계 {int(sk['population_articles'].sum()):,}건 "
          f"({100 * sk['population_articles'].sum() / len(pop):.2f}%)", "",
          md(sk.sort_values("population_articles", ascending=False)), ""]

    # ---------------- 4. suspicious domain review
    top = dom.head(pc["top_domains_n"]).copy()
    agg = succ.groupby("domain_key").agg(
        median_words=("body_word_count", "median"), title_has_hormuz_pct=("title_has_hormuz", lambda s: 100 * s.fillna(0).astype(float).mean()),
        hormuz_once_pct=("hormuz_mention_count", lambda s: 100 * (s.astype(float) == 1).mean()),
        nc_country=("nc_country", mode_str))
    ndc = nd.groupby("domain_key").size()
    top = top.join(agg)
    top["near_dup_articles"] = top.index.map(ndc).fillna(0).astype(int)
    dom.join(agg).to_csv(dest / "domain_summary.csv", encoding="utf-8-sig")
    L += ["## 4. 의심 도메인 검토", "", f"### 성공 상위 {pc['top_domains_n']}개", "",
          md(top.reset_index().round(1)), ""]
    for d in pc["review_domains"]:
        m = succ[succ["domain_key"] == d]
        L += [f"### {d} — 성공 {len(m)}건", ""]
        for r in m.sample(min(pc["review_examples_per_domain"], len(m)), random_state=2026).itertuples():
            L += [f"- **{r.title}** ({r.date}, {int(r.body_word_count)}단어, hormuz {r.hormuz_mention_count}회)",
                  f"  - {r.url}", f"  - 본문 앞 300자: {(bodies.get(r.article_id, '') or '')[:300]!r}"]
        L.append("")

    # ---------------- 5. partial_body >= N words, over_10000_words
    pb = att[(att["failure_reason"] == "partial_body") & (att["body_word_count"].astype(float) >= pc["partial_body_min_words"])]
    pb = pb.join(ci[["nc_word_count"]], on="article_id")
    allb = pd.read_sql("SELECT * FROM bodies", con).set_index("article_id")["body"]
    L += [f"## 5. partial_body 중 본문 {pc['partial_body_min_words']}단어 이상: {len(pb):,}건 (예시 {pc['partial_body_examples']}건)", ""]
    for r in pb.sample(min(pc["partial_body_examples"], len(pb)), random_state=2026).itertuples():
        L.append(f"- {r.domain_key} | 본문 {int(r.body_word_count)}단어 | nc_word_count {r.nc_word_count} | 비율 {r.words_ratio_to_nc} | "
                 f"{(allb.get(r.article_id, '') or '')[:200]!r}")
    ov = succ[succ["over_10000_words"].fillna(0).astype(int) == 1]
    L += ["", f"## 6. over_10000_words 성공 건 전체: {len(ov)}건", "",
          md(ov[["domain_key", "date", "body_word_count", "title", "url"]].sort_values("body_word_count", ascending=False)), ""]

    # ---------------- 6. refill method
    L += ["## 7. 도메인 제외·기준 변경으로 50,000건 아래로 내려갈 때의 보충 방법", "",
          "현재 수집기 구조로 추가 코드 없이 같은 주에서 보충된다.", "",
          "1. 제외할 도메인을 config `excluded_domains`에 추가하거나 성공 기준을 바꾼다(결정 후 CLAUDE.md 갱신).",
          "2. `python collection/rejudge.py` — DB를 백업한 뒤, 제외 도메인의 기존 성공은 `pre_excluded_domain`, "
          "기준 변경은 저장 HTML로 다시 판정한다(요청 없음, 변경 내역은 `rejudge_log`).",
          "3. `python collection/plan.py` — 제외 후 모집단으로 주별 할당을 다시 계산한다(층 목표 2,500/47,500 유지).",
          "4. `python collection/collect.py --stage main` — 실행기는 재판정 후 성공 수로 주별 충족을 다시 세고, 할당에 못 미친 주만 "
          "그 주의 아직 시도하지 않은 후보(같은 시드 순서)로 채운다. 그 주 후보가 바닥나면 같은 층의 가장 가까운 주(이전 주부터)로 넘긴다. "
          "제외 도메인은 후보에서 빠지므로 다시 뽑히지 않는다.",
          "5. 근사 중복 제거로 줄어드는 건은 성공 상태를 바꾸지 않으므로(그룹 ID만 부여) 보충 대상이 되지 않는다.", "",
          "주별 남은 미시도 후보 수는 2절 주별 표의 `untried_candidates` 열 참고(보충 여력).", ""]

    # ---------------- 7. country verification prep
    dc = succ.groupby("domain_key").agg(
        successes=("article_id", "size"), nc_country=("nc_country", mode_str),
        nc_country_values=("nc_country", lambda s: ";".join(sorted(set(s.replace("", "(blank)"))))[:80]),
        nc_source_name=("nc_source_name", mode_str), domain=("domain", "first"),
    ).sort_values("successes", ascending=False)
    dc["cc_tld"] = dc.index.map(cc_tld)
    dc["cum_share_pct"] = (100 * dc["successes"].cumsum() / max(n_s, 1)).round(2)
    dc["verified_country"] = ""  # to be filled in the verification step
    dc["verification_source"] = ""
    dc.to_csv(dest / "country_verification_domains.csv", encoding="utf-8-sig")
    reach = {f"{int(t * 100)}%": int((dc["cum_share_pct"] < t * 100).sum() + 1) for t in pc["country_cum_shares"]}
    cn = succ["nc_country"].replace("", "(blank)").value_counts().rename_axis("nc_country").reset_index(name="articles")
    cn["share_pct"] = (100 * cn["articles"] / max(n_s, 1)).round(2)
    cn["domains"] = cn["nc_country"].map(succ.assign(c=succ["nc_country"].replace("", "(blank)"))
                                         .groupby("c")["domain_key"].nunique())
    cn.to_csv(dest / "country_distribution_nc.csv", index=False, encoding="utf-8-sig")
    few = cn[cn["articles"] < pc["few_articles_country_threshold"]]
    tld_agree = dc[(dc["cc_tld"] != "") & (dc["nc_country"] != "")]
    agree = (tld_agree["cc_tld"].str.upper() == tld_agree["nc_country"].str.upper().str[:2])
    L += ["## 8. 국가 검증 준비 (언론사 소재국 기준)", "",
          f"- 도메인 {len(dc):,}개. 표본의 80%를 차지하는 상위 도메인 **{reach.get('80%')}개**, 90%는 **{reach.get('90%')}개**",
          f"- 검증 목록: `postprocess/country_verification_domains.csv` (domain_key, successes, cum_share_pct, nc_country, "
          "nc_country_values, nc_source_name, cc_tld, 빈 칸 verified_country / verification_source)",
          f"- 참고: ccTLD가 있고 nc_country도 있는 도메인 {len(tld_agree):,}개 중 두 값이 같은 도메인 {int(agree.sum()):,}개 "
          f"({100 * agree.mean() if len(agree) else 0:.1f}%). ccTLD가 없는(.com 등) 도메인은 {int((dc['cc_tld'] == '').sum()):,}개",
          "", "상위 30개 도메인:", "",
          md(dc.head(30).reset_index()[["domain_key", "successes", "cum_share_pct", "nc_country", "nc_country_values",
                                         "cc_tld", "nc_source_name"]]), "",
          f"### nc_country 기준 국가별 기사 수 (참고용, {len(cn)}개 값)", "", md(cn.head(40)), "",
          f"### 기사 {pc['few_articles_country_threshold']}건 미만 국가: {len(few)}개", "",
          ", ".join(f"{r.nc_country}({r.articles})" for r in few.itertuples()), ""]

    rep = "\n".join(L)
    (dest / "POSTPROCESS_REPORT.md").write_text(rep, encoding="utf-8")
    print(f"wrote {dest / 'POSTPROCESS_REPORT.md'} ({len(rep):,} chars)")


if __name__ == "__main__":
    main()
