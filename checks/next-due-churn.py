#!/usr/bin/env python3
"""Detector: the due-time timer is replaced instead of kept, so every arm costs a restart.

Every producer signal write arms the single next-due timer, and until commit 606330ad the
scheduler stopped and re-created the pending transient timer on every arm even when it already
fired sooner. Measured 2026-11-18 09:47Z at the unfixed pin (release 4f0962ae): 156 distinct
``mishe-next-due-<hex>.timer`` units started in ten minutes while only one due handler ran — about
15 timer restarts a minute. The 2026-11-14 incident showed the same churn pushing an overdue retry
out until it never fired.

The window is five minutes, so a reading settles quickly after a pin move instead of carrying the
old release's churn for ten.

The invariant is the pairing, not an absolute rate: a due handler fire legitimately re-arms the
timer once, so a timer start whose unit never ran its due handler in the same window is a
replacement that cost a restart. A kept timer shows up once, at arm time, and fires.

``checks/next-due-armed.py`` owns "a timer exists"; this check only judges whether the scheduler
keeps it, so an empty window is undecidable here rather than a second GREEN for the same fact.

Exit status:
  0  GREEN   at most --max-churn timer starts had no matching due handler run
  2  RED     more (the detail gives the counts, the window and the unfixed baseline)
  3  UNKNOWN journalctl could not be read, or no next-due timer was started in the window
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

TIMER_UNIT = "mishe-next-due"
HEX_LEN = 12
TIMER_START = re.compile(rf"Started ({TIMER_UNIT}-[0-9a-f]{{{HEX_LEN}}}\.timer)")
SERVICE_RUN = re.compile(rf"(?:Starting|Finished|Failed) ({TIMER_UNIT}-[0-9a-f]{{{HEX_LEN}}}\.service)")
HEX = re.compile(rf"{TIMER_UNIT}-([0-9a-f]{{{HEX_LEN}}})\.")
WINDOW_S = 300.0
MAX_CHURN = 10
BASELINE = "156 timer starts against 1 due handler run in 10 min at release 4f0962ae (2026-11-18 09:47Z)"


def units_started(journal: str) -> tuple[set[str], set[str]]:
    """Start hexes of next-due timers and hexes of next-due services that were started or finished."""
    timers = {HEX.search(name).group(1) for name in TIMER_START.findall(journal)}
    services = {HEX.search(name).group(1) for name in SERVICE_RUN.findall(journal)}
    return timers, services


def evaluate(timers: set[str], services: set[str], *, window_s: float,
             max_churn: int = MAX_CHURN) -> tuple[int, list[str]]:
    """Return (exit_code, lines) for the observed unit starts in the window."""
    minutes = int(window_s // 60)
    if not timers:
        return 3, [f"no {TIMER_UNIT} timer was started in the last {minutes} min, so churn cannot be judged "
                   f"(checks/next-due-armed.py owns 'a timer exists')"]
    churn = timers - services
    if len(churn) <= max_churn:
        return 0, [f"{len(timers)} {TIMER_UNIT} timer(s) started, {len(timers) - len(churn)} ran a due "
                   f"handler, {len(churn)} replaced without a run in the last {minutes} min: "
                   f"at most {max_churn} is GREEN"]
    return 2, [f"{len(timers)} {TIMER_UNIT} timer(s) started, {len(timers) - len(churn)} ran a due "
               f"handler, {len(churn)} replaced without a run in the last {minutes} min: the scheduler "
               f"replaces a pending timer instead of keeping it (more than {max_churn} is RED); "
               f"baseline {BASELINE}"]


def read_journal(window_s: float) -> str:
    done = subprocess.run(["journalctl", "--user", "--since", f"-{int(window_s)}s", "--no-pager", "-o", "cat"],
                          capture_output=True, text=True, errors="replace", timeout=45)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[:160])
    return done.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-seconds", type=float, default=WINDOW_S)
    parser.add_argument("--max-churn", type=int, default=MAX_CHURN)
    args = parser.parse_args(argv)
    try:
        journal = read_journal(args.window_seconds)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"journal unreadable, so churn cannot be judged: {exc}")
        return 3
    timers, services = units_started(journal)
    code, lines = evaluate(timers, services, window_s=args.window_seconds, max_churn=args.max_churn)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
