"""Fill hormuz_mention_count / strait_of_hormuz_count for rows recorded before those
columns existed, by re-extracting from the saved gzip HTML (no network).

Re-extraction uses the same trafilatura settings; the re-extracted body hash is
compared with the stored body_sha1 so any non-determinism is reported, not hidden.

  python collection/backfill_mentions.py
"""
from __future__ import annotations

import gzip
import json

from common import load_config, project_path
from extract import extract_and_judge, mention_counts
from store import Store


def main() -> None:
    cfg = load_config()
    out = project_path(cfg["output_dir"])
    store = Store(out / cfg["runs"]["db"])
    with store.lock:
        rows = store.con.execute(
            "SELECT article_id, html_path, final_url, url, body_sha1, extract_status FROM attempts "
            "WHERE html_path IS NOT NULL AND hormuz_mention_count IS NULL").fetchall()
    res = {"rows": len(rows), "updated": 0, "hash_match": 0, "hash_mismatch": [], "missing_html": []}
    for aid, html_path, final_url, url, sha, status in rows:
        p = out / html_path
        if not p.exists():
            res["missing_html"].append(aid)
            continue
        html = gzip.decompress(p.read_bytes())
        ex = extract_and_judge(html, url, final_url, "", "", "", cfg["extract"], set())
        body = ex["body"] or ""
        if ex["body_sha1"] == sha:
            res["hash_match"] += 1
        else:
            res["hash_mismatch"].append(aid)
        h, s = mention_counts(body)
        with store.lock:
            store.con.execute("UPDATE attempts SET hormuz_mention_count=?, strait_of_hormuz_count=? WHERE article_id=?",
                              (h, s, aid))
        res["updated"] += 1
    with store.lock:
        n_succ_filled = store.con.execute(
            "SELECT COUNT(*) FROM attempts WHERE extract_status='success' AND hormuz_mention_count IS NOT NULL").fetchone()[0]
        n_succ = store.con.execute("SELECT COUNT(*) FROM attempts WHERE extract_status='success'").fetchone()[0]
    res["success_rows_filled"] = f"{n_succ_filled}/{n_succ}"
    store.event("backfill", "mention_counts", json.dumps(res))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
