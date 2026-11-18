#!/usr/bin/env python3
"""Detector: a running wake or producer sitting below its declared memory floor.

The gap this closes.  The generated wake and producer units declare
``MemoryHigh=1536M``, the protected catch-all drop-in
(``mishe-.service.d/zzzzzz-host-containment.conf``) declares ``MemoryHigh=1G``,
and a ``systemctl --user daemon-reload`` writes the drop-in's 1G back onto every
*running* unit's cgroup.  At 1G the engine is throttled at the mark and stalls
until its timeout throws the work away.  Measured 2026-11-10 (five 1800 s
timeouts) and 2026-11-18 (genome, health, rust-plan, senses and steward throttled
at 1G; host ``full`` PSI 39-61 % with 25 GiB free), and again at 03:32:21Z on
``mishe-wake@rust-plan.service``, peak 1076M, ``memory.events.local high``
118506.

``wake_memory.MemoryFloorGuard`` repairs that while the unit runs, and each wake
and producer records the revert and repair counters it saw.  Nothing read the
resulting state: the only detector for a throttled plant was host memory PSI,
which reported the 03:32 recurrence at 03:39 -- seven minutes after the throttle
began, and only because five units were throttled at once.

This check reads the live value instead of the intent.  It walks ``/proc`` for
processes whose cgroup is a leaf of the plant slice, keeps the leaves named
``mishe-wake@*.service`` or ``mishe-producer-*.service``, and reads each leaf's
own ``memory.high``.  A leaf counts only while it still has a process, because
systemd leaves the cgroup of a dead oneshot behind with whatever value it held
when it exited.

The value is re-read after ``--persist`` seconds before it is reported, because
the guard's repair window is up to its two-second tick: a low reading that
disappears is a repair in progress, not a fault.  A low reading that persists is
a unit the guard is not repairing -- either the running release does not contain
the guard, or its cgroup write is failing -- and it is RED either way, naming the
unit and both values.

It never writes: no cgroup, no unit, no config.  A tree it cannot read, or a
floor it cannot learn, is UNKNOWN (exit 3), never a pass.

Exit status:
  0  GREEN   every guarded leaf that has a process reads at least the floor
  2  RED     a leaf with a process reads below the floor for --persist seconds,
             or a guarded leaf's memory.high cannot be read while it has processes
  3  UNKNOWN the declared floor cannot be learned, or the process tree cannot be
             enumerated

``--floor`` overrides the declared floor (bytes); without it the floor comes from
the running release's ``mishe_tauftauf.units.WAKE_MEMORY_HIGH_BYTES``.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

GUARDED = (("mishe-wake@", ".service"), ("mishe-producer-", ".service"))
"""Unit-name prefixes the plant restores a floor for, and the suffix every one carries."""

SLICE = "/mishe-plants.slice/"
"""Every plant unit's leaf is below this slice; a process elsewhere is not this check's business."""

PROC = pathlib.Path("/proc")


def declared_floor(home: pathlib.Path | None = None) -> int:
    """The floor the unit generator declares, from the bytes this plant runs.

    The duty runs this check from the checkout while the units come from the pinned
    release, so the release's own ``units`` module is the honest source; the checkout
    copy is used only when the package is already importable from here.
    """
    try:
        from mishe_tauftauf.units import WAKE_MEMORY_HIGH_BYTES
    except Exception as first:
        if home is None:
            raise Undecidable(f"cannot learn the declared floor: {type(first).__name__}: {first}") from first
        release = pathlib.Path(home) / "runtime" / "current" / "src"
        if not release.is_dir():
            raise Undecidable(f"cannot learn the declared floor: {first}; {release} is not a directory") from first
        sys.path.insert(0, str(release))
        try:
            from mishe_tauftauf.units import WAKE_MEMORY_HIGH_BYTES
        except Exception as exc:
            raise Undecidable(f"cannot learn the declared floor: {type(exc).__name__}: {exc}") from exc
    value = int(WAKE_MEMORY_HIGH_BYTES)
    if value <= 0:
        raise Undecidable(f"the declared floor is not a positive byte count: {value}")
    return value


class Undecidable(Exception):
    """The question cannot be decided from here; the answer is UNKNOWN."""


def cgroup_root(mountinfo: pathlib.Path = pathlib.Path("/proc/self/mountinfo")) -> pathlib.Path:
    """Where cgroup v2 is mounted, because ``/proc`` cgroup paths are relative to it."""
    try:
        text = mountinfo.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise Undecidable(f"cannot read {mountinfo}: {exc}") from exc
    for line in text.splitlines():
        after, _, fstype = line.partition(" - ")
        if fstype.split(" ", 1)[0] != "cgroup2":
            continue
        point = after.split()[4] if len(after.split()) > 4 else ""
        if point:
            return pathlib.Path(point)
    raise Undecidable("no cgroup2 mount is listed")


def guarded_leaves(root: pathlib.Path = PROC) -> dict[str, pathlib.Path]:
    """Map every guarded unit leaf that has at least one process to its cgroup path."""
    leaves: dict[str, pathlib.Path] = {}
    mount = cgroup_root()
    try:
        procs = list(root.iterdir())
    except OSError as exc:
        raise Undecidable(f"cannot enumerate {root}: {exc}") from exc
    for proc in procs:
        if not proc.name.isdigit():
            continue
        try:
            lines = (proc / "cgroup").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue  # the process exited between the listing and the read
        for line in lines:
            fields = line.split(":", 2)
            if len(fields) != 3 or not fields[0].startswith("0"):
                continue  # only the cgroup v2 (unified) entry names the cgroup path
            path = fields[2].strip()
            if SLICE not in path:
                continue
            leaf = path.rsplit(SLICE, 1)[1].split("/", 1)[0]
            if leaf and leaf.endswith(GUARDED[0][1]) and leaf.startswith(tuple(p for p, _ in GUARDED)):
                leaves[leaf] = mount / path.lstrip("/")
    return leaves


def read_high(cgroup: pathlib.Path) -> int | None:
    """The leaf's own ``memory.high`` in bytes, or None when it is not a number (``max``)."""
    try:
        text = (cgroup / "memory.high").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def inspect(leaves: dict[str, pathlib.Path], floor: int) -> list[dict]:
    """Read every guarded leaf once, naming the ones below the floor."""
    rows = []
    for unit, cgroup in sorted(leaves.items()):
        high = read_high(cgroup)
        rows.append({"unit": unit, "cgroup": cgroup, "high": high,
                     "below": high is not None and high < floor})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=pathlib.Path, help="site home; accepted for the runner's argv")
    parser.add_argument("--floor", type=int, default=None, help="expected memory.high in bytes")
    parser.add_argument("--persist", type=float, default=3.0,
                        help="seconds a low reading must survive to count as a fault")
    args = parser.parse_args(argv)
    try:
        floor = args.floor if args.floor is not None else declared_floor(args.home)
        leaves = guarded_leaves()
    except Undecidable as exc:
        print(f"wake-memory-floor: {exc}", file=sys.stderr)
        return 3
    first = inspect(leaves, floor)
    suspects = [row for row in first if row["below"]]
    if suspects:
        time.sleep(max(0.0, args.persist))
        again = {row["unit"]: row for row in inspect(leaves, floor)}
        suspects = [row for row in suspects if again.get(row["unit"], row)["below"]]
    unreadable = [row for row in first if row["high"] is None]
    if suspects:
        for row in suspects:
            print(f"wake-memory-floor: RED {row['unit']} memory.high={row['high']} below the "
                  f"declared floor {floor} for {args.persist:g}s", file=sys.stderr)
        return 2
    if unreadable:
        for row in unreadable:
            print(f"wake-memory-floor: {row['unit']} memory.high is unreadable while it has "
                  f"processes ({row['cgroup']})", file=sys.stderr)
        return 3
    print(f"wake-memory-floor: GREEN {len(first)} guarded unit(s) at or above {floor} "
          f"({', '.join(r['unit'] for r in first) or 'none running'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
