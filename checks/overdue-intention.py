#!/usr/bin/env python3
"""Detector: a due intention is served by a wake, or is an intentional budget hold; otherwise it is RED.

Observed 2026-11-12: docs idle-initiative was due at 09:20Z and still unserved at 10:40Z. owed.check held it because
engines/docs.json selects budget-claude, which was UNKNOWN. A held intention writes no record, and next-due-armed only
proves that a timer exists, so nothing reported the lateness.

For every intention more than --grace-minutes past its ``due`` time:
  * a wake of that role started at or after the due time: served (no finding);
  * a spontaneous intention whose role's engine budget signal is not GREEN: an intentional budget hold. A hold on a
    signal that is itself RED (the provider quota holds work) is HELD (reported, not a fault). A hold on an UNKNOWN,
    stale or unreadable budget signal is UNKNOWN, because missing evidence must not look like a deliberate hold;
  * a delivery or operator hold in force now (``home/hold`` or ``work/delivery-admission.json``) that blocks every wake:
    HELD, naming the hold. Observed 2026-11-12: the steward intention went RED at 14:04:32Z while a moving-pin delivery
    hold stood; the wake started the second the hold was released, so the RED named the wrong cause;
  * anything else (no wake, no hold explanation): RED. A GREEN budget that did not wake the role is a routing fault.

Only stdlib is used, so the check runs from committed HEAD bytes without importing the pinned release.

Exit status:
  0  no intention is overdue, or every overdue one is served or intentionally HELD
  2  an overdue intention has no wake and no budget-hold explanation (RED)
  3  undecidable: an intention, the wake ledger, an engine config or a budget signal is unreadable or stale (UNKNOWN)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

GRACE_S = 900.0
SIGNAL_FRESH_S = 900.0
DEFAULT_BUDGET_SIGNAL = "budget"


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_intentions(home: Path):
    """Return (entries, problems). Each entry is (role, intention dict). Unreadable files are problems, never skipped."""
    entries, problems = [], []
    root = home / "intentions"
    if not root.is_dir():
        # A missing root is missing evidence, not an empty schedule.
        problems.append("intentions root missing or not a directory")
        return entries, problems
    for role_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(role_dir.glob("*.json")):
            try:
                item = _read_json(path)
            except (OSError, ValueError) as exc:
                problems.append(f"{role_dir.name}/{path.name} unreadable: {str(exc)[:100]}")
                continue
            if isinstance(item, dict) and item.get("id"):
                due = item.get("due")
                if due is not None and (isinstance(due, bool) or not isinstance(due, (int, float))):
                    problems.append(f"{role_dir.name}/{path.name} has non-numeric due {str(due)[:40]!r}")
                entries.append((role_dir.name, item))
            else:
                problems.append(f"{role_dir.name}/{path.name} has no id")
    return entries, problems


def _seen_time(home: Path, name: str):
    """mtime of signals/NAME.seen, the freshness stamp; None when it was never touched."""
    try:
        return (home / "signals" / f"{name}.seen").stat().st_mtime
    except OSError:
        return None


def _budget_state(home: Path, role: str, now: float):
    """(state, value, reason) of the role's budget signal; state is OK, RED or UNKNOWN."""
    try:
        config = _read_json(home / "engines" / f"{role}.json")
    except (OSError, ValueError) as exc:
        return "UNKNOWN", None, f"engine config for {role} unreadable: {str(exc)[:80]}"
    name = config.get("budget_signal", DEFAULT_BUDGET_SIGNAL) if isinstance(config, dict) else None
    if not isinstance(name, str) or not name:
        return "UNKNOWN", None, f"engine config for {role} names no budget signal"
    try:
        sig = _read_json(home / "signals" / f"{name}.json")
    except (OSError, ValueError) as exc:
        return "UNKNOWN", None, f"budget signal {name} unreadable: {str(exc)[:80]}"
    if not isinstance(sig, dict):
        return "UNKNOWN", None, f"budget signal {name} is not a JSON object"
    # Freshness is the .seen stamp (signal_store touches it on every observation). observed_at only moves on
    # a value edge, so a steady RED would read stale here while the producer keeps re-observing it.
    seen = _seen_time(home, name)
    if seen is None or now - seen > SIGNAL_FRESH_S:
        return "UNKNOWN", None, f"budget signal {name} stale or has no observation time"
    state = sig.get("state")
    if state == "OK" and sig.get("value") == "GREEN":
        return "OK", "GREEN", f"budget signal {name} GREEN"
    if state == "RED" or (state == "OK" and sig.get("value") == "RED"):
        return "RED", "RED", f"budget signal {name} RED"
    return "UNKNOWN", None, f"budget signal {name} is {state}"


def _hold_reason(home: Path):
    """Name the hold that blocks wakes right now, or None. A present hold file is a fact even if unreadable."""
    hold = home / "hold"
    if hold.exists():
        try:
            text = hold.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            text = ""
        return f"hold ({text[:120]})" if text else "hold (reason unreadable)"
    if (home / "work" / "delivery-admission.json").exists():
        return "delivery admission present"
    return None


def evaluate(home: Path, now: float, grace: float = GRACE_S):
    entries, problems = _load_intentions(home)
    try:
        ledger = _read_json(home / "wake-start-ledger.json")
        starts = ledger if isinstance(ledger, dict) else None
    except (OSError, ValueError) as exc:
        starts = None
        problems.append(f"wake-start-ledger.json unreadable: {str(exc)[:100]}")
    overdue = [(role, item) for role, item in entries
               if isinstance(item.get("due"), (int, float)) and now - item["due"] > grace]
    if starts is None and overdue:
        problems.append("wake start ledger unavailable, so overdue intentions cannot be shown as served")
    red, held, unknown, served = [], [], [], []
    for role, item in overdue:
        due = item["due"]
        late = int((now - due) // 60)
        if starts is None:
            unknown.append(f"{role}/{item['id']} overdue {late} min, wake history unreadable so service is undecided")
            continue
        started = starts.get(role)
        if isinstance(started, (int, float)) and started >= due:
            served.append(f"{role}/{item['id']} served by a wake {int((started - due) // 60)} min after due")
            continue
        blocked = _hold_reason(home)
        if blocked:
            held.append(f"{role}/{item['id']} overdue {late} min, wakes held: {blocked}")
            continue
        if item.get("spontaneous"):
            state, _, reason = _budget_state(home, role, now)
            if state == "OK":
                red.append(f"{role}/{item['id']} overdue {late} min, not woken although {reason}")
            elif state == "RED":
                held.append(f"{role}/{item['id']} overdue {late} min, intentionally held: {reason}")
            else:
                unknown.append(f"{role}/{item['id']} overdue {late} min, held by UNKNOWN evidence: {reason}")
        else:
            red.append(f"{role}/{item['id']} overdue {late} min with no wake and no hold")
    lines = []
    lines += [f"RED: {r}" for r in red]
    lines += [f"UNKNOWN: {u}" for u in unknown] + [f"UNKNOWN: {p}" for p in problems]
    lines += [f"HELD: {h}" for h in held] + [f"served: {s}" for s in served]
    if red:
        return 2, lines
    if unknown or problems:
        return 3, lines
    return 0, lines or [f"no intention overdue by more than {int(grace // 60)} min"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--grace-minutes", type=float, default=GRACE_S / 60)
    args = parser.parse_args(argv)
    code, lines = evaluate(args.home, time.time(), args.grace_minutes * 60)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
