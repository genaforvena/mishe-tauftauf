"""Read-only acquisition bounds in a compatible Linux BOOTTIME domain.

Domain equality is compatibility evidence, not authentication of a boot or
namespace lifetime. UTC is diagnostic only and never establishes recency.
"""
from __future__ import annotations

import math
import os
import re
import time
from fractions import Fraction
from pathlib import Path


_NANOSECONDS = 1_000_000_000
_ENDPOINT_KEYS = {"clock", "bounds_ns", "boot_id", "time_namespace", "offsets", "utc_ns"}
_OFFSET_KEYS = {"monotonic", "boottime"}
_BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_TIME_NAMESPACE = re.compile(r"time:\[(?:0|[1-9][0-9]*)\]")


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bounds(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(_integer(part) for part in value)
        and 0 <= value[0] <= value[1]
    )


def _valid_endpoint(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _ENDPOINT_KEYS:
        return False
    if value["clock"] != "CLOCK_BOOTTIME":
        return False
    if not isinstance(value["bounds_ns"], list) or not _bounds(value["bounds_ns"]):
        return False
    if not isinstance(value["boot_id"], str) or not _BOOT_ID.fullmatch(value["boot_id"]):
        return False
    if not isinstance(value["time_namespace"], str) or not _TIME_NAMESPACE.fullmatch(value["time_namespace"]):
        return False
    if not _integer(value["utc_ns"]) or value["utc_ns"] < 0:
        return False
    offsets = value["offsets"]
    if not isinstance(offsets, dict) or set(offsets) != _OFFSET_KEYS:
        return False
    return all(
        isinstance(offset, list)
        and len(offset) == 2
        and _integer(offset[0])
        and _integer(offset[1])
        and 0 <= offset[1] < _NANOSECONDS
        for offset in offsets.values()
    )


def _compatible(left: dict, right: dict) -> bool:
    return all(left[key] == right[key] for key in ("clock", "boot_id", "time_namespace", "offsets"))


def endpoint() -> dict | None:
    """Bracket read-only domain and UTC reads; unavailable evidence is None."""
    try:
        first = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        namespace = os.readlink("/proc/self/ns/time")
        offsets = {}
        for line in Path("/proc/self/timens_offsets").read_text().splitlines():
            clock, seconds, nanoseconds = line.split()
            if clock not in _OFFSET_KEYS or clock in offsets:
                return None
            offsets[clock] = [int(seconds), int(nanoseconds)]
        utc_ns = time.time_ns()
        last = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    except (OSError, AttributeError, ValueError, TypeError, OverflowError, NotImplementedError):
        return None
    value = {
        "clock": "CLOCK_BOOTTIME",
        "bounds_ns": [first, last],
        "boot_id": boot_id,
        "time_namespace": namespace,
        "offsets": offsets,
        "utc_ns": utc_ns,
    }
    return value if _valid_endpoint(value) else None


def _acquisition(snapshot: object) -> tuple[dict, dict] | None:
    if not isinstance(snapshot, dict):
        return None
    acquisition = snapshot.get("acquisition")
    if not isinstance(acquisition, dict) or set(acquisition) != {"start", "end"}:
        return None
    start, end = acquisition["start"], acquisition["end"]
    if not _valid_endpoint(start) or not _valid_endpoint(end):
        return None
    if not _compatible(start, end) or start["bounds_ns"][1] > end["bounds_ns"][0]:
        return None
    return start, end


def age_bounds(snapshot: dict, current: dict | None = None) -> tuple[int, int] | None:
    """Return whole-acquisition age bounds in ns; never repair missing anchors."""
    acquisition = _acquisition(snapshot)
    if acquisition is None:
        return None
    start, end = acquisition
    if current is None:
        current = endpoint()
    if not _valid_endpoint(current) or not _compatible(start, current):
        return None
    bounds = (
        current["bounds_ns"][0] - end["bounds_ns"][1],
        current["bounds_ns"][1] - start["bounds_ns"][0],
    )
    return bounds if _bounds(bounds) else None


def classify_age(bounds: tuple[int, int] | None, max_age_seconds: float) -> str:
    """Classify inclusive freshness without rounding ns bounds through floats.

    A finite int/float threshold is interpreted by its decimal spelling so,
    for example, 0.3 seconds denotes exactly 300,000,000 nanoseconds.
    """
    if not _bounds(bounds) or isinstance(max_age_seconds, bool):
        return "unknown"
    if isinstance(max_age_seconds, float):
        if not math.isfinite(max_age_seconds):
            return "unknown"
    elif not isinstance(max_age_seconds, int):
        return "unknown"
    if max_age_seconds < 0:
        return "unknown"
    threshold = (
        max_age_seconds * _NANOSECONDS
        if isinstance(max_age_seconds, int)
        else Fraction(str(max_age_seconds)) * _NANOSECONDS
    )
    if bounds[1] <= threshold:
        return "recent"
    if bounds[0] > threshold:
        return "stale"
    return "unknown"


def utc_consistency(snapshot: dict) -> str:
    """Compare endpoint UTC delta to elapsed bounds, not clock continuity."""
    acquisition = _acquisition(snapshot)
    if acquisition is None:
        return "unknown"
    start, end = acquisition
    elapsed = (
        end["bounds_ns"][0] - start["bounds_ns"][1],
        end["bounds_ns"][1] - start["bounds_ns"][0],
    )
    wall_delta = end["utc_ns"] - start["utc_ns"]
    return "consistent" if elapsed[0] <= wall_delta <= elapsed[1] else "anomaly"
