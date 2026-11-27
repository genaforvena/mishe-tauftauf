"""Boundary adapter: decodes the host pressure and scan write-byte samples appended to each scan stage line.

discovery.py records one stage line per scan step. The kernel's PSI files (``/proc/pressure/*``) and the
scan process's own ``/proc/self/io`` are external text representations, so they are read and decoded here
and nowhere else; discovery.py only receives the finished ``| wbytes=... psi_mem=... psi_io=...`` suffix.
"""
from __future__ import annotations

from pathlib import Path

PRESSURE_DIR = Path("/proc/pressure")
SELF_IO = Path("/proc/self/io")


def parse_psi_avg10(text: str) -> str:
    """``some avg10`` of a PSI body, with the ``full`` avg10 appended after ``/`` when the second line exists.

    Raises ValueError/IndexError on a malformed body; the caller turns that into ``?``."""
    head = text.splitlines()
    fields = dict(item.split("=", 1) for item in head[0].split()[1:])
    full = ""
    if len(head) > 1:
        full = "/" + dict(item.split("=", 1) for item in head[1].split()[1:]).get("avg10", "?")
    return fields.get("avg10", "?") + full


def parse_write_bytes(text: str) -> int:
    """The ``write_bytes:`` counter of a /proc/<pid>/io body."""
    return next(int(line.split()[1]) for line in text.splitlines() if line.startswith("write_bytes:"))


def _psi(name: str) -> str:
    try:
        return parse_psi_avg10((PRESSURE_DIR / name).read_text(encoding="ascii"))
    except (OSError, IndexError, ValueError):
        return "?"


def _write_bytes() -> int | str:
    try:
        return parse_write_bytes(SELF_IO.read_text(encoding="ascii"))
    except (OSError, StopIteration, ValueError, IndexError):
        return "?"


def stage_suffix() -> str:
    """Cheap host pressure and this scan's own write total, appended to each stage line.

    ``wbytes`` is the scan process's bytes written to storage, so a stall can be attributed to the scan
    itself; the PSI ``avg10`` values say whether the host was under memory or I/O pressure at that stage.
    Any unreadable file reads as ``?`` rather than failing the scan."""
    return f"| wbytes={_write_bytes()} psi_mem={_psi('memory')} psi_io={_psi('io')}"
