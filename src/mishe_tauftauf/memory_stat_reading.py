"""Read-only cgroup v2 memory.stat and swap readings as Signals.

Genome (2026-11-01) accepted a read-only reader as a separate function: it performs
no writes, is not called from host_capacity.decide, and does not touch the running
controller. Missing file, missing key or parse failure yields a failed Signal
(UNKNOWN), never 0. Text decoding lives in the memory_stat_text boundary adapter.
"""
from __future__ import annotations

from pathlib import Path

from . import live_signal as ls
from .memory_stat_text import (
    MEMORY_STAT_KEYS,
    parse_bytes_or_max,
    parse_memory_stat,
    parse_nonnegative_int,
)

__all__ = ["MEMORY_STAT_KEYS", "parse_bytes_or_max", "parse_memory_stat", "read_memory_stat"]


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
        swap_current = parse_nonnegative_int((path / "memory.swap.current").read_text())
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
