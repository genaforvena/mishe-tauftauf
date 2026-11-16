#!/usr/bin/env python3
"""Detector: the outcome of the newest pin promotion (``mishe release advance``), as a signal.

Reads the newest record under SITE/work/releases (written by release_promotion.promote) and the live pointer.

  GREEN    delivered, and runtime/current names that release; or a promotion still running and younger than --stuck-min
  RED      the newest promotion ended in check-failed, check-mutated-*, rolled-back or UNKNOWN: names the sha, the
           phase, the failure and the last failing line of the check, so the resolver fixes a cause, not a symptom
  UNKNOWN  no promotion record, an UNREADABLE record (a corrupt or torn record is not skipped in silence:
           the newest promotion is then unknown, not the newest record that happened to parse), a pointer that
           disagrees with a delivered record, or a promotion that has run longer than --stuck-min

Exit: 0 GREEN, 2 RED, 3 UNKNOWN. GREEN prints to stdout, the others to stderr.
"""
import argparse
import json
import sys
import time
from pathlib import Path

FAILED = {"check-failed", "check-mutated-source", "check-mutated-command", "rolled-back", "UNKNOWN"}
RUNNING = {"checking", "waiting-for-wakes", "delivery-busy", "activating"}


def newest(directory):
    """The newest promotion record, or the reason there is none.

    A record that cannot be read is NOT skipped.  It was, until the review
    of the 2026-11-14 release refused the change on 2026-11-15 with exactly
    this defect: a corrupt or torn record was passed over in silence, and an
    OLDER record with phase "delivered" whose sha matched runtime/current
    then answered GREEN.  The check reported a healthy pin while the record
    of the promotion that actually happened was unreadable.

    A record that cannot be read is a promotion whose outcome nobody can
    rule out, so it is UNKNOWN.  Which record is "newest" cannot be known
    from a file that does not parse, so the newest UNREADABLE record is
    reported and the check refuses to pass on the ones it can read.

    Returns ``(record, unreadable)``: the newest readable record and the
    newest unreadable one, either of which may be None.
    """
    best = None
    unreadable = None
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            unreadable = (path, error)
            continue
        if isinstance(record, dict) and isinstance(record.get("created_at"), (int, float)):
            if best is None or record["created_at"] > best["created_at"]:
                best = record
    return best, unreadable


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
    record, unreadable = newest(a.home / "work" / "releases")
    if unreadable is not None:
        path, error = unreadable
        print(f"pin-delivery: the promotion record {path.name} could not be read "
              f"({type(error).__name__}: {error}); the newest promotion is "
              f"UNKNOWN, not the newest record that could be parsed", file=sys.stderr)
        return 3
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
