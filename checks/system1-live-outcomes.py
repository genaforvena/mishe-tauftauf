#!/usr/bin/env python3
"""Acceptance check: System One initiatives count only on real, live outcomes.

A caller in the source tree proves wiring, not use. These checks read the ledgers a live path writes:

``decision``   the commit-risk ledger (SITE/decision-log/commit-risk.jsonl) has a System One answer that
               was actually decided (not escalated or escaped), the newest one within 24 h, and the
               decision budget ledger (XDG state, ``decision-routerai-budget.json``) records credits spent
               on decisions. A shadow or unrun caller never passes.
``shadow``     the P0 shadow log has >= 50 wake starts carrying a System One answer (decision.choice);
               a row count alone never passes, because starts written with ``decision: null`` are not answers.
``wake-gate``  a wake gate that acts is live: SITE/decision-log/wake-gate-live.jsonl has a row with
               ``gate == "live"`` and ``action`` in {"skip", "wake"} within 24 h. The shadow log
               (wake-gate.jsonl, ``decision`` null, no action) never counts.

Exit 0 DONE, 1 OPEN (no live outcome yet), 3 UNKNOWN (a ledger is unreadable or invalid). A missing
ledger is OPEN, not UNKNOWN, because nothing has run yet; a corrupt one is UNKNOWN, never GREEN.
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

DONE, OPEN, UNKNOWN = 0, 1, 3
WINDOW_S = 24 * 3600
DECISION_LOG = "decision-log/commit-risk.jsonl"
WAKE_GATE_LIVE_LOG = "decision-log/wake-gate-live.jsonl"
BUDGET_NAME = "decision-routerai-budget.json"
SHADOW_LOG = "decision-log/wake-gate.jsonl"
SHADOW_MIN = 50


def _reject_constant(name):
    # Python's json accepts NaN/Infinity by default; they are not evidence, so the ledger is corrupt.
    raise ValueError(f"non-finite JSON constant {name}")


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
            row = json.loads(line, parse_constant=_reject_constant)
        except ValueError as exc:
            raise ValueError(f"{path.name} line {number} is not finite JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name} line {number} is not an object")
        rows.append(row)
    return rows


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _age(at, now):
    """Seconds since evidence stamped `at`. A stamp in the future is malformed evidence: ValueError."""
    age = now - at
    if age < 0:
        raise ValueError(f"evidence stamped in the future ({at!r} is after now)")
    return age


def decision_check(home, now, budget_path):
    """(code, detail) for the first System One live decision."""
    rows = _rows(home / DECISION_LOG)
    decided = [r for r in rows if r.get("source") == "system1" and r.get("reason") == "decided"
               and _number(r.get("at"))]
    if not decided:
        return OPEN, "no decided System One answer in the commit-risk ledger"
    # Every decided stamp must be a real past time; one future stamp makes the ledger untrustworthy.
    ages = [_age(r["at"], now) for r in decided]
    newest_age = min(ages)
    if newest_age > WINDOW_S:
        return OPEN, f"newest decided answer is {newest_age / 3600:.1f} h old"
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
    return DONE, f"{len(decided)} decided System One answers, newest {newest_age / 3600:.1f} h old, spent {spent:.4f}"


def wake_gate_check(home, now):
    """(code, detail) for a wake gate that has acted live."""
    rows = _rows(home / WAKE_GATE_LIVE_LOG)
    candidates = [r for r in rows if r.get("gate") == "live" and r.get("action") in ("skip", "wake")
                  and _number(r.get("at"))]
    live = [r for r in candidates if _age(r["at"], now) <= WINDOW_S]
    if not live:
        return OPEN, "no live wake-gate action in the last 24 h (the shadow log does not count)"
    return DONE, f"{len(live)} live wake-gate actions in the last 24 h"


def shadow_check(home):
    """(code, detail) for the P0 shadow log: 50 wake starts that carry a System One answer.

    A row count alone is not evidence: start rows written before any model answer exist have
    ``decision: null`` and must not count. Only a start row whose ``decision`` is an object with a
    non-empty ``choice`` string is a shadow answer.
    """
    rows = _rows(home / SHADOW_LOG)
    starts = [r for r in rows if r.get("kind") == "start"]
    answered = [r for r in starts if isinstance(r.get("decision"), dict)
                and isinstance(r["decision"].get("choice"), str) and r["decision"]["choice"]]
    detail = f"{len(answered)} of {len(starts)} wake starts carry a System One answer (need 50)"
    if len(answered) >= SHADOW_MIN:
        return DONE, detail
    return OPEN, detail


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--check", choices=("decision", "wake-gate", "shadow"), required=True)
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
        elif a.check == "shadow":
            code, detail = shadow_check(a.home)
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
