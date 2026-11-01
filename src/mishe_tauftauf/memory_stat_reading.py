"""Read-only cgroup v2 memory.stat and swap readings as Signals.

Genome (2026-11-01) accepted a read-only reader as a separate function: it performs
no writes, is not called from host_capacity.decide, and does not touch the running
controller. Missing file, missing key or parse failure yields a failed Signal
(UNKNOWN), never 0.
"""
from __future__ import annotations

from pathlib import Path

from . import live_signal as ls

MEMORY_STAT_KEYS = ("anon", "file", "shmem")


def parse_memory_stat(text: str) -> dict:
    """Parse the anon/file/shmem byte counters from a memory.stat body. Raises ValueError if any is absent."""
    found = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in MEMORY_STAT_KEYS:
            found[fields[0]] = int(fields[1])
    missing = [k for k in MEMORY_STAT_KEYS if k not in found]
    if missing:
        raise ValueError("memory.stat missing " + ",".join(missing))
    if any(v < 0 for v in found.values()):
        raise ValueError("negative memory.stat counter")
    return found


def parse_bytes_or_max(text: str):
    """A cgroup limit file: a byte count, or 'max' (no limit). Returns int, or None for 'max'."""
    token = text.strip()
    if token == "max":
        return None
    value = int(token)
    if value < 0:
        raise ValueError("negative limit")
    return value


def read_memory_stat(slice_path, *, at: float) -> dict:
    """Named Signals {anon, file, shmem, swap_current, swap_max} for one cgroup. Never OK on failure."""
    path = Path(slice_path)
    out = {}
    try:
        stat = parse_memory_stat((path / "memory.stat").read_text())
        for key in MEMORY_STAT_KEYS:
            out[key] = ls.observe(stat[key], at=at, source=f"memory.stat {key}")
    except (OSError, ValueError) as exc:
        for key in MEMORY_STAT_KEYS:
            out[key] = ls.fail(f"memory.stat {key}", at=at, reason=str(exc))
    try:
        swap_current = int((path / "memory.swap.current").read_text().strip())
        if swap_current < 0:
            raise ValueError("negative swap.current")
        out["swap_current"] = ls.observe(swap_current, at=at, source="memory.swap.current")
    except (OSError, ValueError) as exc:
        out["swap_current"] = ls.fail("memory.swap.current", at=at, reason=str(exc))
    try:
        swap_max = parse_bytes_or_max((path / "memory.swap.max").read_text())
        # 'max' means swap is not capped: a fresh OK reading whose value is the marker "unlimited".
        out["swap_max"] = ls.observe(swap_max if swap_max is not None else "unlimited",
                                     at=at, source="memory.swap.max")
    except (OSError, ValueError) as exc:
        out["swap_max"] = ls.fail("memory.swap.max", at=at, reason=str(exc))
    return out
