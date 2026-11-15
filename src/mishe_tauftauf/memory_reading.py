"""Kernel memory readings for the memory-samples producer: /proc text in, numbers out.

A reading that is missing or malformed is None. Callers leave a gap; nothing here
fills, carries or zeroes a value.
"""
from __future__ import annotations

from pathlib import Path


def meminfo_kib(text: str, key: str) -> int | None:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            fields = line.split()
            return int(fields[1]) if len(fields) >= 2 and fields[1].isdigit() else None
    return None


def psi_some_avg10(text: str) -> float | None:
    for line in text.splitlines():
        if line.startswith("some "):
            for part in line.split():
                if part.startswith("avg10="):
                    try:
                        return float(part[len("avg10="):])
                    except ValueError:
                        return None
    return None


def charged_kib(text: str) -> int | None:
    """Charged memory in KiB: page cache (Cached) plus kernel slab (Slab), the 12.6 GiB split Ilya asked about."""
    cached = meminfo_kib(text, "Cached")
    slab = meminfo_kib(text, "Slab")
    if cached is None or slab is None:
        return None
    return cached + slab


def read_charged(proc: Path = Path("/proc")) -> int | None:
    try:
        return charged_kib((proc / "meminfo").read_text(encoding="utf-8"))
    except OSError:
        return None


def read_memory(proc: Path = Path("/proc")) -> tuple[float | None, int | None]:
    """(memory PSI some avg10, AnonPages in KiB); each None when its file cannot be read."""
    try:
        psi = psi_some_avg10((proc / "pressure" / "memory").read_text(encoding="utf-8"))
    except OSError:
        psi = None
    try:
        anon = meminfo_kib((proc / "meminfo").read_text(encoding="utf-8"), "AnonPages")
    except OSError:
        anon = None
    return psi, anon
