#!/usr/bin/env python3
"""Check that a stored OK reading is retired after its observation expires.

The timer check covers arming. This read-only check compares the stored state
with the freshness stamp and registered lifetime, without calling the expiry
sweep or the display reader. Allow five minutes for the due timer and retries.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import stat
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mishe_tauftauf.live_signal import OK, STALE, UNKNOWN  # noqa: E402
from mishe_tauftauf.signal_store import _check_name  # noqa: E402
from mishe_tauftauf.state_files import read_json  # noqa: E402

GRACE_S = 300.0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _stamp(path: Path) -> float | None:
    try:
        observed = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(observed.st_mode):
        raise ValueError("freshness stamp is not a regular file")
    return observed.st_mtime_ns / 1e9


def _overdue(signals: Path, name: str, entry, now: float) -> float | None:
    _check_name(name)
    max_age = entry.get("max_age") if isinstance(entry, dict) else None
    if not _number(max_age) or max_age <= 0:
        raise ValueError("missing or invalid max_age")
    path = signals / f"{name}.json"
    row = read_json(path)
    if row is None and not path.exists():
        registered = entry.get("registered_at")
        if (_stamp(signals / f"{name}.seen") is None and _number(registered)
                and now <= registered + max_age + GRACE_S):
            return None  # A newly registered monitor still has time to emit its first edge.
        raise ValueError("reading missing")
    state = row.get("state") if isinstance(row, dict) else None
    if state in (STALE, UNKNOWN):
        return None
    if state != OK:
        raise ValueError("reading has no recognized state")
    seen = _stamp(signals / f"{name}.seen")
    if seen is None:
        raise ValueError("OK reading has no freshness stamp")
    late = now - (seen + max_age)
    return late if late > GRACE_S else None


def evaluate(home: Path, now: float) -> tuple[int, str]:
    signals = home / "signals"
    try:
        registry = read_json(signals / "registry.json")
    except (OSError, ValueError) as exc:
        return 3, f"signal registry unreadable: {exc}"
    if not isinstance(registry, dict) or not registry:
        return 3, "signal registry missing, empty or malformed"

    overdue = []
    unreadable = []
    for name, entry in sorted(registry.items()):
        try:
            late = _overdue(signals, name, entry, now)
        except (OSError, OverflowError, ValueError) as exc:
            unreadable.append(f"{name}: {exc}")
            continue
        if late is not None:
            overdue.append(f"{name} ({late:.0f}s past expiry)")

    details = []
    if overdue:
        details.append(f"{len(overdue)} overdue OK: {', '.join(overdue[:5])}")
    if unreadable:
        details.append(f"{len(unreadable)} unreadable: {', '.join(unreadable[:5])}")
    if details:
        return (2 if overdue else 3), ". ".join(details)
    return 0, f"no overdue OK readings across {len(registry)} registrations"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    args = parser.parse_args()
    code, message = evaluate(args.home, time.time())
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
