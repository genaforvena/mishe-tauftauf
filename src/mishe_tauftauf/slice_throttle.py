"""Parent-slice CPU throttle sense: per-minute deltas of cgroup v2 cpu.stat.

Per-service cpu.stat cannot see a quota enforced on the parent slice, so this
reads the slices that carry the quota (mishe-plants.slice, mishe-lifeline.slice)
directly. Each discover scan reads one sample and compares it with the previous
sample persisted under ``home/discovery``. A delta is valid only when the two
samples share a counter epoch (no counter went backwards) and the elapsed gap is
within ``MIN_GAP_SECONDS``..``MAX_GAP_SECONDS``; otherwise the sense is UNKNOWN
for that slice and the new sample becomes the baseline. Observation only: this
never changes a quota or trips a guard.

Counter scope: the three counters read from a slice's cpu.stat (nr_periods,
nr_throttled, throttled_usec) are non-hierarchical. They count only throttling
caused by that slice's own bandwidth limit, not throttling inherited from an
ancestor cgroup's limit (kernel admin-guide cgroup-v2, cpu section, "the above
five CFS bandwidth stats are non-hierarchical"; read 2026-11-01 from
docs.kernel.org/admin-guide/cgroup-v2.html). A quiet reading therefore does not
rule out throttling from above the slice. The sample reports the slice's own
limit as the sense's verdict. Throttling inherited from an ancestor is read
separately from the sibling file cpu.stat.local (its ``throttled_usec``): the
``inherited`` field is inferred as ``local delta - own delta``, and is UNKNOWN (never zero)
when cpu.stat.local is absent. The sense id and sample text do not change.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .cpu_stat_text import (
    COUNTERS,
    baseline_text,
    parse_cpu_max_period,
    parse_cpu_stat,
    parse_cpu_stat_local,
    read_baseline,
    read_cpu_stat_file,
    slice_sense_id,
)

SLICES = ("mishe-plants.slice", "mishe-lifeline.slice")
MIN_GAP_SECONDS = 20.0
MAX_GAP_SECONDS = 900.0
STATE_NAME = "slice-throttle-prev.json"


def default_cgroup_root() -> Path:
    return Path(f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/mishe.slice")


def throttle_verdict(previous: dict, current: dict, now: float) -> dict:
    """Per-minute throttle delta between two samples, or an UNKNOWN reason.

    ``previous``/``current`` hold ``at`` (epoch seconds) plus the counters.
    """
    elapsed = now - float(previous["at"])
    if elapsed < MIN_GAP_SECONDS:
        return {"state": "unknown", "reason": f"baseline too recent ({elapsed:.1f}s)"}
    if elapsed > MAX_GAP_SECONDS:
        return {"state": "unknown", "reason": f"baseline stale ({elapsed:.0f}s old)"}
    deltas = {name: int(current[name]) - int(previous[name]) for name in COUNTERS}
    if any(value < 0 for value in deltas.values()):
        return {"state": "unknown", "reason": "counter went backwards (reboot or slice recreated)"}
    periods = deltas["nr_periods"]
    if periods <= 0:
        return {"state": "unknown", "reason": "no scheduler periods in the gap"}
    minutes = elapsed / 60.0
    return {"state": "verified",
            "window_seconds": round(elapsed, 1),
            "periods": periods,
            "throttled": deltas["nr_throttled"],
            "throttled_usec": deltas["throttled_usec"],
            "ratio": round(deltas["nr_throttled"] / periods, 4),
            "periods_per_min": round(periods / minutes, 1),
            "throttled_per_min": round(deltas["nr_throttled"] / minutes, 1),
            "throttled_usec_per_min": round(deltas["throttled_usec"] / minutes, 1),
            "throttled_cpus": _throttled_cpus(previous, current, deltas),
            "inherited": _inherited_throttle(previous, current, deltas["throttled_usec"], minutes)}


def _throttled_cpus(previous: dict, current: dict, deltas: dict) -> dict:
    """Own throttled time as CPU-equivalents: delta throttled_usec / (delta nr_periods x period_us).

    The ratio ``nr_throttled / nr_periods`` counts throttle events, not time; this is the
    time-equivalent beside it. ``throttled_usec`` is summed over runqueues (inference from
    the field-review, not checked in kernel source), so read this as an average CPU count.
    UNKNOWN when cpu.max is absent or the period changed between samples.
    """
    period_us = current.get("period_us")
    if period_us is None or previous.get("period_us") != period_us:
        return {"state": "unknown", "reason": "cpu.max period unavailable or changed"}
    cpus = deltas["throttled_usec"] / (deltas["nr_periods"] * int(period_us))
    return {"state": "verified", "cpus": round(cpus, 3)}


def _inherited_throttle(previous: dict, current: dict, own_usec: int, minutes: float) -> dict:
    """Ancestor-limit throttling: cpu.stat.local delta minus the slice's own delta."""
    before = previous.get("local_throttled_usec")
    after = current.get("local_throttled_usec")
    if before is None or after is None:
        return {"state": "unknown", "reason": "cpu.stat.local unavailable"}
    local_usec = int(after) - int(before)
    if local_usec < 0:
        return {"state": "unknown", "reason": "cpu.stat.local went backwards"}
    inherited = local_usec - own_usec
    if inherited < 0:
        return {"state": "unknown", "reason": "cpu.stat.local below own throttled_usec"}
    return {"state": "verified",
            "throttled_usec": inherited,
            "throttled_usec_per_min": round(inherited / minutes, 1)}


def read_slice_throttle(home: Path, cgroup_root: Path | None = None,
                        now: float | None = None) -> list[dict]:
    """One ``sense.cgroup.<slice>.throttle`` reading per slice; persists the baseline."""
    root = cgroup_root or default_cgroup_root()
    now = time.time() if now is None else now
    state_path = home / "discovery" / STATE_NAME
    previous_state = read_baseline(state_path)
    next_state: dict[str, dict] = {}
    readings: list[dict] = []
    for name in SLICES:
        sense_id = slice_sense_id(name)
        text = read_cpu_stat_file(root / name / "cpu.stat")
        counters = parse_cpu_stat(text)
        if counters is None:
            readings.append({"id": sense_id, "state": "unknown",
                             "sample": f"{name} cpu.stat unreadable", "kind": "read"})
            continue
        local_usec = parse_cpu_stat_local(read_cpu_stat_file(root / name / "cpu.stat.local"))
        period_us = parse_cpu_max_period(read_cpu_stat_file(root / name / "cpu.max"))
        current = {"at": now, **counters, "local_throttled_usec": local_usec, "period_us": period_us}
        next_state[name] = current
        previous = previous_state.get(name)
        if not isinstance(previous, dict) or "at" not in previous:
            verdict = {"state": "unknown", "reason": "no prior sample (first read after start)"}
        else:
            verdict = throttle_verdict(previous, current, now)
        if verdict["state"] == "verified":
            sample = (f"{name} window={verdict['window_seconds']}s ratio={verdict['ratio']} "
                      f"periods/min={verdict['periods_per_min']} "
                      f"throttled/min={verdict['throttled_per_min']} "
                      f"throttled_usec/min={verdict['throttled_usec_per_min']}")
        else:
            sample = f"{name} throttle unknown: {verdict['reason']}"
        readings.append({"id": sense_id, "state": verdict["state"], "sample": sample,
                         "kind": "read", "throttle": verdict})
    _write_state(state_path, next_state)
    return readings


def _write_state(path: Path, state: dict[str, dict]) -> None:
    """Persist the baseline; a slice that failed this read loses its baseline."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(baseline_text(state), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass
