"""HTTP fetching with robots.txt handling and per-registrable-domain spacing.

- robots.txt: only an explicit Disallow in a successfully fetched (HTTP 200) robots.txt
  stops a fetch (robots_disallow). 4xx -> no robots (proceed), 5xx/timeout/error ->
  unavailable (proceed). The reason is always recorded.
- Blocks, Cloudflare challenges and paywalls are recorded, never bypassed.
- Retries only for 5xx, 429, timeouts and transient connection errors.
"""
from __future__ import annotations

import gzip
import threading
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

import requests

CF_MARKERS = (b"cf-chl", b"challenge-platform", b"Just a moment...", b"cf_chl_opt", b"Attention Required! | Cloudflare")


class RateLimiter:
    """At most one request start per `interval` seconds per key."""

    def __init__(self, interval: float):
        self.interval = interval
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def ready_at(self, key: str) -> float:
        with self._lock:
            return self._next.get(key, 0.0)

    def wait(self, key: str) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                t = self._next.get(key, 0.0)
                if now >= t:
                    self._next[key] = now + self.interval
                    return
            time.sleep(min(t - now, 1.0))


class Fetcher:
    def __init__(self, cfg: dict, html_root: Path):
        self.fc = cfg["fetch"]
        self.html_root = html_root
        self.limiter = RateLimiter(self.fc["per_domain_min_interval_sec"])
        self.headers = {"User-Agent": self.fc["user_agent"], "Accept": self.fc["accept_header"]}
        self.timeout = (self.fc["connect_timeout_sec"], self.fc["read_timeout_sec"])
        self._robots: dict[str, tuple] = {}
        self._robots_lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}

    # ---------- robots ----------
    def robots(self, url: str, rl_key: str, store) -> tuple[str, urllib.robotparser.RobotFileParser | None]:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        with self._robots_lock:
            lock = self._host_locks.setdefault(origin, threading.Lock())
        with lock:
            if origin in self._robots:
                return self._robots[origin]
            cached = store.get_robots(origin)
            if cached is not None:
                note, text = cached
            else:
                note, text, status = self._fetch_robots(origin, rl_key)
                store.put_robots(origin, note, status, text)
            parser = None
            if note == "robots_rules" and text is not None:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(text.splitlines())
            self._robots[origin] = (note, parser)
            return note, parser

    def _fetch_robots(self, origin: str, rl_key: str):
        self.limiter.wait(rl_key)
        try:
            r = requests.get(origin + "/robots.txt", headers=self.headers,
                             timeout=self.fc["robots_timeout_sec"], allow_redirects=True)
        except requests.Timeout:
            return "robots_unavailable_timeout", None, None
        except requests.RequestException as e:
            return f"robots_unavailable_{type(e).__name__}", None, None
        if r.status_code == 200:
            return "robots_rules", r.text, 200
        if 400 <= r.status_code < 500:
            return f"robots_absent_http_{r.status_code}", None, r.status_code
        return f"robots_unavailable_http_{r.status_code}", None, r.status_code

    def robots_allows(self, parser, url: str) -> bool:
        if parser is None:
            return True
        return parser.can_fetch(self.fc["robots_user_agent_token"], url)

    # ---------- article ----------
    def fetch(self, url: str, rl_key: str) -> dict:
        """Returns dict(http_status, final_url, fetch_status, failure_reason, content, attempts, elapsed_sec)."""
        retry_status = set(self.fc["retry_on_status"])
        out = {"http_status": None, "final_url": None, "fetch_status": None, "failure_reason": None,
               "content": None, "attempts": 0, "content_type": None}
        t0 = time.monotonic()
        for attempt in range(self.fc["max_retries"] + 1):
            if attempt:
                time.sleep(self.fc["backoff_base_sec"] * (2 ** (attempt - 1)))
            self.limiter.wait(rl_key)
            out["attempts"] = attempt + 1
            try:
                with requests.get(url, headers=self.headers, timeout=self.timeout,
                                  allow_redirects=True, stream=True) as r:
                    out["http_status"], out["final_url"] = r.status_code, r.url
                    out["content_type"] = r.headers.get("Content-Type", "")
                    body = b""
                    for chunk in r.iter_content(65536):
                        body += chunk
                        if len(body) > self.fc["max_html_bytes"]:
                            break
                    cf = r.headers.get("cf-mitigated", "").lower() == "challenge"
            except requests.Timeout:
                out.update(fetch_status="timeout", failure_reason="timeout")
                continue
            except requests.exceptions.SSLError:
                out.update(fetch_status="connection_error", failure_reason="ssl_error")
                break
            except requests.exceptions.TooManyRedirects:
                out.update(fetch_status="http_error", failure_reason="too_many_redirects")
                break
            except requests.ConnectionError:
                out.update(fetch_status="connection_error", failure_reason="connection_error")
                continue
            except requests.RequestException as e:
                out.update(fetch_status="connection_error", failure_reason=f"request_error:{type(e).__name__}")
                break
            st = out["http_status"]
            if cf or (st in (403, 503) and any(m in body[:20000] for m in CF_MARKERS)):
                out.update(fetch_status="blocked", failure_reason="cloudflare_challenge", content=None)
                break
            if st == 200:
                if len(body) > self.fc["max_html_bytes"]:
                    out.update(fetch_status="http_error", failure_reason="too_large")
                    break
                ctype = (out["content_type"] or "").lower()
                if "html" not in ctype and not body.lstrip()[:200].lower().startswith((b"<!doctype", b"<html", b"<")):
                    out.update(fetch_status="not_html", failure_reason="not_html")
                    break
                out.update(fetch_status="ok", failure_reason=None, content=body)
                break
            reason = f"http_{st}"
            out.update(fetch_status="blocked" if reason in self.fc["blocked_failure_reasons"] else "http_error",
                       failure_reason=reason)
            if st in retry_status:
                continue
            break
        out["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return out

    def save_html(self, article_id: str, content: bytes) -> str | None:
        if not self.fc["save_html_gzip"] or not content:
            return None
        d = self.html_root / article_id[:2]
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{article_id}.html.gz"
        with gzip.open(p, "wb") as f:
            f.write(content)
        return p.relative_to(self.html_root.parent).as_posix()
