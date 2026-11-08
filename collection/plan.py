"""Step 1: planning report (no network access).

Outputs (in <config.output_dir>/plan/):
  plan_summary.json, week_allocation.csv, daily_allocation.csv,
  excluded_by_domain.csv, domain_expected.csv, rate_limit_domains.csv, PLAN_REPORT.md

Usage (from project root):  python collection/plan.py [--config path]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from allocation import allocate, headroom, simulate_cross_block, week_blocks
from common import load_candidates, load_config, project_path

PLAN_COLUMNS = ["article_id", "url", "domain", "published_date", "nc_paid_content", "nc_country"]
EXAMPLE_DATES = ["2026-02-14", "2026-02-15", "2026-02-16", "2026-05-02", "2026-05-03",
                 "2026-05-07", "2026-05-08", "2026-08-31"]


def md_table(df: pd.DataFrame, floatfmt: str = "{:,.2f}") -> str:
    def fmt(v):
        if isinstance(v, (float, np.floating)):
            return "inf" if np.isinf(v) else floatfmt.format(v)
        if isinstance(v, (int, np.integer)):
            return f"{v:,}"
        return str(v)
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    rows = ["| " + " | ".join(fmt(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join([head, sep, *rows])


def pct(p: float) -> str:
    return f"p{int(round(p * 100))}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    sp = cfg["sampling"]
    if sp["allocation_unit"] != "week_block":
        raise ValueError(f"unsupported allocation_unit {sp['allocation_unit']}")
    block_days = int(sp["block_days"])
    prev_first = bool(sp["nearest_block_previous_first"])
    scen_p = cfg["plan_report"]["success_rate_scenarios"]
    out = project_path(cfg["output_dir"]) / "plan"
    out.mkdir(parents=True, exist_ok=True)

    df = load_candidates(cfg, PLAN_COLUMNS)
    n_total = len(df)
    ts = pd.to_datetime(df[cfg["date_column"]], errors="coerce")
    df["is_midnight"] = (ts.dt.hour == 0) & (ts.dt.minute == 0) & (ts.dt.second == 0)
    period_lo = pd.Timestamp(cfg["research_period"]["start"]).date()
    period_hi = pd.Timestamp(cfg["research_period"]["end"]).date()
    in_period = (~df["date_parse_failed"]) & (df["date"] >= period_lo) & (df["date"] <= period_hi)

    # ---- exclusions (only the configured domain list) ----
    excl = df["excluded_by"] != ""
    excl_tbl = (df[excl].groupby(["excluded_by", "domain"]).size()
                .rename("rows").reset_index().sort_values(["excluded_by", "rows"], ascending=[True, False]))
    excl_tbl.to_csv(out / "excluded_by_domain.csv", index=False, encoding="utf-8-sig")
    excl_by_pattern = df[excl].groupby("excluded_by").size().reindex(cfg["excluded_domains"], fill_value=0)

    pop = df[~excl].copy()
    unassigned = pop[pop["stratum"] == ""]
    pop_daily = pop.groupby("date").size()
    mid_daily = pop.groupby("date")["is_midnight"].mean()

    # ---- weekly allocation per stratum ----
    week_rows, day_rows, strata_info, scen_rows = [], [], [], []
    for s in sp["strata"]:
        f = int(s["daily_floor"])
        blocks = week_blocks(s["start"], s["end"], block_days)
        n_w = np.array([sum(int(pop_daily.get(d, 0)) for d in b) for b in blocks])
        floor_w = np.array([sum(min(f, int(pop_daily.get(d, 0))) for d in b) for b in blocks])
        quota, info = allocate(n_w, s["target"], floor_w, n_w)

        # scenarios: day floors short -> same block; block short -> nearest blocks
        moved = {}
        for p in scen_p:
            cap_w = np.floor(p * n_w)
            same_week = sum(max(0, min(f, int(pop_daily.get(d, 0))) - int(np.floor(p * pop_daily.get(d, 0))))
                            for b in blocks for d in b)
            deficit, received, unresolved = simulate_cross_block(quota, cap_w, prev_first)
            moved[p] = (deficit, received)
            scen_rows.append({"assumed_success_rate": p, "stratum": s["name"],
                              "daily_floor_short_filled_in_same_week": int(same_week),
                              "weeks_short": int((deficit > 0).sum()),
                              "moved_to_other_weeks": int(round(deficit.sum())),
                              "moved_share_of_target_pct": round(100 * deficit.sum() / s["target"], 2),
                              "unresolved_in_stratum": unresolved})

        for wi, b in enumerate(blocks):
            rem = int(quota[wi] - floor_w[wi])
            free = int(n_w[wi] - floor_w[wi])
            row = {"stratum": s["name"], "week_id": f"{s['name']}_w{wi + 1:02d}",
                   "week_start": b[0].isoformat(), "week_end": b[-1].isoformat(), "days": len(b),
                   "candidates": int(n_w[wi]), "quota": int(quota[wi]),
                   "floor_secured": int(floor_w[wi]), "remainder_by_shuffle": rem,
                   "headroom_candidates_per_quota": round(headroom(n_w[wi], quota[wi]), 2)}
            for p in scen_p:
                row[f"moved_out_{pct(p)}"] = int(round(moved[p][0][wi]))
                row[f"received_{pct(p)}"] = int(round(moved[p][1][wi]))
            week_rows.append(row)
            for d in b:
                c = int(pop_daily.get(d, 0))
                fd = min(f, c)
                exp = fd + (rem * (c - fd) / free if free > 0 else 0.0)
                day_rows.append({"date": d.isoformat(), "stratum": s["name"], "week_id": row["week_id"],
                                 "candidates": c, "share_published_00_00_00": round(float(mid_daily.get(d, np.nan)), 3),
                                 "daily_floor_secured": fd, "expected_successes_plan": round(exp, 2)})
        info.update(name=s["name"], start=s["start"], end=s["end"], weeks=len(blocks),
                    last_week_days=len(blocks[-1]), daily_floor=f, quota_sum=int(quota.sum()))
        strata_info.append(info)

    weeks = pd.DataFrame(week_rows)
    daily = pd.DataFrame(day_rows)
    scen = pd.DataFrame(scen_rows)
    weeks.to_csv(out / "week_allocation.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(out / "daily_allocation.csv", index=False, encoding="utf-8-sig")

    # ---- expected successes per domain under uniform success (planning estimate only) ----
    cap = int(round(sp["domain_cap_share"] * sp["target_success_total"]))
    warn = cfg["plan_report"]["domain_cap_warn_fraction"] * cap
    di = daily.set_index("date")
    rate = di["expected_successes_plan"] / di["candidates"].where(lambda x: x > 0)
    pop["sample_rate"] = pop["date"].map(lambda d: d.isoformat()).map(rate).fillna(0.0)
    dom = (pop.groupby("domain_key")
           .agg(candidates=("article_id", "size"), expected_successes=("sample_rate", "sum"),
                rate_limit_key=("rate_limit_key", "first"),
                raw_domain_values=("domain", lambda x: ";".join(sorted(set(x)))[:200]))
           .reset_index().sort_values("expected_successes", ascending=False))
    dom["candidate_share_pct"] = 100 * dom["candidates"] / len(pop)
    dom["expected_share_pct"] = 100 * dom["expected_successes"] / sp["target_success_total"]
    dom["status"] = "below"
    dom.loc[dom["expected_successes"] >= warn, "status"] = "near_cap"
    dom.loc[dom["expected_successes"] >= cap, "status"] = "over_cap"
    dom.to_csv(out / "domain_expected.csv", index=False, encoding="utf-8-sig")
    over, near = dom[dom["status"] == "over_cap"], dom[dom["status"] == "near_cap"]
    excess = float((over["expected_successes"] - cap).sum())

    # ---- rate-limit units (registrable domain) ----
    interval = cfg["fetch"]["per_domain_min_interval_sec"]
    rl = (pop.groupby("rate_limit_key")
          .agg(candidates=("article_id", "size"), expected_successes=("sample_rate", "sum"),
               cap_units=("domain_key", "nunique"),
               cap_unit_values=("domain_key", lambda x: ";".join(sorted(set(x)))[:200]))
          .reset_index().sort_values("expected_successes", ascending=False))
    rl["min_hours_at_interval_if_all_succeed"] = rl["expected_successes"] * interval / 3600
    rl.to_csv(out / "rate_limit_domains.csv", index=False, encoding="utf-8-sig")

    # ---- collection-artifact evidence ----
    zero_days = daily[daily["candidates"] == 0]["date"].tolist()
    dd = daily.copy()
    dd["month"] = dd["date"].str[:7]
    c = dd["candidates"].replace(0, np.nan)
    dd["adjacent_day_ratio"] = np.maximum(c, c.shift(1)) / np.minimum(c, c.shift(1))
    med7 = dd["candidates"].rolling(7, center=True, min_periods=1).median()
    dd["low_day"] = dd["candidates"] < 0.3 * med7
    osc = (dd.groupby("month")
           .agg(median_adjacent_day_ratio=("adjacent_day_ratio", "median"),
                min_daily_candidates=("candidates", "min"), max_daily_candidates=("candidates", "max"))
           .reset_index())
    for col in ["min_daily_candidates", "max_daily_candidates"]:
        osc[col] = osc[col].astype(int)
    mid_cmp = (dd[dd["candidates"] > 0].groupby("low_day")
               .agg(days=("date", "size"), median_candidates=("candidates", "median"),
                    median_share_00_00_00=("share_published_00_00_00", "median"))
               .reset_index().replace({"low_day": {True: "low_day (<30% of 7-day median)", False: "other days"}}))
    mid_cmp["days"] = mid_cmp["days"].astype(int)

    # ---- summary ----
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "network_used": False,
        "input_csv": cfg["input_csv"], "input_rows": n_total,
        "date_parse_failed": int(df["date_parse_failed"].sum()),
        "outside_research_period": int((~in_period).sum()),
        "excluded_rows": int(excl.sum()),
        "excluded_by_pattern": {k: int(v) for k, v in excl_by_pattern.items()},
        "population_after_exclusion": len(pop),
        "population_not_in_any_stratum": len(unassigned),
        "distinct_domain_keys_cap_unit": int(pop["domain_key"].nunique()),
        "distinct_rate_limit_keys": int(pop["rate_limit_key"].nunique()),
        "allocation_unit": sp["allocation_unit"], "block_days": block_days,
        "domain_cap": cap,
        "domains_expected_over_cap": len(over), "domains_expected_near_cap": len(near),
        "expected_excess_over_cap_total": round(excess, 1),
        "strata": strata_info,
        "scenarios": scen_rows,
        "zero_candidate_days": zero_days,
        "nc_paid_content_recorded_only": pop["nc_paid_content"].replace("", "(blank)").value_counts().to_dict(),
    }
    (out / "plan_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---- markdown report ----
    strata_tbl = pd.DataFrame([{
        "stratum": s["name"], "period": f"{s['start']} ~ {s['end']}", "weeks": s["weeks"],
        "last_week_days": s["last_week_days"], "candidates": s["candidates"], "target": s["target"],
        "quota_sum": s["quota_sum"], "daily_floor": s["daily_floor"], "floor_secured_total": s["floor_total"],
        "candidates_per_target": round(s["candidates"] / s["target"], 2), "shortfall_at_plan": s["shortfall"],
    } for s in strata_info])
    week_view = weeks[["week_id", "week_start", "week_end", "days", "candidates", "quota", "floor_secured",
                       "remainder_by_shuffle", "headroom_candidates_per_quota"]
                      + [f"moved_out_{pct(p)}" for p in scen_p]]
    ex = daily[daily["date"].isin(EXAMPLE_DATES)][["date", "week_id", "candidates", "share_published_00_00_00",
                                                    "daily_floor_secured", "expected_successes_plan"]]
    month_tbl = (dd.groupby(["month"]).agg(candidates=("candidates", "sum"),
                                           expected=("expected_successes_plan", "sum")).reset_index())
    month_tbl["candidate_share_pct"] = 100 * month_tbl["candidates"] / month_tbl["candidates"].sum()
    month_tbl["expected_share_pct"] = 100 * month_tbl["expected"] / month_tbl["expected"].sum()
    month_tbl["candidates"] = month_tbl["candidates"].astype(int)

    def dom_view(d):
        v = d.head(60)[["domain_key", "candidates", "candidate_share_pct", "expected_successes",
                        "expected_share_pct", "raw_domain_values"]].copy()
        v["candidates"] = v["candidates"].astype(int)
        return v

    rl_view = rl.head(10)[["rate_limit_key", "cap_units", "candidates", "expected_successes"]].copy()
    for col in ["cap_units", "candidates"]:
        rl_view[col] = rl_view[col].astype(int)
    excl_pat_tbl = pd.DataFrame({"pattern": excl_by_pattern.index, "rows": excl_by_pattern.astype(int).to_numpy()})
    pr = "이전 주" if prev_first else "다음 주"

    lines = [
        "# body_collection_v3 계획 리포트 (1단계, 네트워크 미사용)", "",
        f"- 생성 시각(UTC): {summary['generated_at']}",
        f"- 입력: `{cfg['input_csv']}` — {n_total:,}행",
        f"- 날짜 파싱 실패: {summary['date_parse_failed']:,}건 / 연구 기간 밖: {summary['outside_research_period']:,}건",
        "- 날짜 기준: `published_date`(시간대 표기 없음)를 UTC로 간주해 날짜를 잘랐다.", "",
        "## 1. 할당 방식과 근거", "",
        f"**일별 수집량이 수집 과정의 영향을 받아 주 단위로 층화함.** 각 층을 층 시작일부터 {block_days}일 블록(주)으로 나누고 "
        "층 끝에서 마지막 블록을 자른다. 층 목표를 주별 후보 수에 비례해 배분하고(이동평균 미사용), 주 안에서는 "
        "하루 하한(그날 후보가 더 적으면 전부)을 먼저 확보한 뒤 나머지를 그 주 전체 후보를 고정 시드로 섞은 순서로 채운다. "
        f"실행 중 부족분은 같은 주의 다른 날 → 같은 층의 가장 가까운 주({pr}부터 앞뒤 번갈아) 순으로 넘긴다.", "",
        "### 근거: 일별 수집량의 출렁임 (수집 과정에서 생긴 현상으로 추정)", "",
        f"- 후보 0건인 날: {', '.join(zero_days) if zero_days else '없음'} — 수집 과정에서 생긴 현상으로 추정.",
        "- 후보 수가 하루걸러 크게 오르내림(위상은 월 중간에 바뀌기도 함). `median_adjacent_day_ratio` = 인접한 두 날 "
        "후보 수의 큰 값/작은 값의 월별 중앙값.", "",
        md_table(osc), "",
        "- 후보가 적은 날은 발행 시각이 정확히 00:00:00인 기사 비중이 매우 높다. git 기록의 원 수집 코드"
        "(`bb85a6c:hormuz_2026_news_textmining_filtered.ipynb`)는 `from_`/`to_`에 시각 없는 `YYYY-MM-DD`를 넘기고, "
        "결과가 9,000건을 넘으면 날짜 구간을 중간점에서 재귀 분할했다. API가 `to_` 날짜를 그날 00:00:00으로 해석했다면 "
        "각 분할 구간의 마지막 날은 자정 기사만 들어가게 되어 이 패턴과 맞는다(추정, API 동작은 미검증).", "",
        md_table(mid_cmp), "",
        "## 2. 사전 제외", "",
        f"제외 {int(excl.sum()):,}건 → 모집단 **{len(pop):,}건**. 도메인 상한 단위(호스트에서 www.만 제거) "
        f"{summary['distinct_domain_keys_cap_unit']:,}개, 요청 간격 단위(등록 도메인) {summary['distinct_rate_limit_keys']:,}개.", "",
        md_table(excl_pat_tbl), "",
        "## 3. 층별 요약", "", md_table(strata_tbl), "",
        "## 4. 주별 할당", "",
        "`floor_secured` = 하루 하한으로 확보하는 건수의 주 합계, `remainder_by_shuffle` = 주 전체 셔플 순서로 채우는 나머지. "
        "`moved_out_pNN` = 성공률 NN% 가정 시 그 주 후보로 채우지 못해 다른 주로 넘어가는 건수.", "",
        md_table(week_view), "",
        "## 5. 성공률 가정별 재배분 규모 (계획용 추정)", "",
        "모든 시도가 성공률 p로 성공한다고 가정. `daily_floor_short_filled_in_same_week` = 그날 후보로 하한을 못 채워 "
        "같은 주 다른 날이 메우는 건수, `moved_to_other_weeks` = 주 전체로도 못 채워 가까운 주로 넘어가는 건수, "
        "`unresolved_in_stratum` = 층 안 어느 주로도 못 메우는 건수.", "",
        md_table(scen), "",
        "## 6. 예시 날짜 (계획상 기대 성공 건수)", "",
        "`expected_successes_plan` = 하루 하한 + 주 나머지 × (그날 하한 외 후보 / 주 하한 외 후보). 모든 시도가 성공한다고 볼 때의 기대값. "
        "전체 표는 `plan/daily_allocation.csv`.", "",
        md_table(ex), "",
        "### 월별 후보 비중 vs 기대 성공 비중", "", md_table(month_tbl), "",
        f"## 7. 도메인 상한({cap}건, 호스트에서 www.만 제거한 단위) 예상", "",
        "가정: 모든 도메인의 성공률이 같다고 볼 때의 기대 성공 건수. 실제로는 도메인별 실패율에 따라 달라진다.", "",
        f"- 상한 초과 예상: **{len(over)}개** (초과분 합계 약 {excess:,.0f}건), 상한의 "
        f"{int(cfg['plan_report']['domain_cap_warn_fraction'] * 100)}% 이상 근접: {len(near)}개", "",
        md_table(dom_view(pd.concat([over, near]))) if len(over) + len(near) else "(없음)", "",
        f"## 8. 요청 간격 단위(등록 도메인, {interval}초) 상위 10개", "",
        "전체 목록은 `plan/rate_limit_domains.csv`.", "", md_table(rl_view), "",
        "## 9. 기록만 하는 값 (필터 미사용)", "",
        f"- nc_paid_content: {summary['nc_paid_content_recorded_only']}", "",
    ]
    (out / "PLAN_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ["population_after_exclusion", "domains_expected_over_cap",
                                              "scenarios"]}, indent=1, default=str))
    print("wrote", out)


if __name__ == "__main__":
    main()
