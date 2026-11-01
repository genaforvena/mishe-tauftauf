"""Host capacity as a signal graph: anon pressure instead of total cgroup current.

cgroup memory.current includes reclaimable file cache. With memory.high set, the
kernel reclaims cache to hold the slice near high, so current/high parks near
0.9 whether or not anything is wrong. Replay on 2026-11-01 (artifact
signals-replay-20261101): the anon reading cuts LIMITED/CRITICAL starts from 33 to 14
on the same rows; the remaining starts are PSI memory stalls with anon refaults
dominant, which this module still reports. This module reads anon (and file,
for the record) separately; the decision is still host_capacity.decide, fed the
anon reading. Nothing here is wired into the running controller.

Pure: no I/O, no clock. The caller supplies a joined sample and `now`.
"""
from __future__ import annotations

from . import host_capacity
from . import live_signal as ls


def sample_signals(sample: dict, *, at: float, max_age: float = 60.0) -> dict:
    """Return named Signals from one joined sample, or UNKNOWN where input is missing.

    sample keys: anon, file, current, high, max, memory_full, io_full,
    available_ratio, events (dict with oom_kill). `at` is the observation time of
    the sample. Each part is observed separately so one missing part does not
    pass as OK.
    """
    def get(key):
        return sample.get(key) if isinstance(sample, dict) else None

    high, maximum = get("high"), get("max")
    out = {}
    out["limits"] = ls.observe(
        (high, maximum) if all(type(v) in (int, float) and v > 0 for v in (high, maximum))
        and high < maximum else None,
        at=at, source="memory.high/max")
    for name in ("anon", "file", "current"):
        out[name] = ls.observe(get(name) if type(get(name)) in (int, float) and get(name) >= 0
                               else None, at=at, source=f"memory.{name}")
    for name in ("memory_full", "io_full", "available_ratio"):
        out[name] = ls.observe(get(name), at=at, source=name)
    events = get("events")
    oom = events.get("oom_kill") if isinstance(events, dict) else None
    out["oom_kill"] = ls.observe(oom if type(oom) is int and oom >= 0 else None,
                                 at=at, source="memory.events oom_kill")
    return out


def controller_observation(sample: dict, *, at: float, now: float, max_age: float = 60.0):
    """Anon-based observation for host_capacity.decide, or None if any part is not OK.

    `current` is replaced by anon so decide()'s memory checks see anon pressure.
    The file-cache reading is kept in the sample for display and replay only.
    """
    signals = sample_signals(sample, at=at, max_age=max_age)
    fresh = {k: ls.hold_until_expiry(s, now, max_age) for k, s in signals.items()}
    if any(s.state != ls.OK for s in fresh.values()):
        return None
    obs = dict(
        memory_full=fresh["memory_full"].value,
        io_full=fresh["io_full"].value,
        available_ratio=fresh["available_ratio"].value,
        current=fresh["anon"].value,
        high=fresh["limits"].value[0],
        max=fresh["limits"].value[1],
        events={"oom_kill": fresh["oom_kill"].value},
    )
    return obs


def decide_anon(previous, sample: dict, *, at: float, now: float, max_age: float = 60.0):
    """host_capacity.decide on the anon reading. Missing or stale input yields UNKNOWN."""
    obs = controller_observation(sample, at=at, now=now, max_age=max_age)
    return host_capacity.decide(previous, obs, now=now)
