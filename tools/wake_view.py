#!/usr/bin/env python3
"""Wake-time report: where engine wall time goes across retained wakes.

Companion to ``delivery_view.py`` (which measures checked-to-live time). This
reads the engine routing ledger's ``select`` / ``outcome`` events and pairs them
by wake id, so the wake half of the standing question is a retained view rather
than an ad-hoc script every wake rewrites.

Only outcomes count as finished wakes: a ``select`` with no ``outcome`` is a
wake still in flight, not a zero-length one. A duration is measured from the
*first* select for that wake (a fall-through retries candidates) to its outcome.
An outcome whose select is absent from the retained tail is UNKNOWN, never zero:
missing evidence stays missing. Outcome classes match ``checks/wake-waste.py``
(hard cap, no output, timeout, no yield) so the two detectors agree.

Output is plain text; ``--json`` emits the aggregate. UNKNOWN is printed when
the ledger is absent or the window holds no outcomes.
"""
from __future__ import annotations

import argparse
import calendar
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

DEFAULT_HOME = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME")
                    or ".mishe-tauftauf")
DEFAULT_WINDOW_S = 7 * 86400.0
LEDGER_TAIL_LINES = 40000
LEDGER_READ_BLOCK = 64 * 1024


def decode(line: str) -> dict | None:
    """One decoded ledger event, or None for a torn/malformed line."""
    try:
        ev = json.loads(line)
        at = calendar.timegm(time.strptime(ev["at"], "%Y-%m-%dT%H:%M:%SZ"))
        return {"event": ev["event"], "outcome": str(ev.get("outcome", "")),
                "reason": str(ev.get("reason", "")), "role": ev.get("role", "?"),
                "key": ev.get("key", "?"), "wake": str(ev.get("wake", "")), "at": at}
    except (ValueError, KeyError, TypeError):
        return None


def load_events(home: Path) -> list[dict] | None:
    """Decode the recent ledger lines without scanning older history."""
    ledger = Path(home) / "engine" / "routing-ledger.jsonl"
    try:
        with ledger.open("rb") as handle:
            position = handle.seek(0, os.SEEK_END)
            blocks = []
            newlines = 0
            # Count one extra newline to exclude a partial first line even
            # when the final line ends in a newline.
            while position and newlines <= LEDGER_TAIL_LINES:
                size = min(position, LEDGER_READ_BLOCK)
                position -= size
                handle.seek(position)
                block = handle.read(size)
                if len(block) != size:
                    return None
                blocks.append(block)
                newlines += block.count(b"\n")
        lines = b"".join(reversed(blocks)).splitlines()[-LEDGER_TAIL_LINES:]
        return [e for line in lines if (e := decode(line.decode("utf-8"))) is not None]
    except (OSError, UnicodeError):
        return None

def first_wake_starts(events: list[dict]) -> dict[str, float]:
    """First recorded select per wake, including wakes with no recorded outcome."""
    starts = {}
    for ev in events:
        wake, at = ev.get("wake"), ev.get("at")
        if ev.get("event") == "select" and wake and type(at) in (int, float):
            if wake not in starts or at < starts[wake]:
                starts[wake] = at
    return starts


def wake_intervals(events: list[dict]) -> list[tuple[float, float]]:
    """[select, outcome] spans for wakes whose ledger has both ends.

    A retained tail can begin mid-wake, so a wake without a select or outcome is
    absent, never a zero-length interval. The shared dispatch lock a wake holds
    runs from its first select to its outcome.
    """
    starts = first_wake_starts(events)
    ends = {}
    for ev in events:
        wake, at = ev.get("wake"), ev.get("at")
        if ev.get("event") == "outcome" and wake and type(at) in (int, float):
            if wake not in ends or at > ends[wake]:
                ends[wake] = at
    return [(at, ends[wake]) for wake, at in starts.items()
            if wake in ends and ends[wake] >= at]


def interval_coverage(intervals: list[tuple[float, float]], start: float, end: float) -> float | None:
    """Fraction of [start, end] inside at least one interval; None for a torn span."""
    if type(start) not in (int, float) or type(end) not in (int, float) or end <= start:
        return None
    merged = []
    for a, b in sorted((max(a, start), min(b, end)) for a, b in intervals if b > start and a < end):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return sum(b - a for a, b in merged) / (end - start)


def classify(outcome: str, reason: str) -> str:
    """Cause class of a finished wake, aligned with checks/wake-waste.py:classify."""
    if "hard cap" in reason.lower():
        return "hard cap"
    if "no output" in reason.lower():
        return "no output"
    if outcome.startswith("timeout"):
        return "timeout"
    return "yielded" if outcome == "yielded" else "no yield"


