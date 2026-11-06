"""Scope existing quota verdicts to the provider used by a mind.

This does not collect accounts, change limits or invent a burn forecast. It
consumes decoded rows from the site's existing budget view and preserves its
freshness/forecast verdicts. Missing evidence never admits spontaneous work.
"""
import math
from pathlib import Path

from . import signal_store


def evaluate(rows, account, *, now):
    if account != "claude":
        return "UNKNOWN", "provider window contract not configured"
    if not isinstance(rows, list) or any(not isinstance(r, dict) or r.get("error") for r in rows):
        return "UNKNOWN", "budget collection or accounting incomplete"
    selected = [r for r in rows if r.get("account") == account]
    if (len(selected) != 2 or any(r.get("window") not in ("5h", "7d") for r in selected)
            or {r["window"] for r in selected} != {"5h", "7d"}):
        return "UNKNOWN", "provider windows missing or duplicated"
    for row in selected:
        used, reset = row.get("used"), row.get("reset")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (used, reset)):
            return "UNKNOWN", "provider quota is malformed"
        try:
            valid = math.isfinite(used) and 0 <= used <= 1 and math.isfinite(reset) and reset > now
        except OverflowError:
            valid = False
        if not valid or row.get("state") != "FRESH":
            return "UNKNOWN", "provider quota is missing, stale or malformed"
        if row.get("verdict") not in ("ok", "LOW", "RUNS OUT BEFORE RESET"):
            return "UNKNOWN", "provider forecast is unavailable"
    if any(r["verdict"] != "ok" for r in selected):
        return "RED", "provider's existing quota or burn rule holds spontaneous work"
    return "GREEN", "both provider windows satisfy the existing budget policy"


def publish(home, rows, *, now):
    state, reason = evaluate(rows, "claude", now=now)
    name = "budget-claude"
    if name not in signal_store.registry(Path(home)):
        signal_store.register(Path(home), name, max_age=900, subscribers=[])
    signal_store.observe(Path(home), name, state, now=now, source="existing-budget-view:claude")
    return dict(state=state, reason=reason)
