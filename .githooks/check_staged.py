"""Pre-commit check (run by .githooks/pre-commit). Blocks a commit when a staged file
  1. is an .env file (anything named .env or .env.* except .env.example),
  2. adds a line containing a local user path (drive-letter or home-folder "Users" path),
  3. adds a line containing an actual API key value from the local .env.
Only added lines are checked, so history that already exists is not re-flagged.
Secret values are never printed; only the file name and line number are reported.
Set HOOK_ENV_FILE to use another env file (used for testing).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# decode git output as UTF-8 explicitly (the Windows default code page cannot read non-ASCII paths)
ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True).stdout.decode("utf-8").strip())
SELF = {".githooks/check_staged.py", ".githooks/pre-commit"}  # the patterns below live here
# drive-letter path into a Users folder, POSIX-style mount of it, or a /Users/ or /home/ home folder
USER_PATH = re.compile(r"[A-Za-z]:(\\\\|\\|/)+Users(\\\\|\\|/)|/[a-z]/Users/|(^|[\s\"'(=])/(Users|home)/[^/\s]+/", re.I)
ENV_NAME = re.compile(r"(^|/)\.env(\.[^/]+)?$")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, cwd=ROOT).stdout.decode("utf-8", "replace")


def env_values() -> list[str]:
    path = Path(os.environ.get("HOOK_ENV_FILE", ROOT / ".env"))
    vals = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                v = line.split("=", 1)[1].strip().strip("'\"")
                if len(v) >= 8:
                    vals.append(v)
    return vals


def main() -> int:
    files = [f for f in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0") if f]
    secrets = env_values()
    problems = []
    for f in files:
        if ENV_NAME.search(f) and not f.endswith(".env.example"):
            problems.append(f"{f}: .env file must not be committed")
            continue
        if f in SELF:
            continue
        diff = git("diff", "--cached", "-U0", "--no-color", "--text", "--", f)
        lineno = 0
        for line in diff.splitlines():
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", line)
            if m:
                lineno = int(m.group(1))
                continue
            if line.startswith("+") and not line.startswith("+++"):
                text = line[1:]
                if USER_PATH.search(text):
                    problems.append(f"{f}:{lineno}: local user path")
                if any(s in text for s in secrets):
                    problems.append(f"{f}:{lineno}: actual API key value")
                lineno += 1
    if problems:
        print("pre-commit: commit blocked", file=sys.stderr)
        for p in problems[:50]:
            print("  " + p, file=sys.stderr)
        if len(problems) > 50:
            print(f"  ... and {len(problems) - 50} more", file=sys.stderr)
        print("Use project-relative paths; never commit .env or key values.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
