#!/usr/bin/env python3
"""Detector: committed main is released to the running pin within a bounded time.

The running release is the directory that `SITE/runtime/current` points at, named by its commit SHA.
Every commit on main after that SHA is not live yet. This reads the pin (a readlink, no writes) and
local main, then goes RED when the oldest unreleased commit is older than --max-age. A moving pin that
stays behind shows up here even when every commit passed CI, which is the failure the watchdog and
`mishe release advance` exist to prevent.

Exit status:
  0  the pin is main's HEAD, or the oldest unreleased commit is younger than --max-age
  2  the oldest unreleased commit is older than --max-age
  3  undecidable: no pin, the pin is not a commit in this clone, or the pin is not an ancestor of HEAD
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

MAX_AGE_S = 30 * 60.0


def git(repo: Path, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                              env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin",
                                   "HOME": str(Path.home())})
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, done.stdout.strip() if done.returncode == 0 else (done.stderr.strip() or done.stdout.strip())


def pinned_sha(home: Path) -> tuple[str | None, str]:
    """The commit SHA named by runtime/current, read without touching the link."""
    link = home / "runtime" / "current"
    if not link.is_symlink():
        return None, f"{link} is not a symlink to a release"
    target = Path(os.readlink(link))
    name = target.name
    if len(name) < 7 or any(c not in "0123456789abcdef" for c in name):
        return None, f"pin {target} is not named by a commit SHA"
    return name, ""


def evaluate(home: Path, repo: Path, now: float, max_age: float = MAX_AGE_S) -> tuple[int, list[str]]:
    pin, why = pinned_sha(home)
    if pin is None:
        return 3, [why]
    code, head = git(repo, "rev-parse", "HEAD")
    if code != 0:
        return 3, [f"cannot read local HEAD: {head[:120]}"]
    if pin == head or head.startswith(pin):
        return 0, [f"the pin {pin[:8]} is HEAD"]
    if git(repo, "cat-file", "-e", f"{pin}^{{commit}}")[0] != 0:
        return 3, [f"the pin {pin[:8]} is not a commit in this clone"]
    if git(repo, "merge-base", "--is-ancestor", pin, head)[0] != 0:
        return 3, [f"the pin {pin[:8]} is not an ancestor of HEAD {head[:8]}"]
    code, rows = git(repo, "log", f"{pin}..{head}", "--format=%ct")
    if code != 0:
        return 3, [f"cannot list unreleased commits: {rows[:160]}"]
    times = sorted(int(t) for t in rows.split() if t.isdigit())
    if not times:
        return 3, ["HEAD differs from the pin but no unreleased commit was found"]
    age = now - times[0]
    summary = (f"{len(times)} commit(s) on main not live; the pin is {pin[:8]}, the oldest is "
               f"{age / 60:.0f} min old (limit {max_age / 60:.0f} min)")
    return (2, [summary]) if age > max_age else (0, [summary])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path, help="SITE home holding runtime/current")
    parser.add_argument("--repo", required=True, type=Path, help="shared Git checkout")
    parser.add_argument("--max-age-minutes", type=float, default=MAX_AGE_S / 60)
    args = parser.parse_args(argv)
    code, lines = evaluate(args.home, args.repo, time.time(), args.max_age_minutes * 60)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
