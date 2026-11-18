#!/usr/bin/env python3
"""Detector: the root-drive migration bulk copy has finished, and whether it succeeded.

The copy is the remedy for the measured root-device throughput cliff (15 MiB/s
writes, 62 MiB/s reads on nvme0 against ~1.3 GB/s on nvme1n1) recorded in
docs/root-fs-migration-plan.md. It runs as the transient user unit
``mishe-root-migration-bulk`` (script ``bulk-copy.sh``) and writes a progress
marker per tree to ``bulk.log``:

    === mishe-tauftauf start 2026-11-17T07:53:47Z
    === mishe-tauftauf end rc=0 2026-11-17T08:01:58Z 30925374169 bytes copied
    ...
    === all done rc=0 2026-11-17T08:25:10Z

Completion is only knowable from that log, so this check makes it a signal
instead of a timed poll: a wake can then be scheduled on ``GREEN`` and the delta
pass happens when the copy is actually done, not when someone guesses.

Exit status:
  0  every tree finished and the pass ended with rc=0
  2  a tree ended rc != 0 — even if the final "all done" line reports 0 — or the
     log stopped advancing mid-pass: the copy was abandoned, which a partial
     tree must never be mistaken for completion
  3  undecidable or still running: no log yet, or a tree is in progress with a
     log that is still being written
"""
from __future__ import annotations

import argparse
import calendar
import re
import sys
import time
from pathlib import Path

TERMINAL = re.compile(r"^=== all done rc=(\d+) (\S+)$")
TREE_START = re.compile(r"^=== (\S+) start (\S+)$")
TREE_END = re.compile(r"^=== (\S+) end rc=(\d+) (\S+) (\d+) bytes copied$")
STALL_S = 1800


def parse(lines: list[str]) -> tuple[str, list[str], float]:
    """Return (verdict, detail lines, last marker time). last is 0.0 when unparsed."""
    done: dict[str, int] = {}
    failed: dict[str, int] = {}
    started: dict[str, str] = {}
    terminal: tuple[int, str] | None = None
    last = 0.0
    for line in lines:
        line = line.strip()
        if m := TERMINAL.match(line):
            terminal = (int(m.group(1)), m.group(2))
            last = _stamp(m.group(2))
        elif m := TREE_END.match(line):
            tree, rc = m.group(1), int(m.group(2))
            done[tree] = int(m.group(4))
            if rc:
                failed[tree] = rc
            else:
                failed.pop(tree, None)
            started.pop(tree, None)
            last = _stamp(m.group(3))
        elif m := TREE_START.match(line):
            started[m.group(1)] = m.group(2)
            last = _stamp(m.group(2))
    if terminal is not None:
        rc = terminal[0]
        summary = f"all trees done, {len(done)} recorded"
        if done:
            summary += " (" + ", ".join(f"{k} {v} B" for k, v in done.items()) + ")"
        if rc == 0 and not failed:
            return "ok", [summary], last
        if failed:
            trees = ", ".join(f"{k} rc={v}" for k, v in failed.items())
            return "fail", [f"tree(s) failed: {trees}; pass ended rc={rc}: {summary}"], last
        return "fail", [f"pass ended rc={rc}: {summary}"], last
    running = ", ".join(f"{k} since {v}" for k, v in started.items()) or "none"
    return "pending", [f"in progress ({len(done)} tree(s) done; {running}); no 'all done' marker yet"], last


def _stamp(text: str) -> float:
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    parser.add_argument("--log", default="/mnt/mishe-scratch/mishe-migration/bulk.log")
    args = parser.parse_args(argv)
    log = Path(args.log)
    try:
        text = log.read_text(errors="replace")
    except OSError as exc:
        print(f"UNKNOWN: cannot read {log} ({exc.strerror or exc})")
        return 3
    verdict, detail, last = parse(text.splitlines())
    detail = "; ".join(detail)
    if verdict == "ok":
        print(f"OK: {detail}")
        return 0
    if verdict == "fail":
        print(f"FAIL: {detail}")
        return 2
    age = time.time() - last if last else None
    if age is not None and age > STALL_S:
        print(f"FAIL: copy stopped advancing {int(age)}s ago, {detail}")
        return 2
    print(f"UNKNOWN: {detail}")
    return 3


if __name__ == "__main__":
    sys.exit(main())
