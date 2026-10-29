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

def aoi_summary(stamps: list, *, now) -> dict | None:
    """Windowed Age-of-Information summary for a stamp sequence.

    Yates et al. 2020 §II: monitor age ``Δ(t)=t−u(t)`` sawtooth with
    time-average ``⟨Δ⟩=E[Q]/E[Y]`` (Eq. 2-3) and average peak ``Δ^{(p)}=E[A]``
    (§II-B). This module already owns the point decision ``classify_age``
    (recent/stale/unknown) at one ``now``; this summary is the additive
    interval measurement over a window of prior publish instants.

    * ``stamps`` — ordered window of prior publish instants, earliest first
      (pane footer ``-- pane live <utc>``, scan ``created``, report
      ``generated_at``). Caller chooses ``WS`` (``100`` ~ 8 min at 5 s/frame
      for peaks, ``1000`` ~ 83 min for ϕ validation per review).
    * ``now`` — current time for ``Δ(t)=now−stamps[-1]``.

    Returns ``None`` (unknown) on warmup (<2 stamps), incompatible or
    invalid stamps, or ``now`` preceding the window. Otherwise returns a
    dict with ``current``, ``mean_peak`` (``E[A]≈E[D]``), ``mean_age``
    (``E[Q]/E[D]`` with ``Q=D²/2`` when ``T`` folded, Eq. 2), ``p95``,
    ``max``, ``min``, ``n``, ``window_seconds`` — all in seconds as float.
    ``None`` uses the same unknown vocabulary as ``classify_age`` and
    ``coverage-window-expiry._fresh`` (``Undecidable``): warmup does not
    become ``recent``/``stale``.
    """
    from datetime import datetime, timezone

    if not isinstance(stamps, list) or len(stamps) < 2:
        return None
    if not isinstance(now, datetime) or now.tzinfo is None:
        return None
    # Validate each stamp is an aware datetime; normalize to UTC for deltas.
    normalized: list[datetime] = []
    for s in stamps:
        if not isinstance(s, datetime) or s.tzinfo is None:
            return None
        try:
            normalized.append(s.astimezone(timezone.utc))
        except Exception:
            return None
    try:
        now_utc = now.astimezone(timezone.utc)
    except Exception:
        return None
    # Strictly increasing stamps; non-monotonic or duplicate is incompatible.
    for i in range(1, len(normalized)):
        if normalized[i] <= normalized[i - 1]:
            return None
    if now_utc < normalized[-1]:
        return None
    # Inter-stamp peaks A_n ≈ D_n (Eq. II-B with T folded).
    peaks: list[float] = []
    for i in range(1, len(normalized)):
        delta = (normalized[i] - normalized[i - 1]).total_seconds()
        # Finite, non-negative by monotonicity check; defend against NaN/inf.
        if not math.isfinite(delta) or delta < 0:
            return None
        peaks.append(float(delta))
    current = (now_utc - normalized[-1]).total_seconds()
    if not math.isfinite(current) or current < 0:
        return None
    n = len(peaks)
    window_seconds = (normalized[-1] - normalized[0]).total_seconds()
    # Means via exact sums then float; Eq.2 Q = D²/2, ⟨Δ⟩ = ΣQ/ΣD.
    sum_d = float(sum(peaks))
    if sum_d == 0:
        return None
    sum_d2 = float(sum(d * d for d in peaks))
    mean_peak = sum_d / n
    mean_age = sum_d2 / (2.0 * sum_d)
    sorted_peaks = sorted(peaks)
    # p95 as smallest value with at least 95% of samples ≤ it (ceil).
    p95_index = math.ceil(0.95 * n) - 1
    p95_index = max(0, min(p95_index, n - 1))
    p95 = float(sorted_peaks[p95_index])
    return {
        "current": float(current),
        "mean_peak": float(mean_peak),
        "mean_age": float(mean_age),
        "p95": float(p95),
        "max": float(max(peaks)),
        "min": float(min(peaks)),
        "n": int(n),
        "window_seconds": float(window_seconds),
    }


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
