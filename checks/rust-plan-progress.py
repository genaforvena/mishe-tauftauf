#!/usr/bin/env python3
"""Detector: the rust-plan investigation is making progress.

The rust-plan role produces two things: its canonical wall and the migration
proposal. This reads both from disk and reports whether the role is alive and
producing. It does not take the role's word for it, and it does not read a
hand-written GREEN: a missing or stale wall is RED, and an unreadable
observation is UNKNOWN.

The signal this backs is registered with a max_age, so a producer that stops
running ages the reading to UNKNOWN on its own. Nothing holds it GREEN by hand.

Exit status:
  0  GREEN: the wall exists and is inside its bound; the plan document is present
     and non-empty, or not yet written inside a fresh window
  2  RED: the wall is missing, older than the bound, or the plan document exists
     but is too small to be the deliverable
  3  UNKNOWN: the observation cannot be made (site home missing, mtime
     unreadable, or a wall timestamp in the future)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

#: The role's wall may be this old before the investigation counts as stalled.
DEFAULT_MAX_AGE = 7200
#: A plan document smaller than this is not the deliverable yet.
MIN_PLAN_BYTES = 200


def wall_age(wall: Path, now: float) -> tuple[float | None, str]:
    """Seconds since the wall was written, or (None, reason) when unreadable."""
    try:
        mtime = wall.stat().st_mtime
    except OSError as exc:
        return None, f"wall mtime unreadable: {exc.strerror or exc}"
    age = now - mtime
    if age < -300:
        return None, f"wall mtime is in the future by {-age:.0f}s; cannot tell"
    return age, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE)
    args = parser.parse_args(argv)

    home: Path = args.home
    repo: Path = args.repo
    now = time.time()

    if not home.is_dir():
        print(f"rust-plan-progress: site home missing: {home}", file=sys.stderr)
        return 3

    wall = home / "walls" / "rust-plan.md"
    if not wall.is_file():
        print(f"rust-plan-progress: no wall at {wall}; the role has written nothing", file=sys.stderr)
        return 2

    age, reason = wall_age(wall, now)
    if age is None:
        print(f"rust-plan-progress: {reason}", file=sys.stderr)
        return 3
    if age > args.max_age:
        print(f"rust-plan-progress: wall is {age:.0f}s old, past the {args.max_age:g}s bound",
              file=sys.stderr)
        return 2

    plan = repo / "docs" / "rust-runtime-migration-plan.md"
    if plan.is_file():
        try:
            size = plan.stat().st_size
        except OSError as exc:
            print(f"rust-plan-progress: plan size unreadable: {exc.strerror or exc}", file=sys.stderr)
            return 3
        if size < MIN_PLAN_BYTES:
            print(f"rust-plan-progress: plan document is only {size}B; treat as not yet written",
                  file=sys.stderr)
            return 2
        print(f"rust-plan-progress: GREEN wall_age={age:.0f}s plan_bytes={size}")
        return 0

    # A fresh window without a plan document is not a failure: the role has a
    # wall and has not been silent.
    print(f"rust-plan-progress: GREEN wall_age={age:.0f}s plan_document=not-yet-written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
