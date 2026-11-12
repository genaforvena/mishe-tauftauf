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
  - decision level (live file only): the pinned scheduler's admission (yielding.spare_budget, run in a fresh
    interpreter from runtime/current/src) disagrees with the admission rule recomputed from raw fields (FRESH account
    and windows, age 0..900 s and within 3600 s, reset ahead, floor met). No pin, or a pin that cannot be run, is UNKNOWN.

Exit status:
  0  GREEN: every account row matches its windows and no window is shown FRESH past its age
  2  RED: one of the checks above fails
  3  UNKNOWN: file missing, unreadable, not schema 1, produced_at older than --max-age, or no runnable pinned
     scheduler (a stale file or an unobserved decision cannot prove anything)

Usage: budget-consistency.py --home SITE_HOME [--max-age 900] [--now EPOCH] [--file PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
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


# Decision-level rule, written again here from the raw fields (not from budget_producer.window_fresh_at),
# so a regression in the shared freshness helper or in the scheduler's spare_budget cannot agree with
# itself. The scheduler applies two independent age bounds: its own evidence limit (BUDGET_MAX_AGE, 3600 s)
# and the producer's freshness rule (window_fresh_at, MAX_AGE_S, 900 s). Both are mirrored here and both
# must hold, so a window aged 901..3600 s is not admitted (DM 300163).
ADMIT_MAX_AGE_S = 3600.0
PRODUCER_MAX_AGE_S = 900.0
ADMIT_FLOORS = {"5h": 0.30, "7d": 0.30}


def raw_admission(data: dict, now: float) -> bool:
    """True when some account has FRESH state and both 5h and 7d windows FRESH, recent (0..900 s old, and
    within the 3600 s evidence limit), not yet reset and above the spare floor. Computed from raw fields only."""
    for acct in (data.get("accounts") or {}).values():
        if not isinstance(acct, dict) or acct.get("state") != "FRESH" or not isinstance(acct.get("windows"), dict):
            continue
        ok = True
        for name, floor in ADMIT_FLOORS.items():
            w = acct["windows"].get(name)
            if not isinstance(w, dict) or w.get("state") != "FRESH" or not all(_num(w.get(f)) for f in ("remaining", "reset_at", "observed_at")):
                ok = False
                break
            age = now - w["observed_at"]
            ok = (
                0 <= age <= ADMIT_MAX_AGE_S
                and age <= PRODUCER_MAX_AGE_S
                and w["reset_at"] > now
                and w["remaining"] >= floor
            )
            if not ok:
                break
        if ok:
            return True
    return False


# Run in a fresh interpreter (-I: no PYTHONPATH, no cwd on sys.path) so no cached module can stand in for the
# pinned bytes. The child prints the file it actually imported and the decision it actually made.
_PROBE = """
import json, sys
src, home, now = sys.argv[1], sys.argv[2], float(sys.argv[3])
sys.path.insert(0, src)
from mishe_tauftauf import yielding
print(json.dumps({"file": yielding.__file__, "decided": bool(yielding.spare_budget(home, now))}))
"""


def check_admission(home: Path, data: dict, now: float) -> tuple[list, dict]:
    """Compare the scheduler's admission decision (yielding.spare_budget, which reads home/engine/budget.json)
    with raw_admission on the same data. Only the pinned release (home/runtime/current/src) is judged: with no
    pin, or with a pinned scheduler that cannot be imported or run, the decision is not observed and the result
    is UNKNOWN. A disagreement is RED. The checkout src is never used as a stand-in for the pin."""
    home = Path(home).resolve()
    pinned_src = home / "runtime" / "current" / "src"
    if not pinned_src.is_dir():
        return [], {"available": False, "reason": f"no pinned scheduler at {pinned_src} (runtime/current/src missing)"}
    src = pinned_src.resolve()
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _PROBE, str(src), str(home), repr(float(now))],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], {"available": False, "reason": f"pinned scheduler probe did not run: {exc}", "scheduler_src": str(src)}
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return [], {"available": False, "reason": f"pinned scheduler not importable or raised: {tail[0]}", "scheduler_src": str(src)}
    try:
        probe = json.loads(proc.stdout)
        imported = Path(probe["file"]).resolve()
        decided = probe["decided"]
    except (ValueError, KeyError, TypeError):
        return [], {"available": False, "reason": "pinned scheduler probe printed no decision", "scheduler_src": str(src)}
    if not imported.is_relative_to(src) or not isinstance(decided, bool):
        return [], {"available": False, "reason": f"imported {imported}, not under the pin {src}", "scheduler_src": str(src)}
    raw = raw_admission(data, now)
    summary = {"available": True, "decided": decided, "raw": raw, "scheduler_src": str(src),
               "scheduler_file": str(imported)}
    if decided != raw:
        return [f"admission disagrees: scheduler spare_budget={decided}, raw fields={raw}"], summary
    return [], summary


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
    if path is None:
        # The scheduler reads home/engine/budget.json itself, so the decision is only comparable for the live file.
        adm_problems, admission = check_admission(home, data, now)
        problems = problems + adm_problems
        result["admission"] = admission
        if not admission.get("available"):
            result.update({"accounts": summary, "exhausted_windows": exhausted, "problems": problems})
            return UNKNOWN, {**result, "verdict": "UNKNOWN", "reason": admission["reason"]}
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
