#!/usr/bin/env python3
"""Detector: wakes that did real work and were thrown away.

A wake that ends by timeout or without a yield, after a long stream of tool calls, loses what it learned:
no wall update, no yield, no result.  This reads the engine routing ledger (select/outcome events) and
the retained stream sizes, and says RED while such a wake is recent.  The message rolls the losses up
per candidate so the resolver can see *which* engine/model/account is the offender and how often it
wasted work, instead of an undifferentiated list.

Exit: 0 GREEN, 2 RED (a wake in the window ended in timeout/no-yield with real work), 3 UNKNOWN (ledger unreadable).
"""
import argparse
import calendar
import json
import sys
import time
from collections import defaultdict
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


def classify(ev):
    """Short cause class for a discarded wake: hard cap, no-output watchdog, or other."""
    reason = ev["reason"]
    if "hard cap" in reason:
        return "hard cap"
    if "no output" in reason:
        return "no output"
    if ev["outcome"].startswith("timeout"):
        return "timeout"
    return "no yield"


def evaluate(events, home, now):
    """Return (exit_code, message).  events is the decoded ledger; home locates the wake streams."""
    per_key = defaultdict(lambda: {"count": 0, "classes": defaultdict(int), "roles": set(),
                                   "bytes": 0, "worst_age_s": 0.0})
    for ev in events:
        if not ev or ev["event"] != "outcome" or ev["outcome"] == "yielded" or now - ev["at"] > WINDOW_S:
            continue
        stream = Path(home) / "wakes" / f"{ev['wake']}.prompt.out.jsonl"
        size = stream.stat().st_size if stream.is_file() else 0
        if not (ev["outcome"].startswith("timeout") or size >= MIN_STREAM_BYTES):
            continue
        slot = per_key[ev["key"]]
        slot["count"] += 1
        slot["classes"][classify(ev)] += 1
        slot["roles"].add(ev["role"])
        slot["bytes"] += size
        slot["worst_age_s"] = max(slot["worst_age_s"], now - ev["at"])
    if not per_key:
        return 0, "wake-waste: GREEN no timed-out or yield-less wake with real work in the last 2 h"
    ranked = sorted(per_key.items(), key=lambda kv: kv[1]["count"], reverse=True)
    parts = []
    for key, slot in ranked:
        classes = ", ".join(f"{n}x {c}" for c, n in sorted(slot["classes"].items(), key=lambda x: -x[1]))
        parts.append(f"{key}: {slot['count']} wake(s) lost ({classes}) for {','.join(sorted(slot['roles']))}, "
                     f"{slot['bytes'] / 1e6:.1f} MB, most recent {slot['worst_age_s'] / 60:.0f} min ago")
    total = sum(s["count"] for _, s in ranked)
    return 2, f"wake-waste: {total} wake(s) threw away work in the last 2 h -> " + "; ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    a = ap.parse_args(argv)
    ledger = a.home / "engine" / "routing-ledger.jsonl"
    if not ledger.is_file():
        print("wake-waste: no routing ledger; cannot tell", file=sys.stderr)
        return 3
    try:
        events = [decode(line) for line in ledger.read_text().splitlines()[-4000:]]
    except OSError as exc:
        print(f"wake-waste: ledger unreadable: {exc}", file=sys.stderr)
        return 3
    code, message = evaluate(events, a.home, time.time())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
