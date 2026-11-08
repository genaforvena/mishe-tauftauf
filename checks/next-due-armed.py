#!/usr/bin/env python3
"""Detector: the due-time timer is armed, so due intentions and signal expiries wake minds.

Observed 2026-11-08: units-installed was missing, zero mishe-next-due timers existed, and
next-due.json stayed frozen for hours. runtime_cli.scheduler() recorded the next due time but
never armed it, so only DM and signal triggers woke minds. This check reads the two site-local
facts that prove arming (the recorded due time and a systemd transient timer) and goes RED when
they disagree.

Exit status:
  0  next-due.json is fresh (or not yet due) and a mishe-next-due timer is armed
  2  the recorded due time passed more than --grace ago, no mishe-next-due timer is armed,
     or the units-installed marker is missing
  3  undecidable: next-due.json is unreadable or systemctl cannot list timers
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

GRACE_S = 300.0
TIMER_PREFIX = "mishe-next-due-"


def list_timers() -> tuple[int, list[str]]:
    try:
        done = subprocess.run(["systemctl", "--user", "list-timers", "--all", "--no-legend", "--plain"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return 3, [str(exc)]
    if done.returncode != 0:
        return 3, [(done.stderr or done.stdout).strip()[:160]]
    names = []
    for line in done.stdout.splitlines():
        for field in line.split():
            if field.startswith(TIMER_PREFIX) and field.endswith(".timer"):
                names.append(field)
                break
    return 0, names


def evaluate(home: Path, now: float, timers: list[str] | None, grace: float = GRACE_S) -> tuple[int, list[str]]:
    """``timers`` is None when systemd could not be asked (then the verdict is UNKNOWN for that part)."""
    path = home / "next-due.json"
    try:
        due = float(json.loads(path.read_text(encoding="utf-8"))["at"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return 3, [f"next-due.json unreadable: {str(exc)[:120]}"]
    problems = []
    if not (home / "units-installed").exists():
        problems.append("units-installed marker is missing, so due-time timers are not installed")
    # An overdue target with a timer armed is a retry on arm.OVERDUE_RETRY_SECONDS backoff, not an unarmed one.
    if due < now - grace and not timers:
        problems.append(f"next due time passed {int((now - due) // 60)} min ago and was never armed")
    if timers is None:
        if problems:
            return 3, problems + ["timer listing unavailable, so arming cannot be confirmed"]
        return 3, ["timer listing unavailable"]
    if not timers and due >= now - grace:
        problems.append("no mishe-next-due timer is armed")
    if problems:
        return 2, problems
    return 0, [f"next due {int(due)} (in {int((due - now) // 60)} min); armed timers: {', '.join(timers) or 'none needed'}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--grace-minutes", type=float, default=GRACE_S / 60)
    args = parser.parse_args(argv)
    now = time.time()
    code, timers = list_timers()
    lines_code, lines = evaluate(args.home, now, timers if code == 0 else None, args.grace_minutes * 60)
    print("\n".join(lines))
    return lines_code


if __name__ == "__main__":
    sys.exit(main())
