#!/usr/bin/env python3
"""Detector: engine/budget.json obeys the accounts-section fail-closed rules.

Contract: docs/budget-json-contract.md, "budget-consistency duty". This slice checks only the
accounts section the producer (src/mishe_tauftauf/budget_producer.py) writes today. The ledger and
router rules (ledger select without a row, routing.roles selections) need sections that are not
shipped yet; they are not judged here and are named in the output as not_checked.

Checks (RED when any fails):
  - a window marked FRESH whose observed_at is older than --max-age, or whose reset_at is not in
    the future (the number is old but shown FRESH: contract rule 4)
  - a window with a non-numeric used/remaining/reset_at/observed_at field
  - an account whose state disagrees with its windows (FRESH needs every window FRESH and at
    least one window; UNREADABLE needs no windows; otherwise STALE)
  - an account marked FRESH with no windows

Exit status:
  0  GREEN: every account row matches its windows and no window is shown FRESH past its age
  2  RED: one of the checks above fails
  3  UNKNOWN: file missing, unreadable, not schema 1, or produced_at older than --max-age
     (a stale file cannot prove anything about current windows)

Usage: budget-consistency.py --home SITE_HOME [--max-age 900] [--now EPOCH] [--file PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

GREEN, RED, UNKNOWN = 0, 2, 3
# A window carries the producer's full state set (budget_producer.SOURCE_STATES plus FRESH/STALE):
# the engine's own label is carried through, so UNKNOWN/UNATTRIBUTED/UNREADABLE are legitimate
# non-admitting states, never malformed. Only FRESH is judged against the age and reset rules.
WINDOW_STATES = {"FRESH", "STALE", "UNKNOWN", "UNATTRIBUTED", "UNREADABLE"}
# Account rows are produced as FRESH, STALE or UNREADABLE only (budget_producer.build_accounts).
ACCOUNT_STATES = {"FRESH", "STALE", "UNREADABLE"}
NOT_CHECKED = ["ledger select rows (rule 1)", "cooling/routing exhaustion (rules 2-3)"]


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_window(account: str, name: str, w, now: float, max_age: float, problems: list) -> str | None:
    """Return the window's state as shown in the file, or None when the window is malformed."""
    if not isinstance(w, dict):
        problems.append(f"{account}.{name}: window is not an object")
        return None
    fields = ("used", "remaining", "reset_at", "observed_at")
    if not all(_num(w.get(f)) for f in fields):
        problems.append(f"{account}.{name}: non-numeric field among {fields}")
        return None
    shown = w.get("state")
    if shown not in WINDOW_STATES:
        problems.append(f"{account}.{name}: state {shown!r} is not a window state the producer writes")
        return None
    observed_at, reset_at = w["observed_at"], w["reset_at"]
    really_fresh = now - max_age <= observed_at <= now + 5 and reset_at > now
    if shown == "FRESH" and not really_fresh:
        problems.append(
            f"{account}.{name}: shown FRESH but age {now - observed_at:.0f}s "
            f"(max {max_age:.0f}s) or reset_at {reset_at:.0f} not in the future"
        )
    return shown


def check_accounts(data: dict, now: float, max_age: float) -> tuple[list, list, dict]:
    problems: list[str] = []
    exhausted: list[str] = []
    summary: dict[str, str] = {}
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        problems.append("accounts section missing or not an object")
        return problems, exhausted, summary
    for account, row in accounts.items():
        if not isinstance(row, dict):
            problems.append(f"{account}: row is not an object")
            continue
        state = row.get("state")
        if state not in ACCOUNT_STATES:
            problems.append(f"{account}: state {state!r} is not FRESH, STALE or UNREADABLE")
            continue
        windows = row.get("windows")
        if not isinstance(windows, dict):
            problems.append(f"{account}: windows missing or not an object")
            continue
        shown = []
        for name, w in windows.items():
            s = check_window(account, name, w, now, max_age, problems)
            if s is not None:
                shown.append(s)
            if isinstance(w, dict) and _num(w.get("used")) and _num(w.get("remaining")):
                if w["used"] >= 0.99 or w["remaining"] <= 0:
                    exhausted.append(f"{account}.{name}")
        if len(shown) != len(windows):
            # a malformed window already reported; the account cannot be judged fully
            summary[account] = "MALFORMED"
            continue
        if not windows:
            expect = "UNREADABLE"
        elif all(s == "FRESH" for s in shown):
            expect = "FRESH"
        else:
            expect = "STALE"
        if state == "FRESH" and not windows:
            problems.append(f"{account}: FRESH with no windows")
        elif state != expect:
            problems.append(f"{account}: state {state} but its windows say {expect}")
        summary[account] = state
    return problems, exhausted, summary


def run(home: Path, max_age: float, now: float, path: Path | None) -> tuple[int, dict]:
    target = path or home / "engine" / "budget.json"
    result: dict = {"file": str(target), "now": now, "max_age": max_age, "not_checked": NOT_CHECKED}
    try:
        data = json.loads(target.read_text())
    except FileNotFoundError:
        return UNKNOWN, {**result, "verdict": "UNKNOWN", "reason": "engine/budget.json does not exist"}
    except (OSError, ValueError) as exc:
        return UNKNOWN, {**result, "verdict": "UNKNOWN", "reason": f"unreadable: {exc}"}
    if not isinstance(data, dict) or data.get("schema") != 1:
        return UNKNOWN, {**result, "verdict": "UNKNOWN", "reason": "not a schema 1 budget file"}
    produced_at = data.get("produced_at")
    if not _num(produced_at):
        return UNKNOWN, {**result, "verdict": "UNKNOWN", "reason": "produced_at missing"}
    if now - produced_at > max_age:
        return UNKNOWN, {
            **result,
            "verdict": "UNKNOWN",
            "reason": f"file is {now - produced_at:.0f}s old, older than max_age {max_age:.0f}s",
        }
    problems, exhausted, summary = check_accounts(data, now, max_age)
    result.update({"accounts": summary, "exhausted_windows": exhausted, "problems": problems})
    if problems:
        return RED, {**result, "verdict": "RED"}
    return GREEN, {**result, "verdict": "GREEN"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True, type=Path, help="site home holding engine/budget.json")
    parser.add_argument("--max-age", type=float, default=900.0)
    parser.add_argument("--now", type=float, default=None, help="epoch seconds (default: current time)")
    parser.add_argument("--file", type=Path, default=None, help="check this file instead of engine/budget.json")
    args = parser.parse_args(argv)
    now = args.now if args.now is not None else time.time()
    code, result = run(args.home, args.max_age, now, args.file)
    print(json.dumps(result, sort_keys=True, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
