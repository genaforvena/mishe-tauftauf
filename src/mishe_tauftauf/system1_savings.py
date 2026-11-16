"""System One savings signal (P4): what the wake path costs and what a gate would save.

A System 0 producer. It reads only the ledgers the wake path already keeps and calls no
provider, so a missing decision service cannot stop it:

- ``decision-log/wake-gate.jsonl``: one ``start`` row per wake (trigger class, the System 0
  ``same_dossier_as_last`` verdict) and one ``end`` row (the yielded result).
- ``decision-log/wake-gate-live.jsonl``: rows a live gate wrote (``action`` skip or wake).
  Absent means no gate is live, so avoided wakes are 0 and the gate reads ``off``.
- ``engine/budget.json`` under ``routerai.decision``: calls and spend of the decision budget.

Over the trailing 24 h it reports wake starts per day by trigger class, wakes avoided by a
live gate, the projected skip of same-dossier starts (what a gate would skip, not what it
skipped), the changed-rate of those projected skips against the changed-rate of all wakes,
and the decision calls and spend. Exit 0 measured (GREEN), 3 UNKNOWN when a ledger is
corrupt or the budget is unreadable. A missing wake-gate ledger is UNKNOWN too: nothing
has been measured, so the signal must not read GREEN.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from . import state_files

GREEN, UNKNOWN = 0, 3
WINDOW_S = 24 * 3600
BUDGET_STALE_S = 6 * 3600
WAKE_LOG = "decision-log/wake-gate.jsonl"
LIVE_LOG = "decision-log/wake-gate-live.jsonl"


def _trigger_class(trigger) -> str:
    """Bucket a trigger by its prefix (signal, intention, inbox, ...) so cardinality stays bounded."""
    text = str(trigger or "none")
    return text.split(":", 1)[0] or "none"


def _rate(num: int, den: int):
    return None if den == 0 else round(num / den, 4)


def measure(home, *, now: float) -> dict:
    """The savings numbers for the trailing window. Raises ValueError when a ledger is unusable."""
    home = Path(home)
    wake_path = home / WAKE_LOG
    if not wake_path.exists():
        raise ValueError("wake-gate ledger missing: nothing measured yet")
    rows = state_files.read_json_lines(wake_path)
    since = now - WINDOW_S
    starts = {}
    ends = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "start" and float(row.get("ts", 0)) >= since:
            starts[row.get("key")] = row
        elif row.get("kind") == "end":
            ends[row.get("key")] = row

    by_trigger = Counter(_trigger_class(r.get("trigger")) for r in starts.values())
    ended = [(s, ends[k]) for k, s in starts.items() if k in ends]
    results = Counter(e.get("result") or "none" for _, e in ended)
    same = [(s, e) for s, e in ended if s.get("same_dossier_as_last") is True]
    changed_all = sum(1 for _, e in ended if e.get("result") == "changed")
    changed_same = sum(1 for _, e in same if e.get("result") == "changed")

    live_path = home / LIVE_LOG
    live_rows = state_files.read_json_lines(live_path) if live_path.exists() else []
    live = [r for r in live_rows if isinstance(r, dict) and float(r.get("ts", 0)) >= since]
    skipped = sum(1 for r in live if r.get("action") == "skip")

    budget = _budget(home, now)
    return {
        "window_h": WINDOW_S // 3600,
        "starts": len(starts),
        "starts_by_trigger": dict(sorted(by_trigger.items())),
        "ended": len(ended),
        "results": dict(sorted(results.items())),
        "gate": "live" if live_path.exists() else "off",
        "avoided": skipped,
        "projected_skip": len(same),
        "changed_rate_all": _rate(changed_all, len(ended)),
        "changed_rate_projected_skip": _rate(changed_same, len(same)),
        "decision": budget,
    }


def _budget(home: Path, now: float) -> dict:
    budget = state_files.read_json(home / "engine" / "budget.json")
    if not isinstance(budget, dict):
        raise ValueError("engine/budget.json missing or not an object")
    decision = (budget.get("routerai") or {}).get("decision")
    if not isinstance(decision, dict):
        raise ValueError("routerai.decision missing from engine/budget.json")
    observed = float(decision.get("observed_at") or 0)
    if observed <= 0 or now - observed > BUDGET_STALE_S:
        raise ValueError("decision budget is stale or unobserved")
    return {
        "calls_today": decision.get("calls_today"),
        "daily_cap": decision.get("daily_cap"),
        "spent": decision.get("spent"),
        "spend_limit": decision.get("spend_limit"),
    }


def duty(home, *, now: float | None = None) -> tuple[int, str]:
    """The duty verdict: exit code and one line. Never raises."""
    now = time.time() if now is None else now
    try:
        numbers = measure(home, now=now)
    except (ValueError, OSError) as exc:
        return UNKNOWN, f"UNKNOWN: {exc}"
    return GREEN, "GREEN: " + json.dumps(numbers, sort_keys=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True)
    parser.add_argument("--repo", default=None, help="accepted for the duty command shape; unused")
    args = parser.parse_args(argv)
    code, line = duty(args.home)
    print(f"mishe: system1-savings: {line}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
