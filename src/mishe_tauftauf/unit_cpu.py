"""Per-unit CPU attribution sense: cgroup usage_usec deltas for units under mishe-plants.slice.

Per-PID /proc sampling (``sense.proc.top-cpu``) misses most of the load on a saturated
slice: discover measured 804 process births in 30 s, 784 of them alive under 1 s, which a
two-read /proc window cannot see. Each unit's cgroup ``cpu.stat usage_usec`` keeps the CPU
its whole subtree used, exited children included, so a delta per unit attributes it.

Units are direct children of the slice (``*.service`` and ``*.scope``). A unit that exits
inside the window drops out of the child listing, so its CPU is not attributed. The slice's
own counter still counts it, which is why the verdict reconciles the unit sum against the
slice root: a gap above ``RECONCILE_TOLERANCE`` is ``drift``, not a quiet attribution.

Observation only. It reads two snapshots ``sample_seconds`` apart and changes nothing.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .cpu_stat_text import parse_cpu_stat, read_cpu_stat_file
from .slice_throttle import default_cgroup_root

SLICE = "mishe-plants.slice"
SENSE_ID = "sense.proc.unit-cpu"
RECONCILE_TOLERANCE = 0.05
TOP_UNITS = 8
SAMPLE_SECONDS = 1.0
_UNIT_SUFFIXES = (".service", ".scope")


def _usage(path: Path) -> int | None:
    counters = parse_cpu_stat(read_cpu_stat_file(path / "cpu.stat"), ("usage_usec",))
    return None if counters is None else counters["usage_usec"]


def _snapshot(slice_path: Path) -> tuple[int, dict[str, int]] | None:
    """Slice usage_usec and each direct unit's usage_usec, or None when the slice is unreadable."""
    root = _usage(slice_path)
    if root is None:
        return None
    try:
        names = os.listdir(slice_path)
    except OSError:
        return None
    units: dict[str, int] = {}
    for name in names:
        if not name.endswith(_UNIT_SUFFIXES) or not (slice_path / name).is_dir():
            continue
        usage = _usage(slice_path / name)
        if usage is not None:
            units[name] = usage
    return root, units


def _unknown(reason: str) -> dict:
    return {"id": SENSE_ID, "state": "unknown", "kind": "read",
            "sample": f"{SLICE} unit cpu unknown: {reason}; not measured, not calm"}


def unit_cpu_verdict(before: tuple[int, dict[str, int]],
                     after: tuple[int, dict[str, int]],
                     span_seconds: float) -> dict:
    """Per-unit CPU over the window, reconciled against the slice root. Pure: no reads."""
    if span_seconds <= 0:
        return _unknown("non-positive sample window")
    (root0, units0), (root1, units1) = before, after
    root_delta = root1 - root0
    if root_delta < 0:
        return _unknown("slice counter went backwards (slice recreated)")
    span_usec = span_seconds * 1e6
    rows: list[tuple[str, int]] = []
    for name, later in units1.items():
        # A unit absent from the baseline was born inside the window, so its whole usage is in-window.
        earlier = units0.get(name, 0)
        if later < earlier:
            continue  # a recycled name: its counter is not this unit's delta
        rows.append((name, later - earlier))
    unit_sum = sum(delta for _, delta in rows)
    exited = sorted(name for name in units0 if name not in units1)
    if root_delta == 0:
        gap = 0.0 if unit_sum == 0 else 1.0
    else:
        gap = abs(root_delta - unit_sum) / root_delta
    rate = lambda usec: round(100.0 * usec / span_usec, 1)  # noqa: E731 - % of one core
    top = sorted(rows, key=lambda row: -row[1])[:TOP_UNITS]
    units_text = " ".join(f"{name}={rate(delta)}%" for name, delta in top) or "none"
    head = (f"{SLICE} root={rate(root_delta)}% of one core units={rate(unit_sum)}% "
            f"reconcile-gap={round(100 * gap, 1)}% tolerance={round(100 * RECONCILE_TOLERANCE)}% "
            f"window={round(span_seconds, 2)}s exited-in-window={len(exited)}")
    reading = {"id": SENSE_ID, "kind": "read",
               "window_seconds": round(span_seconds, 3),
               "root_usec": root_delta, "unit_sum_usec": unit_sum,
               "reconcile_gap": round(gap, 4),
               "exited_in_window": exited,
               "units": [{"unit": name, "usec": delta, "rate": rate(delta)} for name, delta in top]}
    if gap <= RECONCILE_TOLERANCE:
        reading["state"] = "verified"
        reading["sample"] = f"{head} top: {units_text}"
    else:
        reading["state"] = "drift"
        reading["sample"] = (f"{head} unit sum disagrees with slice root beyond tolerance; "
                             f"attribution is partial. top: {units_text}")
    return reading


def read_unit_cpu(cgroup_root: Path | None = None,
                  sample_seconds: float = SAMPLE_SECONDS) -> dict:
    """One ``sense.proc.unit-cpu`` reading: two snapshots of the plants slice, reconciled."""
    slice_path = (cgroup_root or default_cgroup_root()) / SLICE
    before = _snapshot(slice_path)
    if before is None:
        return _unknown("slice cpu.stat unreadable")
    started = time.monotonic()
    time.sleep(sample_seconds)
    span = time.monotonic() - started
    after = _snapshot(slice_path)
    if after is None:
        return _unknown("slice cpu.stat unreadable at second sample")
    return unit_cpu_verdict(before, after, span)
