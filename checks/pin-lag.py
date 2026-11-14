#!/usr/bin/env python3
"""Detector: the running pin trails committed main by a bounded age.

Reads the pin (readlink of SITE/runtime/current, no writes) and local main. The age is the commit
time of the oldest commit on main that is not yet in the pinned release. It is GREEN under
--green-age (default 60 min), AMBER up to --red-age (default 3 h), RED beyond that.

It also reports the stage and last failure reason of the newest delivery run log, so a lagging pin
says where it stopped instead of only that it lags. Stages: check, review, apply, verify.

Exit status:
  0  GREEN   the pin is main's HEAD, or the oldest unreleased commit is younger than --green-age
  1  AMBER   older than --green-age, not yet older than --red-age
  2  RED     the oldest unreleased commit is older than --red-age
  3  UNKNOWN no pin, the pin is not a commit in this clone, or it is not an ancestor of HEAD
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

GREEN_AGE_S = 60 * 60.0
RED_AGE_S = 3 * 3600.0
DELIVERY_DIR = Path("operator/plans/moving-pin-20261105/delivery")
STAGE_PATTERNS = [
    ("verify", re.compile(r"verify exit|verify did not|delivery not verified")),
    ("apply", re.compile(r"apply exit|apply\b")),
    ("review", re.compile(r"review not clear|gate check: phase=|review-unavailable")),
    ("check", re.compile(r"check started|waiting up to|retarget|check failed")),
]
LEGEND_MARK = "verified-or-nothing-to-do"
FAILURE_PATTERN = re.compile(r"exit [1-9]|not clear|refused|failed|unavailable|still busy|did not deliver")


def git(repo: Path, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                              env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin",
                                   "HOME": str(Path.home())})
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, done.stdout.strip() if done.returncode == 0 else (done.stderr.strip() or done.stdout.strip())


def pinned_sha(home: Path) -> tuple[str | None, str]:
    link = home / "runtime" / "current"
    if not link.is_symlink():
        return None, "no runtime/current symlink"
    target = link.readlink().name
    if not re.fullmatch(r"[0-9a-f]{40}", target):
        return None, f"pin does not name a full commit: {target[:40]}"
    return target, ""


def newest_run_log(delivery: Path) -> Path | None:
    logs = sorted(delivery.glob("deliver-run-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def last_stage_and_failure(log: Path | None) -> tuple[str, str]:
    if log is None:
        return "unknown", "no delivery run log"
    stage = "unknown"
    failure = ""
    for raw in log.read_text(errors="replace").splitlines():
        ln = raw.strip()
        # The exit-code legend printed at the end of every run names every stage; it is not a fact of this run.
        if not ln or LEGEND_MARK in ln:
            continue
        for name, pat in STAGE_PATTERNS:
            if pat.search(ln):
                stage = name
                break
        if FAILURE_PATTERN.search(ln):
            failure = ln
    return stage, failure[:200]


def evaluate(home: Path, now: float, green_age: float = GREEN_AGE_S, red_age: float = RED_AGE_S) -> tuple[int, list[str]]:
    repo = home.parent
    code, head = git(repo, "rev-parse", "HEAD")
    if code != 0:
        return 3, [f"cannot read local HEAD: {head[:120]}"]
    pin, why = pinned_sha(home)
    if pin is None:
        return 3, [why]
    if pin == head:
        return 0, [f"pin {pin[:8]} is HEAD"]
    if git(repo, "cat-file", "-e", f"{pin}^{{commit}}")[0] != 0:
        return 3, [f"pin {pin[:8]} is not a commit in this clone"]
    if git(repo, "merge-base", "--is-ancestor", pin, head)[0] != 0:
        return 3, [f"pin {pin[:8]} is not an ancestor of HEAD {head[:8]}"]
    code, rows = git(repo, "log", f"{pin}..{head}", "--format=%ct")
    if code != 0:
        return 3, [f"cannot list unreleased commits: {rows[:160]}"]
    times = sorted(int(t) for t in rows.split() if t.isdigit())
    if not times:
        return 0, [f"pin {pin[:8]} has no unreleased commit"]
    age = now - times[0]
    stage, failure = last_stage_and_failure(newest_run_log(home / DELIVERY_DIR))
    detail = [
        f"pin {pin[:8]} trails HEAD {head[:8]} by {len(times)} commit(s); oldest unreleased is {age / 60:.0f} min old",
        f"last delivery stage: {stage}",
        f"last failure: {failure or 'none in newest run log'}",
    ]
    if age > red_age:
        return 2, detail
    if age > green_age:
        return 1, detail
    return 0, detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", "--home", dest="site", type=Path,
                        default=Path(__file__).resolve().parent.parent / ".mishe-tauftauf",
                        help="site home (--home is the name the scheduler coverage check passes)")
    parser.add_argument("--green-age", type=float, default=GREEN_AGE_S)
    parser.add_argument("--red-age", type=float, default=RED_AGE_S)
    args = parser.parse_args(argv)
    code, lines = evaluate(args.site, time.time(), args.green_age, args.red_age)
    state = {0: "GREEN", 1: "AMBER", 2: "RED", 3: "UNKNOWN"}[code]
    print(f"pin-lag {state}")
    for ln in lines:
        print(ln)
    return code


if __name__ == "__main__":
    sys.exit(main())
