#!/usr/bin/env python3
"""Detector: the outcome of the newest pin promotion (``mishe release advance``), as a signal.

Reads the newest record under SITE/work/releases (written by release_promotion.promote) and the live pointer.

  GREEN    delivered, and runtime/current names that release; or a promotion still running and younger than --stuck-min
  RED      the newest promotion ended in check-failed, check-mutated-*, rolled-back or UNKNOWN: names the sha, the
           phase, the failure and the last failing line of the check, so the resolver fixes a cause, not a symptom
  UNKNOWN  no promotion record, an unreadable record, a pointer that disagrees with a delivered record, or a
           promotion that has run longer than --stuck-min

Exit: 0 GREEN, 2 RED, 3 UNKNOWN. GREEN prints to stdout, the others to stderr.
"""
import argparse
import json
import sys
import time
from pathlib import Path

FAILED = {"check-failed", "check-mutated-source", "check-mutated-command", "rolled-back", "UNKNOWN"}
RUNNING = {"checking"}


def newest(directory):
    best = None
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("created_at"), (int, float)):
            if best is None or record["created_at"] > best["created_at"]:
                best = record
    return best


def pointer_sha(home):
    link = home / "runtime" / "current"
    return link.resolve().name if link.is_symlink() else None


def last_failing_line(record):
    out = (record.get("check") or {}).get("output")
    if not isinstance(out, str):
        return ""
    lines = [line for line in out.splitlines() if line.startswith("FAILED") or "Error" in line]
    return (lines[-1] if lines else out.splitlines()[-1] if out.strip() else "")[:240]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--stuck-min", type=float, default=60.0)
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args(argv)
    now = time.time() if a.now is None else a.now
    record = newest(a.home / "work" / "releases")
    if record is None:
        print("pin-delivery: no promotion record yet; cannot tell", file=sys.stderr)
        return 3
    phase, sha = record.get("phase"), str(record.get("sha", ""))[:8]
    age_min = (now - record["created_at"]) / 60
    if phase == "delivered":
        if pointer_sha(a.home) != record.get("sha"):
            print(f"pin-delivery: {sha} is recorded delivered but runtime/current names {str(pointer_sha(a.home))[:8]}", file=sys.stderr)
            return 3
        print(f"pin-delivery: GREEN {sha} delivered {age_min:.0f} min ago ({len(record.get('paths', []))} paths)")
        return 0
    if phase in RUNNING:
        if age_min > a.stuck_min:
            print(f"pin-delivery: promotion of {sha} has been {phase} for {age_min:.0f} min", file=sys.stderr)
            return 3
        print(f"pin-delivery: GREEN promotion of {sha} is {phase} ({age_min:.0f} min)")
        return 0
    if phase in FAILED:
        print(f"pin-delivery: {sha} {phase} {age_min:.0f} min ago: {record.get('failure') or last_failing_line(record) or 'no reason recorded'}",
              file=sys.stderr)
        return 2
    print(f"pin-delivery: {sha} is in phase {phase!r}; cannot tell", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
