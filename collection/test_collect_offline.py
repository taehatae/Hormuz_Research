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
        if getattr(self, "mode", None) == "timeout":
            return {"http_status": None, "final_url": None, "fetch_status": "timeout", "failure_reason": "timeout",
                    "content": None, "attempts": 3, "content_type": None, "elapsed_sec": 0.0}
        if getattr(self, "mode", None) == "unique":
            body = ARTICLE.replace(b"Tanker traffic", url.encode()[-60:].replace(b"&", b" "))
            return {"http_status": 200, "final_url": url, "fetch_status": "ok", "failure_reason": None,
                    "content": body, "attempts": 1, "content_type": "text/html", "elapsed_sec": 0.0}
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

    def judge(self, html, url="https://x.test/news/a", title="Tanker traffic in Hormuz", snippet="", nc="200",
              hashes=frozenset()):
        return extract_and_judge(html, url, None, title, snippet, nc, self.cfg["extract"], set(hashes))

    def test_extract_success_and_paywall(self):
        ok = self.judge(ARTICLE, snippet="Paragraph 0: shipping through the Strait of Hormuz")
        self.assertEqual(ok["extract_status"], "success", ok["failure_reason"])
        self.assertTrue(ok["snippet_contained"])
        self.assertTrue(ok["title_has_hormuz"])
        self.assertGreaterEqual(ok["sentence_count"], 12)
        self.assertFalse(ok["over_10000_words"])
        dup = self.judge(ARTICLE, hashes={ok["body_sha1"]})
        self.assertEqual(dup["failure_reason"], "exact_duplicate")
        pw = self.judge(PAYWALL, nc="500")
        self.assertEqual(pw["failure_reason"], "paywall")

    def test_new_criteria(self):
        # words ratio: 192 body words vs nc 1000 -> partial; nc missing -> rule not applied
        self.assertEqual(self.judge(ARTICLE, nc="1000")["failure_reason"], "partial_body")
        self.assertEqual(self.judge(ARTICLE, nc="")["extract_status"], "success")
        self.assertEqual(self.judge(ARTICLE, nc="0")["extract_status"], "success")
        # listing page by URL path; live blogs only flagged
        self.assertEqual(self.judge(ARTICLE, url="https://x.test/topic/iran")["failure_reason"], "non_article_page")
        live = self.judge(ARTICLE, url="https://x.test/live-news/iran-war-08-08")
        self.assertEqual((live["extract_status"], live["page_type_suspect"]), ("success", "live"))
        # 100-word floor
        short = ("<html><body><article>" + "".join(
            f"<p>Sentence {i} about the Strait of Hormuz and shipping lanes today.</p>" for i in range(11))
                 + "</article></body></html>").encode()
        r = self.judge(short, nc="")
        self.assertTrue(100 <= r["body_word_count"] < 150, r["body_word_count"])
        self.assertEqual(r["extract_status"], "success")
        # one long paragraph mentioning 'sign in' once is not boilerplate (sentence-level share)
        longp = ("<html><body><article><p>" + " ".join(
            f"Tankers waited near the Strait of Hormuz on day {i} as insurers reviewed cover." for i in range(25))
                 + " Readers can sign in to save articles.</p></article></body></html>").encode()
        self.assertEqual(self.judge(longp, nc="")["extract_status"], "success")
        # code / CSS body
        css = ("<html><body><article><p>" + " ".join(
            ".btn{font-size:17px;max-width:500px;} @media (min-width: 768px){.share{box-sizing:border-box;}}"
            for _ in range(30)) + " Hormuz</p></article></body></html>").encode()
        self.assertEqual(self.judge(css, nc="")["failure_reason"], "code_or_markup")
        # language
        fr = ("<html><body><article>" + "".join(
            f"<p>Le détroit d'Ormuz (Hormuz) reste fermé aux pétroliers selon les autorités, jour {i}.</p>"
            for i in range(15)) + "</article></body></html>").encode()
        self.assertEqual(self.judge(fr, nc="")["failure_reason"], "not_english")

    def test_mention_counts(self):
        from extract import mention_counts
        self.assertEqual(mention_counts("The Strait of Hormuz... HORMUZ STRAIT and hormuz island; strait  of\nhormuz"), (4, 3))
        ok = self.judge(ARTICLE)
        self.assertEqual((ok["hormuz_mention_count"], ok["strait_of_hormuz_count"]), (12, 12))
        self.assertEqual(ok["extract_status"], "success")  # recorded only, not a criterion

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
            cfg["output_dir"] = td  # absolute: progress logs etc. never touch the real output folder
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


    def _runner(self, td, stage, mode, **runs):
        cfg = copy.deepcopy(self.cfg)
        cfg["fetch"]["max_workers"] = 4
        cfg["output_dir"] = td  # absolute: progress logs etc. never touch the real output folder
        cfg["runs"].update(runs)
        store = Store(Path(td) / "t.sqlite3")
        self.addCleanup(store.close)
        fake = FakeFetcher()
        fake.mode = mode
        r = collect.Runner(cfg, stage, store, fetcher=fake, log=lambda *a: None)
        r.progress_path = Path(td) / "progress.log"
        return r, store

    def test_main_stops_exactly_at_total_target(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            r, store = self._runner(td, "main", "unique", anomaly_window_records=10_000)
            r.total_target = 12
            summary = r.run()
            rows = store.attempts()
            self.assertEqual(summary["stop_reason"], "target_reached")
            self.assertEqual(sum(x["extract_status"] == "success" for x in rows), 12)
            self.assertIn("FINAL", (Path(td) / "progress.log").read_text(encoding="utf-8"))

    def test_anomaly_stop_on_network_errors(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            r, store = self._runner(td, "main", "timeout", anomaly_max_consecutive_network_errors=30,
                                    anomaly_check_after=10, anomaly_min_fetch_ok=0)
            summary = r.run()
            self.assertIn("consecutive timeouts", summary["stop_reason"])
            self.assertLess(summary["records"], 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
