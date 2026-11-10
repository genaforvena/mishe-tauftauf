#!/usr/bin/env python3
"""Detector: runtime/current points at the release of the last verified delivery.

Incident 2026-11-10: a mind hand-edited runtime/current from a stale wall note and
moved the whole plant ~2 days back. The only writers of the pin are the delivery
path (mishe-deliver, deliver.sh, heal.sh). The truth for "what should the pin be"
is the newest patches/moving-pin-<id>.json with delivery_verified true.

Exit status:
  0  pin equals the last verified delivery's release (or a delivery is in progress)
  2  pin differs and no delivery is in progress; message holds the restore command
  3  undecidable: records unreadable, no verified record, or release dir missing
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

IN_PROGRESS_S = 7200.0
PENDING = {"prepared", "tested", "reviewed", "activating", "applying"}


def _ts(text) -> float | None:
    try:
        return datetime.datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def expected_release(home: Path) -> tuple[str, str]:
    """Return (record id, release dir name) of the newest verified delivery.

    A superseded record is still a verified delivery: preparing a newer patch
    renames the previous record, and until the newer one is verified the previous
    release is the one the pin must hold.  Skipping them named an older release
    and printed a restore command that would have regressed the plant.
    """
    best = None
    for path in (home / "patches").glob("moving-pin-*.json"):
        data = json.loads(path.read_text())
        at = _ts(data.get("applied_at"))
        if data.get("delivery_verified") is True and data.get("phase") == "applied" and at:
            sha = path.stem.removeprefix("moving-pin-").split(".", 1)[0]
            if best is None or at > best[0]:
                best = (at, sha)
    if best is None:
        raise ValueError("no verified moving-pin record")
    sha = best[1]
    found = sorted(p.name for p in (home / "releases").glob(sha + "*"))
    if len(found) != 1:
        raise ValueError(f"release for {sha}: {len(found)} matches")
    return sha, found[0]


def in_progress(home: Path, now: float) -> bool:
    for path in (home / "patches").glob("moving-pin-*.json"):
        if "superseded" in path.name:
            continue
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        at = _ts(data.get("prepared_at"))
        if data.get("phase") in PENDING and at and now - at <= IN_PROGRESS_S:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    args = parser.parse_args(argv)
    home = Path(args.home)
    link = home / "runtime" / "current"
    try:
        sha, release = expected_release(home)
        target = os.readlink(link)
        st = os.lstat(link)
    except (OSError, ValueError) as exc:
        print(f"UNKNOWN: cannot decide pin integrity: {exc}")
        return 3
    changed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))
    actual = Path(target).name
    if actual == release:
        print(f"OK: runtime/current -> {release[:8]} = last verified delivery {sha}; symlink changed {changed}")
        return 0
    if in_progress(home, time.time()):
        print(f"OK: pin {actual[:8]} differs from verified {sha} but a delivery is in progress; changed {changed}")
        return 0
    print(f"FAIL: runtime/current -> {actual[:8]} but last verified delivery is {sha}; symlink last changed {changed}. "
          f"Restore: ln -sfn {home}/releases/{release} {link}  (never hand-edit otherwise; use deliver.sh)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
