"""NewsCatcher supplement collection for days missed by the original date-split bug.

The original collection (methods/hormuz_2026_news_textmining_filtered.ipynb) sent
date-only from_/to_ values, so each split range stopped at 00:00:00 of its last day.
This script re-requests whole days with explicit times. Only the time range differs
from the original request body: q, lang=en, exclude_duplicates=True, sort_by=date,
page_size=1000, no search_in.

Every response body is saved byte-for-byte; every request is appended to
requests.jsonl (the API key is never written). Pages already saved with HTTP 200
are skipped, so an interrupted run can be resumed.

Usage (from project root):
  python collection/supplement_fetch.py --query "\"Strait of Hormuz\"" --date 2026-05-02
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import dotenv_values

from common import load_config, project_path

SAFE_HEADERS = re.compile(r"^(x-|ratelimit|retry-after|cf-ray|date$|content-type$|content-length$|correlation-id$)", re.I)


def slug(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", q.lower()).strip("_")


class SupplementClient:
    def __init__(self, cfg: dict):
        self.sc = cfg["supplement"]
        self.out = project_path(self.sc["output_dir"])
        self.out.mkdir(parents=True, exist_ok=True)
        self.log_path = self.out / "requests.jsonl"
        key = dotenv_values(project_path(self.sc["dotenv_path"])).get(self.sc["api_key_env"])
        if not key:
            sys.exit(f"{self.sc['api_key_env']} missing in .env")
        self._headers = {"x-api-token": key, "Content-Type": "application/json"}

    def done_pages(self) -> set[tuple]:
        done = set()
        if self.log_path.exists():
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                if r.get("http_status") == 200 and r.get("saved_file"):
                    done.add((r["query"], r["from_"], r["to_"], r["page"]))
        return done

    def _log(self, rec: dict) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def request_page(self, q: str, from_: str, to_: str, page: int, label: str) -> dict | None:
        body = {"q": q, "from_": from_, "to_": to_, "page": page, **self.sc["request_body_fixed"]}
        dest_dir = self.out / "raw" / slug(q) / label
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"page_{page:03d}.json"
        for attempt in range(self.sc["max_retries"]):
            t0 = time.time()
            rec = {"requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "query": q, "from_": from_, "to_": to_, "page": page, "label": label,
                   "request_body": body, "attempt": attempt + 1}
            try:
                r = requests.post(self.sc["api_url"], headers=self._headers, json=body,
                                  timeout=self.sc["timeout_sec"])
            except requests.RequestException as e:
                rec.update(http_status=None, error=type(e).__name__, elapsed_sec=round(time.time() - t0, 2))
                self._log(rec)
                time.sleep(min(60, 2 ** attempt * 2))
                continue
            rec.update(http_status=r.status_code, elapsed_sec=round(time.time() - t0, 2),
                       response_headers={k: v for k, v in r.headers.items() if SAFE_HEADERS.match(k)})
            if r.status_code == 200:
                dest.write_bytes(r.content)
                data = r.json()
                rec.update(saved_file=dest.relative_to(self.out).as_posix(),
                           total_hits=data.get("total_hits"), total_pages=data.get("total_pages"),
                           n_articles=len(data.get("articles") or []), user_input=data.get("user_input"))
                self._log(rec)
                return data
            rec["error_body"] = r.text[:500]
            self._log(rec)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                time.sleep(min(60, 2 ** attempt * 2))
                continue
            return None  # 4xx other than 429: do not retry
        return None

    def fetch_range(self, q: str, from_: str, to_: str, label: str) -> dict:
        done = self.done_pages()
        first = self.request_page(q, from_, to_, 1, label) if (q, from_, to_, 1) not in done else \
            json.loads((self.out / "raw" / slug(q) / label / "page_001.json").read_bytes())
        if first is None:
            return {"status": "failed_page_1"}
        hits, pages = int(first.get("total_hits") or 0), int(first.get("total_pages") or 1)
        if hits > self.sc["split_threshold_hits"]:
            return {"status": "needs_split", "total_hits": hits, "total_pages": pages}
        for p in range(2, pages + 1):
            if (q, from_, to_, p) in done:
                continue
            if self.request_page(q, from_, to_, p, label) is None:
                return {"status": f"failed_page_{p}", "total_hits": hits, "total_pages": pages}
            time.sleep(self.sc["page_sleep_sec"])
        return {"status": "complete", "total_hits": hits, "total_pages": pages}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help='exact q value, e.g. "\\"Strait of Hormuz\\""')
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (whole day, UTC)")
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    sc = cfg["supplement"]
    d = datetime.strptime(args.date, "%Y-%m-%d")
    fmt = sc["datetime_format"]
    from_ = datetime.strptime(f"{args.date} {sc['day_start_time']}", "%Y-%m-%d %H:%M:%S").strftime(fmt)
    to_ = datetime.strptime(f"{args.date} {sc['day_end_time']}", "%Y-%m-%d %H:%M:%S").strftime(fmt)
    client = SupplementClient(cfg)
    res = client.fetch_range(args.query, from_, to_, label=d.strftime("%Y-%m-%d"))
    print(json.dumps({"query": args.query, "from_": from_, "to_": to_, **res}, ensure_ascii=False))


if __name__ == "__main__":
    main()
