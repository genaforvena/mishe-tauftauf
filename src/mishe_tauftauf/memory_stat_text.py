"""Boundary adapter: decodes cgroup v2 memory.stat and swap/limit file text.

memory_stat_reading consumes decoded counters only. The kernel's ``memory.stat`` body
(``name value`` per line) and the ``memory.swap.*`` single-value files are external
representations, so they are decoded here and nowhere else.
"""
from __future__ import annotations

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


def parse_nonnegative_int(text: str) -> int:
    """A single non-negative counter file (for example memory.swap.current)."""
    value = int(text.strip())
    if value < 0:
        raise ValueError("negative counter")
    return value


def parse_bytes_or_max(text: str):
    """A cgroup limit file: a byte count, or 'max' (no limit). Returns int, or None for 'max'."""
    token = text.strip()
    if token == "max":
        return None
    value = int(token)
    if value < 0:
        raise ValueError("negative limit")
    return value
