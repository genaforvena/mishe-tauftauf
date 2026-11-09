#!/usr/bin/env python3
"""Detector: wakes that did real work and were thrown away.

A wake that ends by timeout or without a yield, after a long stream of tool calls, loses what it learned:
no wall update, no yield, no result.  This reads the engine routing ledger (select/outcome events) and
the retained stream sizes, and says RED while such a wake is recent, naming the role, model, outcome,
minutes and megabytes, so the resolver can fix the cause (timeout policy, scope of the wake, engine use).

Exit: 0 GREEN, 2 RED (a wake in the window ended in timeout/no-yield with real work), 3 UNKNOWN (ledger unreadable).
"""
import argparse
import calendar
import json
import sys
import time
from pathlib import Path


WINDOW_S = 2 * 3600
MIN_STREAM_BYTES = 1_000_000


def decode(line):
    try:
        ev = json.loads(line)
        at = calendar.timegm(time.strptime(ev["at"], "%Y-%m-%dT%H:%M:%SZ"))
        return {"event": ev["event"], "outcome": str(ev.get("outcome", "")), "reason": str(ev.get("reason", "")),
                "role": ev.get("role", "?"), "key": ev.get("key", "?"), "wake": ev.get("wake", ""), "at": at}
    except (ValueError, KeyError, TypeError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    a = ap.parse_args()
    ledger = a.home / "engine" / "routing-ledger.jsonl"
    if not ledger.is_file():
        print("wake-waste: no routing ledger; cannot tell", file=sys.stderr)
        return 3
    try:
        events = [decode(line) for line in ledger.read_text().splitlines()[-4000:]]
    except OSError as exc:
        print(f"wake-waste: ledger unreadable: {exc}", file=sys.stderr)
        return 3
    now = time.time()
    lost = []
    for ev in events:
        if not ev or ev["event"] != "outcome" or ev["outcome"] == "yielded" or now - ev["at"] > WINDOW_S:
            continue
        stream = a.home / "wakes" / f"{ev['wake']}.prompt.out.jsonl"
        size = stream.stat().st_size if stream.is_file() else 0
        if ev["outcome"].startswith("timeout") or size >= MIN_STREAM_BYTES:
            lost.append(f"{ev['role']} via {ev['key']}: {ev['outcome']} {ev['reason']} ({size / 1e6:.1f} MB of work, {(now - ev['at']) / 60:.0f} min ago)")
    if lost:
        print("wake-waste: work thrown away -> " + "; ".join(lost), file=sys.stderr)
        return 2
    print("wake-waste: GREEN no timed-out or yield-less wake with real work in the last 2 h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