def summarize(values: list[float]) -> dict[str, float | int]:
    """Mean / nearest-rank p50,p90 / max over measured seconds; empty for no sample."""
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    rank = lambda q: ordered[min(n - 1, math.ceil(n * q) - 1)]
    return {"n": n, "mean": sum(ordered) / n, "p50": rank(0.5), "p90": rank(0.9),
            "max": ordered[-1]}


def evaluate(events: list[dict], now: float, window_s: float) -> dict:
    """Aggregate finished wakes whose outcome falls in the window.

    Selects are read from the whole retained tail: a wake that started just
    before the window still has a measured duration when its outcome is inside.
    """
    starts = first_wake_starts(events)
    outcomes = [ev for ev in events if ev["event"] == "outcome"
                and now - window_s <= ev["at"] <= now + 5]
    rows = []
    for ev in outcomes:
        start = starts.get(ev["wake"])
        duration = None if start is None or start > ev["at"] else ev["at"] - start
        rows.append({"wake": ev["wake"], "role": ev["role"], "key": ev["key"],
                     "at": ev["at"], "outcome": ev["outcome"], "reason": ev["reason"],
                     "class": classify(ev["outcome"], ev["reason"]), "duration_s": duration})
    measured = [r["duration_s"] for r in rows if r["duration_s"] is not None]
    by_role: dict[str, list[float]] = defaultdict(list)
    by_key: dict[str, list[float]] = defaultdict(list)
    by_class: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r["duration_s"] is not None:
            by_role[r["role"]].append(r["duration_s"])
            by_key[r["key"]].append(r["duration_s"])
            by_class[r["class"]].append(r["duration_s"])
    finished = {ev["wake"] for ev in events if ev["event"] == "outcome"}
    in_flight = len({w for w, at in starts.items()
                     if w not in finished and now - window_s <= at <= now + 5})
    return {"outcomes": len(rows), "durations": summarize(measured),
            "unpaired": len(rows) - len(measured), "in_flight": in_flight,
            "by_role": {k: summarize(v) for k, v in by_role.items()},
            "by_key": {k: summarize(v) for k, v in by_key.items()},
            "by_class": {k: summarize(v) for k, v in by_class.items()},
            "rows": sorted(rows, key=lambda r: r["at"], reverse=True)}


def fmt_s(seconds: float | None) -> str:
    if seconds is None:
        return "UNKNOWN"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def render(home: Path, window_s: float, now: float,
           events: list[dict] | None = None, s: dict | None = None) -> str:
    if events is None:
        events = load_events(home)
    if events is None:
        return f"wake-view: UNKNOWN: no readable routing ledger under {home}/engine"
    if s is None:
        s = evaluate(events, now, window_s)
    if not s["outcomes"]:
        return "wake-view: UNKNOWN: no wake outcomes in window"
    stamp = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(now))
    d = s["durations"]
    lines = [f"Wake-time report  {stamp}",
             f"window: last {int(window_s)} s of outcomes",
             f"finished wakes: {s['outcomes']}; durations measured {d.get('n', 0)}, "
             f"UNKNOWN {s['unpaired']}; in flight {s['in_flight']}"]
    if d:
        lines.append(f"  wake duration  mean {fmt_s(d['mean'])}  p50 {fmt_s(d['p50'])}  "
                     f"p90 {fmt_s(d['p90'])}  max {fmt_s(d['max'])}  (n={d['n']})")
    else:
        lines.append("  wake duration  UNKNOWN (no paired select/outcome in the retained ledger)")
    for label, field in (("wake-seconds by role", "by_role"), ("wake-seconds by outcome class", "by_class")):
        parts = [f"{k} {fmt_s(v['mean'])} mean/{fmt_s(v['max'])} max ({v['n']})"
                 for k, v in sorted(s[field].items(), key=lambda kv: -kv[1]["n"])]
        lines.append(f"  {label}: " + (", ".join(parts) if parts else "UNKNOWN"))
    keys = sorted(s["by_key"].items(), key=lambda kv: -(kv[1]["mean"] * kv[1]["n"]))[:6]
    lines.append("  wake-seconds by candidate: " + (", ".join(
        f"{k} {fmt_s(v['mean'])} mean ({v['n']})" for k, v in keys) if keys else "UNKNOWN"))
    longest = sorted((r for r in s["rows"] if r["duration_s"] is not None),
                     key=lambda r: -r["duration_s"])[:5]
    if longest:
        lines.append("  longest finished wakes: " + "; ".join(
            f"{fmt_s(r['duration_s'])} {r['role']} {r['class']} {r['key']}" for r in longest))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, default=DEFAULT_HOME)
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW_S)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    events = load_events(a.home)
    if events is None:
        print("wake-view: UNKNOWN: no readable routing ledger", file=sys.stderr)
        return 3
    now = time.time()
    s = evaluate(events, now, a.window)
    if a.json:
        print(json.dumps({k: v for k, v in s.items() if k != "rows"}, sort_keys=True))
    else:
        print(render(a.home, a.window, now, events, s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
