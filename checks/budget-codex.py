#!/usr/bin/env python3
"""Detector: the codex-a (openai-codex) allowance windows are measured and not spent.

engine/budget.json is the one budget source; its codex-a account carries the engine's own
5h and 7d windows. This check re-ages the snapshot and its FRESH windows at check time
and applies the same spent-window rule budget-opencode-go and provider_budget use
(LOW when used > 0.9). Missing, expired or malformed evidence is UNKNOWN. Burn ETAs
are not judged here: a burn_source of ``history`` with no
eta is the normal codex state, and ``window-elapsed`` is a projection, not evidence.

Verdict is a state word for the producer machinery: exit 0 GREEN, 2 RED, 3 UNKNOWN.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mishe_tauftauf import budget_producer, state_files

GREEN, RED, UNKNOWN = 0, 2, 3
ACCOUNT = "codex-a"
WINDOWS = ("5h", "7d")
USED_LOW = 0.9  # spent window (provider_budget's LOW rule)


def _num(value) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def evaluate(budget: dict, *, now: float) -> tuple[int, str]:
    """(exit code, one-line detail) for the codex-a account in a parsed budget.json."""
    if budget.get("schema") != budget_producer.SCHEMA:
        return UNKNOWN, f"{ACCOUNT}: engine/budget.json has an unsupported schema"
    produced_at = budget.get("produced_at")
    if not _num(produced_at) or not now - budget_producer.MAX_AGE_S <= produced_at <= now + 5:
        return UNKNOWN, f"{ACCOUNT}: engine/budget.json publication time expired or invalid"

    accounts = budget.get("accounts")
    account = accounts.get(ACCOUNT) if isinstance(accounts, dict) else None
    if not isinstance(account, dict):
        return UNKNOWN, f"{ACCOUNT} absent from engine/budget.json"
    windows = account.get("windows")
    if not isinstance(windows, dict):
        windows = {}
    parts, spent, unmeasured = [], False, False
    for name in WINDOWS:
        w = windows.get(name)
        if (not isinstance(w, dict) or w.get("state") != "FRESH"
                or not all(_num(w.get(field)) for field in ("used", "observed_at", "reset_at"))
                or not 0 <= w["used"] <= 1
                or not budget_producer.window_fresh_at(w["observed_at"], w["reset_at"], now)):
            parts.append(f"{name} unmeasured")
            unmeasured = True
            continue
        used = w["used"]
        parts.append(f"{name} {used * 100:.0f}% used")
        if used > USED_LOW:
            parts.append(f"{name} LOW (spent window)")
            spent = True
    # A measured spent window is a real reading: RED outranks an unmeasured sibling.
    verdict = RED if spent else UNKNOWN if unmeasured else GREEN
    head = f"{ACCOUNT}: " + "; ".join(parts)
    return verdict, head


def run(home: Path) -> tuple[int, str]:
    path = Path(home) / "engine" / "budget.json"
    try:
        budget = state_files.read_json(path)
    except (OSError, ValueError) as exc:
        return UNKNOWN, f"{ACCOUNT}: engine/budget.json unreadable ({exc.__class__.__name__})"
    if not isinstance(budget, dict):
        return UNKNOWN, f"{ACCOUNT}: engine/budget.json missing or not an object"
    return evaluate(budget, now=time.time())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True, help="site home")
    args = parser.parse_args(argv)
    code, detail = run(Path(args.home))
    verdict = {GREEN: "GREEN", RED: "RED", UNKNOWN: "UNKNOWN"}[code]
    print(f"budget-codex: {verdict}")
    print(detail)
    return code


if __name__ == "__main__":
    sys.exit(main())
