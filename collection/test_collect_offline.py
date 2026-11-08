"""Offline tests for the collector (network is blocked for the whole run).

  python collection/test_collect_offline.py
"""
from __future__ import annotations

import copy
import socket
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import pandas as pd

import collect
from common import load_config, project_path
from extract import extract_and_judge, snippet_match
from fetch import RateLimiter
from sampling import build_frame
from store import Store


def _no_network(*a, **k):
    raise RuntimeError("network access attempted in offline test")


socket.socket.connect = _no_network  # type: ignore[assignment]
socket.create_connection = _no_network  # type: ignore[assignment]

ARTICLE = ("<html><head><title>T</title></head><body><article><h1>Tanker traffic</h1>"
           + "".join(f"<p>Paragraph {i}: shipping through the Strait of Hormuz remained tense as insurers "
                     f"raised premiums and navies escorted vessels across the Gulf on day {i}.</p>" for i in range(12))
           + "</article></body></html>").encode()
PAYWALL = (b"<html><body><article><p>Subscribe to continue reading. Already a subscriber? Sign in.</p>"
           b"<p>Hormuz tensions rise.</p></article></body></html>")


class FakeFetcher:
    """Domains whose key starts with a-f are 'blocked' (403); others return an article."""

    def __init__(self):
        self.limiter = RateLimiter(0.0)
        self.requests = 0

    def robots(self, url, rl_key, store):
        return "robots_rules", None

    def robots_allows(self, parser, url):
        return True

    def fetch(self, url, rl_key):
        self.requests += 1
        if rl_key[0] in "abcdef":
            return {"http_status": 403, "final_url": url, "fetch_status": "blocked", "failure_reason": "http_403",
                    "content": None, "attempts": 1, "content_type": "text/html", "elapsed_sec": 0.0}
        return {"http_status": 200, "final_url": url, "fetch_status": "ok", "failure_reason": None,
                "content": ARTICLE, "attempts": 1, "content_type": "text/html", "elapsed_sec": 0.0}

    def save_html(self, article_id, content):
        return None


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_extract_success_and_paywall(self):
        ec = self.cfg["extract"]
        ok = extract_and_judge(ARTICLE, "https://x.test/a", "Paragraph 0: shipping through the Strait of Hormuz",
                               "200", ec, set())
        self.assertEqual(ok["extract_status"], "success", ok["failure_reason"])
        self.assertTrue(ok["snippet_contained"])
        dup = extract_and_judge(ARTICLE, "https://x.test/a", "", "200", ec, {ok["body_sha1"]})
        self.assertEqual(dup["failure_reason"], "exact_duplicate")
        pw = extract_and_judge(PAYWALL, "https://x.test/b", "", "500", ec, set())
        self.assertEqual(pw["failure_reason"], "paywall")

    def test_snippet_similarity(self):
        c, s = snippet_match("The strait of hormuz is closed to tanker traffic today, officials said on monday",
                             "officials said: the strait of hormuz is closed to tankers traffic today, officials said on monday",
                             120, 5)
        self.assertFalse(c)
        self.assertGreater(s, 0.9)

    def test_weeks_match_plan(self):
        _, weeks = build_frame(self.cfg)
        plan = pd.read_csv(project_path(self.cfg["output_dir"]) / "plan" / "week_allocation.csv")
        self.assertEqual([w.quota for w in weeks], plan["quota"].tolist())
        self.assertEqual([w.week_id for w in weeks], plan["week_id"].tolist())

    def test_smoke_budget_spread_and_domain_skip(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["fetch"]["max_workers"] = 4
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            store = Store(Path(td) / "t.sqlite3")
            self.addCleanup(store.close)
            fake = FakeFetcher()
            r = collect.Runner(cfg, "smoke", store, fetcher=fake, log=lambda *a: None)
            summary = r.run()
            rows = store.attempts()
            self.assertEqual(len(rows), cfg["runs"]["stages"]["smoke"])
            self.assertEqual(summary["stop_reason"], "budget_reached")
            weeks = Counter(x["week_id"] for x in rows)
            self.assertGreaterEqual(len(weeks), 30)
            self.assertEqual({x["stratum"] for x in rows}, {"pre_war", "post_war"})
            # article bodies are identical in the fake, so only the first success survives
            self.assertEqual(sum(x["extract_status"] == "success" for x in rows), 1)
            self.assertEqual(fake.requests, sum(int(x["request_sent"] or 0) for x in rows))
            # resume: a second smoke run adds nothing (budget already used)
            again = collect.Runner(cfg, "smoke", store, fetcher=fake, log=lambda *a: None).run()
            self.assertEqual(again["records"], 0)
            # pilot continues with new articles only
            r3 = collect.Runner(cfg, "pilot", store, fetcher=fake, log=lambda *a: None)
            r3.budget = 400
            r3.run()
            rows = store.attempts()
            self.assertEqual(len({x["article_id"] for x in rows}), len(rows))
            skipped = [x for x in rows if x["failure_reason"] == "domain_skipped_after_failures"]
            for x in skipped:
                self.assertEqual(x["request_sent"], 0)
                self.assertIn(x["rate_limit_key"][0], "abcdef")


if __name__ == "__main__":
    unittest.main(verbosity=2)
