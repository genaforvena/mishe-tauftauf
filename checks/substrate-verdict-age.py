#!/usr/bin/env python3
"""Detector: how long has the substrate reading been bad?

Health's rule (DM 74917): a SUBSTRATE reading that stays UNKNOWN, or that
carries a non-empty ``problems`` list, for ten consecutive minutes is a
routed blocker to health. The rule had no detector; this is one, and it
writes nothing.

Two facts shape the implementation.

* The verdict's age is measured in **wall-clock time**, not in observer
  intervals. The immutable event archive holds one event per *change* of the
  observation, not one per tick, so its event rate is uneven (the archive of
  2026-10-20 holds a gap of 1508 s between two events). An implementation
  that counted "about twenty intervals" would under-count and trip late.

* The current verdict is read from the plant's own heartbeat
  (``checks/substrate-watch.json``). A heartbeat that is missing, unreadable
  or older than the staleness bound is **not** a healthy plant: this check
  reports UNKNOWN and exits 2. Missing, stale or broken monitoring stays
  UNKNOWN or RED.

The onset of the current bad run is recovered from the archive: the first
bad event after the last good one. A run that is younger than the bound is
reported and exits 0, because a transient UNKNOWN is not a blocker.

Exit codes: 0 healthy or younger than the bound, 2 undecidable (missing,
stale or broken monitoring), 3 the bound is exceeded (route to health).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEARTBEAT = "checks/substrate-watch.json"
ARCHIVE = "checks/substrate-watch-events"
DEFAULT_BOUND_MINUTES = 10.0
DEFAULT_STALE_MINUTES = 5.0


def parse_time(value):
    """Parse an ISO timestamp, or return None when it carries no usable time."""
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def is_bad(row) -> bool:
    """Whether a reading is the kind the rule ages.

    Health's wording: the reading stays UNKNOWN, or it carries a non-empty
    ``problems`` list. A reading that is KNOWN but still lists problems is
    not healthy either, so both conditions count.
    """
    if not isinstance(row, dict):
        return False
    problems = row.get("problems")
    if isinstance(problems, list) and problems:
        return True
    return row.get("status") != "KNOWN"


def read_json(path: Path):
    """Read one JSON document, or return None when it cannot be read."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def read_events(archive: Path) -> list:
    """The archived observations, in time order, with unreadable ones dropped."""
    events = []
    if not archive.is_dir():
        return events
    for path in sorted(archive.glob("*.json")):
        event = read_json(path)
        if not isinstance(event, dict):
            continue
        report = event.get("report")
        if not isinstance(report, dict):
            continue
        checked = parse_time(report.get("checked_at"))
        if checked is None:
            continue
        events.append((checked, is_bad(report), report))
    events.sort(key=lambda row: row[0])
    return events


def run_onset(events: list, current_bad: bool, current_at):
    """When the current bad run started, or None when the verdict is healthy.

    ``events`` is the timeline up to and including the current reading, in
    time order. The onset is the first bad reading after the last good one:
    a later good reading ends the run, so an archive that carries readings
    after the current one must not extend a run that has already ended.
    """
    if not current_bad:
        return None
    onset = None
    for checked, bad, _report in events:
        if bad:
            if onset is None:
                onset = checked
        else:
            onset = None
    if onset is None:
        # The run began with the current reading; the archive has no bad event.
        onset = current_at
    return onset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, help="the site home")
    parser.add_argument("--bound-minutes", type=float, default=DEFAULT_BOUND_MINUTES,
                        help="age at which a bad verdict is a routed blocker")
    parser.add_argument("--stale-minutes", type=float, default=DEFAULT_STALE_MINUTES,
                        help="heartbeat age at which monitoring counts as stale")
    parser.add_argument("--now", default=None,
                        help="ISO time to measure against (for reproducible runs)")
    args = parser.parse_args()

    home = Path(args.home).resolve()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("UNDECIDABLE: --now is not a usable timestamp")
        return 2

    heartbeat = read_json(home / HEARTBEAT)
    if not isinstance(heartbeat, dict):
        print("UNKNOWN: no readable substrate heartbeat at " + str(home / HEARTBEAT))
        print("a missing heartbeat is not a healthy plant")
        return 2
    checked = parse_time(heartbeat.get("checked_at"))
    if checked is None:
        print("UNKNOWN: the substrate heartbeat carries no usable timestamp")
        return 2
    age = (now - checked).total_seconds()
    stale = args.stale_minutes * 60.0
    if age > stale:
        print(f"UNKNOWN: the substrate heartbeat is {age:.0f}s old "
              f"(staleness bound {stale:.0f}s)")
        print("stale monitoring is unknown, not healthy")
        return 2

    events = read_events(home / ARCHIVE)
    # The timeline ends with the current reading: an archive that carries
    # readings from after it describes a plant state that is not the one
    # being judged, and its good readings would end a run that is still open.
    timeline = [row for row in events if row[0] <= checked]
    current_bad = is_bad(heartbeat)
    onset = run_onset(timeline, current_bad, checked)
    problems = heartbeat.get("problems") or []
    if not current_bad:
        print(f"AGREE: substrate verdict KNOWN at {checked.isoformat()}")
        return 0
    if onset is None:
        print("UNKNOWN: the bad run has no recoverable onset")
        return 2
    bad_age = (now - onset).total_seconds()
    bound = args.bound_minutes * 60.0
    detail = "; ".join(str(item) for item in problems[:3])
    print(f"BAD: substrate verdict {heartbeat.get('status')} since {onset.isoformat()} "
          f"(age {bad_age / 60:.1f} min, bound {args.bound_minutes:.1f} min)")
    if detail:
        print("problems: " + detail)
    if bad_age >= bound:
        print(f"BOUND EXCEEDED: route to health (resolver) — the bad verdict has "
              f"persisted {bad_age / 60:.1f} min, at or beyond the "
              f"{args.bound_minutes:.1f} min bound")
        return 3
    print(f"within bound: {bad_age / 60:.1f} min < {args.bound_minutes:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
