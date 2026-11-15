#!/usr/bin/env python3
"""Acceptance check: System One initiatives count only on real, live outcomes.

A caller in the source tree proves wiring, not use. These checks read the ledgers a live path writes:

``decision``   the commit-risk ledger (SITE/decision-log/commit-risk.jsonl) has a System One answer that
               was actually decided (not escalated or escaped), the newest one within 24 h, and the
               decision budget ledger (XDG state, ``decision-routerai-budget.json``) records credits spent
               on decisions. A shadow or unrun caller never passes.
``wake-gate``  a wake gate that acts is live: SITE/decision-log/wake-gate-live.jsonl has a row with
               ``gate == "live"`` and ``action`` in {"skip", "wake"} within 24 h. The shadow log
               (wake-gate.jsonl, ``decision`` null, no action) never counts.

Exit 0 DONE, 1 OPEN (no live outcome yet), 3 UNKNOWN (a ledger is unreadable or invalid). A missing
ledger is OPEN, not UNKNOWN, because nothing has run yet; a corrupt one is UNKNOWN, never GREEN.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

DONE, OPEN, UNKNOWN = 0, 1, 3
WINDOW_S = 24 * 3600
DECISION_LOG = "decision-log/commit-risk.jsonl"
WAKE_GATE_LIVE_LOG = "decision-log/wake-gate-live.jsonl"
BUDGET_NAME = "decision-routerai-budget.json"


def _rows(path):
    """Parsed JSON rows, [] when the file is missing; raises ValueError when it is corrupt."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path.name} line {number} is not JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name} line {number} is not an object")
        rows.append(row)
    return rows


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def decision_check(home, now, budget_path):
    """(code, detail) for the first System One live decision."""
    rows = _rows(home / DECISION_LOG)
    decided = [r for r in rows if r.get("source") == "system1" and r.get("reason") == "decided"
               and _number(r.get("at"))]
    if not decided:
        return OPEN, "no decided System One answer in the commit-risk ledger"
    newest = max(r["at"] for r in decided)
    if now - newest > WINDOW_S:
        return OPEN, f"newest decided answer is {(now - newest) / 3600:.1f} h old"
    try:
        budget_raw = json.loads(Path(budget_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return OPEN, "no decision budget ledger; nothing has been spent"
    except (OSError, ValueError):
        return UNKNOWN, "decision budget ledger is unreadable"
    spent = budget_raw.get("spent") if isinstance(budget_raw, dict) else None
    if not _number(spent):
        return UNKNOWN, "decision budget ledger has no numeric spent"
    if spent <= 0:
        return OPEN, "decision budget shows no spend"
    return DONE, f"{len(decided)} decided System One answers, newest {(now - newest) / 3600:.1f} h old, spent {spent:.4f}"


def wake_gate_check(home, now):
    """(code, detail) for a wake gate that has acted live."""
    rows = _rows(home / WAKE_GATE_LIVE_LOG)
    live = [r for r in rows if r.get("gate") == "live" and r.get("action") in ("skip", "wake")
            and _number(r.get("at")) and now - r["at"] <= WINDOW_S]
    if not live:
        return OPEN, "no live wake-gate action in the last 24 h (the shadow log does not count)"
    return DONE, f"{len(live)} live wake-gate actions in the last 24 h"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--check", choices=("decision", "wake-gate"), required=True)
    ap.add_argument("--budget", type=Path, default=None,
                    help="decision budget ledger (default: XDG state dir, decision-routerai-budget.json)")
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args(argv)
    now = time.time() if a.now is None else a.now
    try:
        if a.check == "decision":
            state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
            budget = a.budget or state / "mishe-tauftauf" / BUDGET_NAME
            code, detail = decision_check(a.home, now, budget)
        else:
            code, detail = wake_gate_check(a.home, now)
    except (OSError, ValueError) as exc:
        print(f"system1-live[{a.check}]: UNKNOWN {exc}", file=sys.stderr)
        return UNKNOWN
    print(f"system1-live[{a.check}]: {'DONE' if code == DONE else 'OPEN'} {detail}",
          file=sys.stderr if code != DONE else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
