"""SQLite store: one row per attempted URL, written immediately (resumable)."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ATTEMPT_COLUMNS = [
    "article_id", "url", "final_url", "http_status", "fetch_status", "extract_status", "failure_reason",
    "robots_note", "body_word_count", "words_ratio_to_nc", "snippet_match", "snippet_contained", "has_hormuz",
    "language_detected", "body_sha1", "stratum", "date", "week_id", "domain", "domain_key", "rate_limit_key",
    "attempted_at", "stage", "request_sent", "fetch_attempts", "elapsed_sec", "content_type", "html_path",
    "paywall_share", "boilerplate_share", "title_extracted", "extractor_version",
    "hormuz_mention_count", "strait_of_hormuz_count",
    "title_has_hormuz", "page_type_suspect", "sentence_count", "title_body_overlap", "body_near_title",
    "non_alpha_ratio", "over_10000_words", "code_token_density", "judged_with",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS attempts (
  {", ".join(c + (" TEXT PRIMARY KEY" if c == "article_id" else "") for c in ATTEMPT_COLUMNS)}
);
CREATE TABLE IF NOT EXISTS bodies (article_id TEXT PRIMARY KEY, body TEXT);
CREATE TABLE IF NOT EXISTS robots (origin TEXT PRIMARY KEY, note TEXT, http_status INTEGER, text TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS events (at TEXT, stage TEXT, kind TEXT, detail TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        existing = {r[1] for r in self.con.execute("PRAGMA table_info(attempts)")}
        for c in ATTEMPT_COLUMNS:  # forward-compatible: add columns introduced after the DB was created
            if c not in existing:
                self.con.execute(f"ALTER TABLE attempts ADD COLUMN {c}")
        self.lock = threading.Lock()

    def close(self) -> None:
        with self.lock:
            self.con.close()

    def add_attempt(self, rec: dict, body: str | None) -> None:
        cols = ATTEMPT_COLUMNS
        vals = [rec.get(c) for c in cols]
        with self.lock:
            self.con.execute("BEGIN")
            self.con.execute(f"INSERT INTO attempts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            if body:
                self.con.execute("INSERT OR REPLACE INTO bodies VALUES (?,?)", (rec["article_id"], body))
            self.con.execute("COMMIT")

    def event(self, stage: str, kind: str, detail: str) -> None:
        with self.lock:
            self.con.execute("INSERT INTO events VALUES (?,?,?,?)", (now(), stage, kind, detail))

    def get_robots(self, origin: str):
        with self.lock:
            r = self.con.execute("SELECT note, text FROM robots WHERE origin=?", (origin,)).fetchone()
        return r

    def put_robots(self, origin: str, note: str, status, text) -> None:
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO robots VALUES (?,?,?,?,?)", (origin, note, status, text, now()))

    def attempts(self, columns: str = "*"):
        with self.lock:
            cur = self.con.execute(f"SELECT {columns} FROM attempts")
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]
