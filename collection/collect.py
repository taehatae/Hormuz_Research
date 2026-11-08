"""Body collection runner.

  python collection/collect.py                 -> status only, NO network requests
  python collection/collect.py --stage smoke   -> up to runs.stages.smoke records (100)
  python collection/collect.py --stage pilot   -> up to runs.stages.pilot records (2,000)
  python collection/collect.py --stage main    -> until all week targets are met

Selection (sampling.py): weekly quotas proportional to weekly candidates; inside a
week the daily floors are served first, then the week's seeded shuffle order.
Weeks are served round-robin in allocation order, so any attempt budget is spread
over all weeks and both strata. A week that runs out of candidates hands its
deficit to the nearest week of the same stratum (previous first).
Every taken candidate is written to SQLite immediately; already-attempted
articles are skipped on restart.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import trafilatura

from common import load_config, project_path
from extract import extract_and_judge
from fetch import Fetcher
from sampling import build_frame, stratum_neighbours
from store import Store, now

EXTRACTOR_VERSION = f"trafilatura {trafilatura.__version__}"
JUDGE_VERSION = "criteria_v2"  # v2: 100 words, en, ratio>=0.5, sentence-level boilerplate, code_or_markup, page types


class Runner:
    def __init__(self, cfg: dict, stage: str, store: Store, fetcher: Fetcher | None = None, log=print):
        self.cfg, self.stage, self.store, self.log = cfg, stage, store, log
        self.rc, self.fc, self.ec = cfg["runs"], cfg["fetch"], cfg["extract"]
        out = project_path(cfg["output_dir"])
        self.fetcher = fetcher or Fetcher(cfg, out / self.fc["html_dir"])
        self.pop, self.weeks = build_frame(cfg)
        self.cap = int(round(cfg["sampling"]["domain_cap_share"] * cfg["sampling"]["target_success_total"]))
        self.budget = self.rc["stages"][stage]
        self.block_reasons = set(self.fc["domain_skip_failure_reasons"])
        self.prev_first = bool(cfg["sampling"]["nearest_block_previous_first"])
        self._restore()

    # ---------- state ----------
    def _restore(self) -> None:
        rows = sorted(self.store.attempts(), key=lambda r: r["attempted_at"] or "")
        self.tried = {r["article_id"] for r in rows}
        self.stage_count = sum(r["stage"] == self.stage for r in rows)
        self.succ_day, self.succ_week, self.succ_dom = Counter(), Counter(), Counter()
        self.success_hashes = set()
        self.consec, self.skipped_domains = Counter(), set()
        for r in rows:
            if r["extract_status"] == "success":
                self.succ_day[r["date"]] += 1
                self.succ_week[r["week_id"]] += 1
                self.succ_dom[r["domain_key"]] += 1
                self.success_hashes.add(r["body_sha1"])
            self._update_consec(r["domain_key"], r["failure_reason"], record_event=False)
        self.inflight_day, self.inflight_week, self.inflight_dom = Counter(), Counter(), Counter()
        self.inflight_rl = set()
        self.pos = {w.week_id: 0 for w in self.weeks}
        self.week_of_date = {d: w for w in self.weeks for d in w.days}
        self.stage_stats = Counter()

    def _update_consec(self, dom: str, reason: str | None, record_event: bool = True) -> None:
        if reason in ("domain_skipped_after_failures", "domain_cap", "robots_disallow", "pre_excluded_domain"):
            return
        if reason in self.block_reasons:
            self.consec[dom] += 1
            if self.consec[dom] >= self.fc["domain_consecutive_block_limit"] and dom not in self.skipped_domains:
                self.skipped_domains.add(dom)
                if record_event:
                    self.store.event(self.stage, "domain_skipped_after_failures", dom)
        else:
            self.consec[dom] = 0

    # ---------- selection ----------
    def _untried(self, w):
        i = self.pos[w.week_id]
        while i < len(w.order) and self.pop.at[w.order[i], "article_id"] in self.tried:
            i += 1
        self.pos[w.week_id] = i
        for j in range(i, len(w.order)):
            row = w.order[j]
            if self.pop.at[row, "article_id"] not in self.tried:
                yield row

    def _skip_record(self, row: int, reason: str) -> None:
        rec = self._base_record(row)
        rec.update(fetch_status="skipped", extract_status="not_attempted", failure_reason=reason, request_sent=0)
        self._finish(row, rec, None, inflight=False)

    def _pick(self, w):
        """Next candidate row of week w, or None. May write skip records for capped/skipped domains."""
        if self.succ_week[w.week_id] + self.inflight_week[w.week_id] >= w.target:
            return None
        need = {d for d in w.days if self.succ_day[d] + self.inflight_day[d] < w.floors[d]}
        now_t = time.monotonic()
        fallback, scanned = None, 0
        for row in self._untried(w):
            scanned += 1
            if scanned > self.rc["dispatch_scan_limit"]:
                break
            dom = self.pop.at[row, "domain_key"]
            if dom in self.skipped_domains:
                self._skip_record(row, "domain_skipped_after_failures")
                continue
            if self.succ_dom[dom] >= self.cap:
                self._skip_record(row, "domain_cap")
                continue
            if self.succ_dom[dom] + self.inflight_dom[dom] >= self.cap:
                continue
            rl = self.pop.at[row, "rate_limit_key"]
            if rl in self.inflight_rl or self.fetcher.limiter.ready_at(rl) > now_t:
                continue
            if self.pop.at[row, "date_iso"] in need:
                return row
            if fallback is None:
                fallback = row
                if not need:
                    return row
        return fallback

    def _exhausted(self, w) -> bool:
        return next(self._untried(w), None) is None and self.inflight_week[w.week_id] == 0

    def _transfer_deficits(self) -> None:
        for w in self.weeks:
            if self._exhausted(w) and self.succ_week[w.week_id] < w.target:
                deficit = w.target - self.succ_week[w.week_id]
                w.target = self.succ_week[w.week_id]
                for nb in stratum_neighbours(self.weeks, w, self.prev_first):
                    if not self._exhausted(nb):
                        nb.target += deficit
                        self.store.event(self.stage, "week_transfer", f"{w.week_id}->{nb.week_id}:{deficit}")
                        break
                else:
                    self.store.event(self.stage, "stratum_shortfall", f"{w.week_id}:{deficit}")

    # ---------- work ----------
    def _base_record(self, row: int) -> dict:
        p = self.pop.loc[row]
        return {"article_id": p["article_id"], "url": p["url"], "stratum": p["stratum"], "date": p["date_iso"],
                "week_id": self.week_of_date[p["date_iso"]].week_id, "domain": p["domain"],
                "domain_key": p["domain_key"], "rate_limit_key": p["rate_limit_key"], "attempted_at": now(),
                "stage": self.stage, "extractor_version": EXTRACTOR_VERSION}

    def _work(self, row: int):
        p = self.pop.loc[row]
        rec = self._base_record(row)
        note, parser = self.fetcher.robots(p["url"], p["rate_limit_key"], self.store)
        rec["robots_note"] = note
        if not self.fetcher.robots_allows(parser, p["url"]):
            rec.update(fetch_status="robots_disallow", extract_status="not_attempted",
                       failure_reason="robots_disallow", request_sent=0)
            return rec, None
        f = self.fetcher.fetch(p["url"], p["rate_limit_key"])
        rec.update(http_status=f["http_status"], final_url=f["final_url"], fetch_status=f["fetch_status"],
                   failure_reason=f["failure_reason"], request_sent=1, fetch_attempts=f["attempts"],
                   elapsed_sec=f["elapsed_sec"], content_type=f["content_type"])
        if f["fetch_status"] != "ok":
            rec["extract_status"] = "not_attempted"
            return rec, None
        rec["html_path"] = self.fetcher.save_html(p["article_id"], f["content"])
        ex = extract_and_judge(f["content"], p["url"], f["final_url"], p["title"], p["nc_content_snippet"],
                               p["nc_word_count"], self.ec, set())
        body = ex.pop("body")
        rec.update(ex)
        rec["judged_with"] = JUDGE_VERSION
        rec["attempted_at"] = now()
        return rec, body

    def _finish(self, row: int, rec: dict, body: str | None, inflight: bool = True) -> None:
        d, wk, dom = rec["date"], rec["week_id"], rec["domain_key"]
        if inflight:
            self.inflight_day[d] -= 1
            self.inflight_week[wk] -= 1
            self.inflight_dom[dom] -= 1
            self.inflight_rl.discard(rec["rate_limit_key"])
        if rec.get("extract_status") == "success" and rec["body_sha1"] in self.success_hashes:
            rec.update(extract_status="failed", failure_reason="exact_duplicate")
        if rec.get("extract_status") == "success":
            self.success_hashes.add(rec["body_sha1"])
            self.succ_day[d] += 1
            self.succ_week[wk] += 1
            self.succ_dom[dom] += 1
        self._update_consec(dom, rec.get("failure_reason"))
        self.tried.add(rec["article_id"])
        self.store.add_attempt(rec, body)
        self.stage_count += 1
        self.stage_stats["records"] += 1
        self.stage_stats["requests_sent"] += int(rec.get("request_sent") or 0)
        self.stage_stats["fetch_ok"] += int(rec.get("fetch_status") == "ok")
        self.stage_stats["success"] += int(rec.get("extract_status") == "success")

    def _anomaly(self) -> str | None:
        s = self.stage_stats
        if s["records"] < self.rc["anomaly_check_after"]:
            return None
        if (s["records"] - s["requests_sent"]) / s["records"] > self.rc["anomaly_max_pre_request_skip_share"]:
            return f"most records skipped before any request ({s['records'] - s['requests_sent']}/{s['records']})"
        if s["fetch_ok"] < self.rc["anomaly_min_fetch_ok"]:
            return f"no successful fetch in {s['records']} records"
        return None

    def run(self) -> dict:
        t0 = time.monotonic()
        self.store.event(self.stage, "stage_start", json.dumps({"budget": self.budget, "already": self.stage_count}))
        budget_left = lambda: self.budget is None or self.stage_count + sum(self.inflight_week.values()) < self.budget
        futures = {}
        rr = 0
        stop_reason = None
        with ThreadPoolExecutor(max_workers=self.fc["max_workers"]) as pool:
            while True:
                dispatched = False
                tries = 0
                while len(futures) < self.fc["max_workers"] and budget_left() and tries < len(self.weeks):
                    w = self.weeks[rr % len(self.weeks)]
                    rr += 1
                    tries += 1
                    row = self._pick(w)
                    if not budget_left():
                        break
                    if row is None:
                        continue
                    p = self.pop.loc[row]
                    self.inflight_day[p["date_iso"]] += 1
                    self.inflight_week[w.week_id] += 1
                    self.inflight_dom[p["domain_key"]] += 1
                    self.inflight_rl.add(p["rate_limit_key"])
                    self.tried.add(p["article_id"])  # reserve
                    futures[pool.submit(self._work, row)] = row
                    dispatched, tries = True, 0
                if futures:
                    done, _ = wait(list(futures), timeout=0.5, return_when=FIRST_COMPLETED)
                    for fut in done:
                        row = futures.pop(fut)
                        try:
                            rec, body = fut.result()
                        except Exception as e:  # never lose a taken candidate
                            rec, body = self._base_record(row), None
                            rec.update(fetch_status="error", extract_status="not_attempted",
                                       failure_reason=f"runner_error:{type(e).__name__}", request_sent=None)
                        self.tried.discard(rec["article_id"])
                        self._finish(row, rec, body)
                        if self.stage_count % self.rc["progress_log_every"] == 0:
                            self._progress()
                    stop_reason = stop_reason or self._anomaly()
                    if stop_reason:
                        self.store.event(self.stage, "anomaly_stop", stop_reason)
                        self.log(f"ANOMALY - stopping: {stop_reason}")
                        for f in futures:
                            f.cancel()
                        break
                    if not dispatched:
                        self._transfer_deficits()
                    continue
                if not budget_left():
                    stop_reason = stop_reason or "budget_reached"
                    break
                self._transfer_deficits()
                if not dispatched:
                    if all(self.succ_week[w.week_id] >= w.target or self._exhausted(w) for w in self.weeks):
                        stop_reason = "targets_met_or_exhausted"
                        break
                    time.sleep(0.2)
            # drain anything still running after an anomaly stop
            for fut in list(futures):
                row = futures.pop(fut)
                if fut.cancelled():
                    self.tried.discard(self.pop.at[row, "article_id"])
                    p = self.pop.loc[row]
                    self.inflight_day[p["date_iso"]] -= 1
                    self.inflight_week[self.week_of_date[p["date_iso"]].week_id] -= 1
                    self.inflight_dom[p["domain_key"]] -= 1
                    self.inflight_rl.discard(p["rate_limit_key"])
                    continue
                rec, body = fut.result()
                self.tried.discard(rec["article_id"])
                self._finish(row, rec, body)
        elapsed = round(time.monotonic() - t0, 1)
        stats = {k: int(self.stage_stats.get(k, 0)) for k in ("records", "requests_sent", "fetch_ok", "success")}
        summary = {"stage": self.stage, "stop_reason": stop_reason, "elapsed_sec": elapsed, **stats}
        self.store.event(self.stage, "stage_end", json.dumps(summary))
        return summary

    def _progress(self) -> None:
        by_stratum = defaultdict(lambda: [0, 0])
        for w in self.weeks:
            by_stratum[w.stratum][0] += self.succ_week[w.week_id]
            by_stratum[w.stratum][1] += w.target
        self.log(f"[{now()}] records={self.stage_count} " +
                 " ".join(f"{k}:{v[0]}/{v[1]}" for k, v in by_stratum.items()))


def status(cfg: dict) -> None:
    db = project_path(cfg["output_dir"]) / cfg["runs"]["db"]
    if not db.exists():
        print(f"no collection database yet ({db}). No requests were sent.")
        return
    s = Store(db)
    rows = s.attempts("stage, extract_status, request_sent")
    c = Counter((r["stage"], r["extract_status"]) for r in rows)
    print(json.dumps({"db": str(db), "records": len(rows),
                      "by_stage_status": {f"{k[0]}/{k[1]}": v for k, v in sorted(c.items())}}, indent=1))
    print("No requests were sent. Use --stage smoke|pilot|main to collect.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["smoke", "pilot", "main"], default=None)
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.stage is None:
        status(cfg)
        return
    store = Store(project_path(cfg["output_dir"]) / cfg["runs"]["db"])
    summary = Runner(cfg, args.stage, store).run()
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
