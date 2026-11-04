#!/usr/bin/env python3
"""Detector: local main reaches the remote within a bounded time.

Genome owns publication. A held or silent publisher leaves commits only on this host, where one disk
failure or rewrite loses them and CI never sees them. This compares local HEAD with the remote `main`
(a read-only `git ls-remote`) and goes RED when the oldest unpushed commit is older than --max-age.

Exit status:
  0  the remote has HEAD, or the oldest unpushed commit is younger than --max-age
  2  the oldest unpushed commit is older than --max-age, or local and remote main have diverged
  3  undecidable: the remote cannot be read, or its main is unknown to this clone
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

MAX_AGE_S = 2 * 3600.0


def git(repo: Path, *args: str, timeout: float = 45.0) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                              env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin",
                                   "HOME": str(Path.home())})
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, done.stdout.strip() if done.returncode == 0 else (done.stderr.strip() or done.stdout.strip())


def evaluate(repo: Path, now: float, max_age: float = MAX_AGE_S, remote: str = "origin") -> tuple[int, list[str]]:
    code, head = git(repo, "rev-parse", "HEAD")
    if code != 0:
        return 3, [f"cannot read local HEAD: {head[:120]}"]
    code, out = git(repo, "ls-remote", remote, "refs/heads/main")
    if code != 0 or not out:
        return 3, [f"cannot read {remote}/main: {out[:160] or 'no such ref'}"]
    theirs = out.split()[0]
    if theirs == head:
        return 0, [f"{remote}/main has HEAD {head[:8]}"]
    if git(repo, "cat-file", "-e", f"{theirs}^{{commit}}")[0] != 0:
        return 3, [f"{remote}/main is {theirs[:8]}, which this clone does not have: fetch before judging"]
    if git(repo, "merge-base", "--is-ancestor", theirs, head)[0] != 0:
        return 2, [f"local main and {remote}/main have diverged (remote {theirs[:8]}, local {head[:8]})"]
    code, rows = git(repo, "log", f"{theirs}..{head}", "--format=%ct")
    if code != 0:
        return 3, [f"cannot list unpushed commits: {rows[:160]}"]
    times = sorted(int(t) for t in rows.split() if t.isdigit())
    if not times:
        return 3, ["HEAD differs from the remote but no unpushed commit was found"]
    age = now - times[0]
    summary = f"{len(times)} unpushed commit(s); the oldest is {age / 3600:.1f} h old (limit {max_age / 3600:.1f} h)"
    return (2, [summary]) if age > max_age else (0, [summary])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--max-age-hours", type=float, default=MAX_AGE_S / 3600)
    args = parser.parse_args(argv)
    code, lines = evaluate(args.repo, time.time(), args.max_age_hours * 3600)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
