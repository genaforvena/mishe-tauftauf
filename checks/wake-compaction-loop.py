#!/usr/bin/env python3
"""Detector: a RUNNING wake is overflowing its context and has not yet yielded.

``failed-mishe-wake-*`` only fires when a wake process exits, so a wake stuck in a compaction
loop (context overflows, the model stops on ``length``, compaction runs again) is invisible until
the engine cuts it.  The 2026-11-27 pub wake (16:53Z) overflowed three times and ran 51 minutes
before it yielded; no detector read its stream while it was overflowing.

This reads the tail of each wake stream written in the last ``ACTIVE_S`` seconds (still running)
and counts two things: turns that ended with ``stopReason: length`` and ``auto_compaction_start``
events with ``action: context-full``.  A wake with no ``mishe yield`` tool call in the tail and at
least ``MIN_OVERFLOWS`` overflow signals is RED, naming the wake, role and counts.  A wake that
yielded, or one that is not active, is never RED.  This only observes; it does not kill or change
any wake.

Exit: 0 GREEN, 2 RED (an active wake overflowed without yielding), 3 UNKNOWN (the wake directory
cannot be listed, or an active stream cannot be read).
"""
import argparse
import re
import sys
import time
from pathlib import Path

ACTIVE_S = 15 * 60
WINDOW_S = 2 * 3600
TAIL_BYTES = 4_000_000
MIN_OVERFLOWS = 2

YIELD_RE = re.compile(r'"command":"[^"]*mishe yield --(?:result|continue|again|when)')
LENGTH_TURN_RE = re.compile(r'^\{"type":"turn_end".*"stopReason":"length"')
COMPACT_RE = re.compile(r'^\{"type":"auto_compaction_start".*"action":"context-full"')


def _tail_text(path):
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            raw = fh.read(TAIL_BYTES)
            first = raw.find(b"\n")
            raw = raw[first + 1:] if first >= 0 else b""
        else:
            raw = fh.read(TAIL_BYTES)
    return raw.decode("utf-8", "replace")


def scan(path):
    """Return counts for one stream tail: (length_turns, compactions, yielded)."""
    length_turns = compactions = 0
    yielded = False
    for line in _tail_text(path).splitlines():
        if LENGTH_TURN_RE.match(line):
            length_turns += 1
        elif COMPACT_RE.match(line):
            compactions += 1
        if not yielded and YIELD_RE.search(line):
            yielded = True
    return length_turns, compactions, yielded


def role_of(name):
    # wake-<role>-<key>.prompt.out.jsonl
    parts = name.split(".")[0].split("-")
    return parts[1] if len(parts) > 2 else "?"


def evaluate(wakes_dir, now):
    """Return (exit_code, message)."""
    if not wakes_dir.is_dir():
        return 3, f"wake-compaction-loop: UNKNOWN no wake directory at {wakes_dir}"
    try:
        streams = list(wakes_dir.glob("wake-*.prompt.out.jsonl"))
    except OSError as exc:
        return 3, f"wake-compaction-loop: UNKNOWN wake directory unreadable ({exc.strerror})"
    active = []
    for path in streams:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        # abs(): a stream stamped a little ahead of the reader's clock is still active, not GREEN.
        if abs(now - mtime) <= ACTIVE_S:
            active.append(path)
    hits = []
    unreadable = []
    for path in active:
        try:
            length_turns, compactions, yielded = scan(path)
        except OSError as exc:
            unreadable.append(f"{path.name}: unreadable ({exc.strerror})")
            continue
        overflows = length_turns + compactions
        if not yielded and overflows >= MIN_OVERFLOWS:
            hits.append(f"{role_of(path.name)} {path.name.split('.')[0]}: {length_turns} length-stopped "
                        f"turn(s), {compactions} context-full compaction(s), no yield yet")
    if hits:
        msg = "wake-compaction-loop: RED " + str(len(hits)) + " active wake(s) overflowing without a yield -> " + "; ".join(hits)
        if unreadable:
            msg += "; unreadable: " + "; ".join(unreadable)
        return 2, msg
    if unreadable:
        return 3, "wake-compaction-loop: UNKNOWN active wake stream(s) unreadable: " + "; ".join(unreadable)
    return 0, (f"wake-compaction-loop: GREEN {len(active)} active wake(s) in the last "
               f"{ACTIVE_S // 60} min, none overflowing without a yield")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--now", type=float, default=None, help="epoch seconds (testing)")
    # --now is only for controlled tests; production reads the clock.
    a = ap.parse_args(argv)
    now = a.now if a.now is not None else time.time()
    code, message = evaluate(a.home / "wakes", now)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
