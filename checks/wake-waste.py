#!/usr/bin/env python3
"""Detector: wakes that did real work and were thrown away.

A wake that ends by timeout or without a yield, after a long stream of tool calls, loses what it learned:
no wall update, no yield, no result.  This reads the engine routing ledger (select/outcome events) and
the retained stream sizes, and says RED while such a wake is recent.  The message rolls the losses up
per candidate so the resolver can see *which* engine/model/account is the offender and how often it
wasted work. Known terminal OpenCode Go errors add a reason to the no-yield count.

Exit: 0 GREEN, 2 RED (a wake in the window ended in timeout/no-yield with real work),
3 UNKNOWN (unreadable ledger or no-yield stream needed to measure work).
"""
import argparse
import calendar
import json
import stat
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mishe_tauftauf.router_text import opencode_go_terminal_error


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


def stream_size(home, wake):
    """Locate the wake's retained engine output and measure its size."""
    if not isinstance(wake, str) or not wake or Path(wake).name != wake:
        raise FileNotFoundError("missing or invalid wake key")
    wakes = Path(home) / "wakes"
    for suffix in (".jsonl", ".json"):
        stream = wakes / f"{wake}.prompt.out{suffix}"
        try:
            info = stream.stat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            return stream, info.st_size
    raise FileNotFoundError("no retained engine output")


def evaluate(events, home, now):
    """Return (exit_code, message).  events is the decoded ledger; home locates the wake streams."""
    per_key = defaultdict(lambda: {"count": 0, "classes": defaultdict(int), "roles": set(),
                                   "bytes": 0, "newest_at": 0.0})
    unavailable = []
    for ev in events:
        if not ev or ev["event"] != "outcome" or ev["outcome"] == "yielded" or not 0 <= now - ev["at"] <= WINDOW_S:
            continue
        timed_out = ev["outcome"].startswith("timeout")
        try:
            stream, size = stream_size(home, ev["wake"])
        except OSError:
            if not timed_out:
                unavailable.append(f"{ev['role']} {ev['wake']}: stream unavailable")
                continue
            stream, size = None, 0
        if not timed_out and size < MIN_STREAM_BYTES:
            continue
        cause = classify(ev)
        key = ev["key"]
        if cause == "no yield" and isinstance(key, str) and key.startswith("omp:opencode-go/"):
            try:
                provider_error = opencode_go_terminal_error(stream)
            except OSError:
                provider_error = None
                unavailable.append(f"{ev['role']} {ev['wake']}: provider detail unavailable")
            if provider_error:
                cause = f"{cause}: {provider_error}"
        slot = per_key[key]
        slot["count"] += 1
        slot["classes"][cause] += 1
        slot["roles"].add(ev["role"])
        slot["bytes"] += size
        slot["newest_at"] = max(slot["newest_at"], ev["at"])
    missing_detail = "; unclassified: " + "; ".join(unavailable) if unavailable else ""
    if not per_key:
        if unavailable:
            return 3, "wake-waste: UNKNOWN" + missing_detail
        return 0, "wake-waste: GREEN no timed-out or yield-less wake with real work in the last 2 h"
    ranked = sorted(per_key.items(), key=lambda kv: kv[1]["count"], reverse=True)
    parts = []
    for key, slot in ranked:
        classes = ", ".join(f"{n}x {c}" for c, n in sorted(slot["classes"].items(), key=lambda x: -x[1]))
        parts.append(f"{key}: {slot['count']} wake(s) lost ({classes}) for {','.join(sorted(slot['roles']))}, "
                     f"{slot['bytes'] / 1e6:.1f} MB, most recent {(now - slot['newest_at']) / 60:.0f} min ago")
    total = sum(s["count"] for _, s in ranked)
    return 2, f"wake-waste: {total} wake(s) threw away work in the last 2 h -> " + "; ".join(parts) + missing_detail


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
    except (OSError, UnicodeError) as exc:
        print(f"wake-waste: ledger unreadable: {exc}", file=sys.stderr)
        return 3
    code, message = evaluate(events, a.home, time.time())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
