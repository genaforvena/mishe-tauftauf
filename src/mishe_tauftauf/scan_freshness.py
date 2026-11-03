"""Read-only acquisition bounds in a compatible Linux BOOTTIME domain.

Domain equality is compatibility evidence, not authentication of a boot or
namespace lifetime. UTC is diagnostic only and never establishes recency.

Freshness (AoI) does not imply decision effectiveness (Li et al.
arXiv:2504.19507v3 \u00a7I, Thm. 9). The module measures age and windowed AoI;
the consumer's cost C(X_t,a_t) is not modeled here. There exists a
threshold rate fmax^T beyond which additional sampling yields no reduction
in optimal decision cost; the AoI line is descriptive, not the objective.
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


# Li et al. 2026 \u2014 goal-oriented sampling; AoI vs effectiveness
# https://arxiv.org/abs/2504.19507 (v3 6 Feb 2026) Thm. 9 fmax^T=1/F(\u03c1*)
SAMPLING_EFFECTIVENESS_DEFINITION = "h* independent of fmax when fmax >= fmax^T = 1/F(rho*) (Thm.9)"


def sampling_frequency_threshold_hint(
    current_interval_s: float, fmax_T_hint: float | None = None
) -> dict:
    """Return whether current sampling rate is beyond the effectiveness threshold.

    Li et al. Thm.9: if fmax >= fmax^T, extra sampling does not reduce h*.
    This stub publishes the comparison without fitting rho*/F(); caller supplies
    fmax_T_hint when measured. Returns {fmax, fmax_T_hint, beyond_threshold: bool|None}.
    Pure arithmetic, no I/O. Source: https://arxiv.org/pdf/2504.19507.pdf \u00a7I, Thm.9.
    """
    import math as _math
    fmax: float | None = None
    if isinstance(current_interval_s, bool):
        fmax = None
    elif isinstance(current_interval_s, (int, float)) and _math.isfinite(float(current_interval_s)) and float(current_interval_s) > 0:
        fmax = 1.0 / float(current_interval_s)
    hint: float | None = None
    if isinstance(fmax_T_hint, bool):
        hint = None
    elif isinstance(fmax_T_hint, (int, float)) and _math.isfinite(float(fmax_T_hint)) and float(fmax_T_hint) > 0:
        hint = float(fmax_T_hint)
    beyond: bool | None = None
    if fmax is not None and hint is not None:
        beyond = fmax >= hint
    return {"fmax": fmax, "fmax_T_hint": hint, "beyond_threshold": beyond}


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

def aoi_summary(stamps: list, *, now, lags: list[float] | None = None) -> dict | None:
    """Windowed Age-of-Information summary for a stamp sequence.

    Yates et al. 2020 §II: monitor age ``Δ(t)=t−u(t)`` sawtooth with
    time-average ``⟨Δ⟩=E[Q]/E[Y]`` (Eq. 2-3) and average peak ``Δ^{(p)}=E[A]``
    (§II-B). This module already owns the point decision ``classify_age``
    (recent/stale/unknown) at one ``now``; this summary is the additive
    interval measurement over a window of prior publish instants.

    * ``stamps`` — ordered window of prior publish instants, earliest first
      (pane footer ``-- pane live <utc>``, scan ``created``, report
      ``generated_at``), or per-receipt pairs ``(stamp, T)`` where ``T`` is
      the age of the update at receipt (Kaul-Yates 2012 Eq. 4: ``Q=X·T+X²/2``).
      Passing pairs and passing ``lags`` are equivalent; ``lags`` is preferred
      for explicit call sites. Caller chooses ``WS`` (``100`` ~ 8 min at 5 s/frame
      for peaks, ``1000`` ~ 83 min for ϕ validation per review).
    * ``now`` — current time for ``Δ(t)=now−stamps[-1]``.
    * ``lags`` — optional per-receipt transport ages ``T`` aligned to
      ``stamps``. When provided the folded ``Q=D²/2`` mean is kept as
      ``mean_age`` and the unfolded ``Q=X·T+X²/2`` mean is published as
      ``mean_age_unfolded`` (gap ``Σ(X·T)/ΣX``).

    Returns ``None`` (unknown) on warmup (<2 stamps), incompatible or
    invalid stamps, or ``now`` preceding the window. Otherwise returns a
    dict with ``current``, ``mean_peak`` (``E[A]≈E[D]``), ``mean_age``
    (``E[Q]/E[D]`` with ``Q=D²/2`` when ``T`` folded, Eq. 2), ``p95``,
    ``max``, ``min``, ``n``, ``window_seconds`` — all in seconds as float.
    When ``lags``/pairs are given also ``mean_age_unfolded``,
    ``mean_transport`` (=``Σ(X·T)/ΣX``), ``t_median``, ``t_p95``, ``t_max``,
    ``t_min``. ``None`` uses the same unknown vocabulary as ``classify_age`` and
    ``coverage-window-expiry._fresh`` (``Undecidable``): warmup does not
    become ``recent``/``stale``.
    """
    from datetime import datetime, timezone

    # Allow per-receipt pairs ``(stamp, T)`` as stamps elements for callers
    # that already have the pairs (review wording). Prefer explicit ``lags``.
    _pair_lags: list[float] | None = None
    if stamps and isinstance(stamps[0], (list, tuple)) and len(stamps[0]) == 2:
        try:
            _maybe_dt = stamps[0][0]
            if isinstance(_maybe_dt, datetime) and isinstance(stamps[0][1], (int, float)) and not isinstance(stamps[0][1], bool):
                _pair_lags = [float(p[1]) for p in stamps]  # type: ignore[arg-type]
                stamps = [p[0] for p in stamps]  # type: ignore[arg-type]
            # else: not a pair mode, treat as plain stamps list
        except Exception:
            return None
    if lags is None:
        lags = _pair_lags
    elif _pair_lags is not None:
        # Both sources supplied — ambiguous.
        return None

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
    # Validate optional lags aligned to stamps.
    norm_lags: list[float] | None = None
    if lags is not None:
        if not isinstance(lags, list) or len(lags) != len(normalized):
            return None
        norm_lags = []
        for v in lags:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return None
            fv = float(v)
            if not math.isfinite(fv) or fv < 0:
                return None
            norm_lags.append(fv)
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
    result: dict[str, float | int] = {
        "current": float(current),
        "mean_peak": float(mean_peak),
        "mean_age": float(mean_age),
        "p95": float(p95),
        "max": float(max(peaks)),
        "min": float(min(peaks)),
        "n": int(n),
        "window_seconds": float(window_seconds),
    }
    if norm_lags is not None:
        # Eq. 4 unfolded: Q_i = X_i·T_i + X_i²/2 where T_i is age at receipt
        # of the later update (interval i between stamps[i-1] and stamps[i]).
        sum_xt = 0.0
        for i in range(len(peaks)):
            sum_xt += peaks[i] * norm_lags[i + 1]
        mean_age_unfolded = (sum_d2 / 2.0 + sum_xt) / sum_d
        mean_transport = sum_xt / sum_d
        # T statistics over the window's receipts (all aligned lags)
        ordered_t = sorted(norm_lags)
        middle = len(ordered_t) // 2
        t_median = ordered_t[middle] if len(ordered_t) % 2 else (ordered_t[middle - 1] + ordered_t[middle]) / 2
        tp95_idx = math.ceil(0.95 * len(ordered_t)) - 1
        tp95_idx = max(0, min(tp95_idx, len(ordered_t) - 1))
        result["mean_age_unfolded"] = float(mean_age_unfolded)
        result["mean_transport"] = float(mean_transport)
        result["t_median"] = float(t_median)
        result["t_p95"] = float(ordered_t[tp95_idx])
        result["t_max"] = float(max(norm_lags))
        result["t_min"] = float(min(norm_lags))
    return result


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
