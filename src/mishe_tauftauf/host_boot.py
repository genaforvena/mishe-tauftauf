"""Decode this host's boot identity and uptime from procfs.

A reboot resets everything the plant reads as "since boot": the kernel's boot
id, journal windows, unit lifetimes, and the scan's own acquisition domain.
The previous scan stays on disk but describes a host that no longer exists, so
a plant that only reads the newest sample cannot tell a reboot from a quiet
host. This adapter decodes the two procfs values that describe the boot and
the newest previous scan, so the consumer acts on decoded values instead of
re-reading procfs text or scan JSON.

Boundaries:

* ``/proc/sys/kernel/random/boot_id`` — one newline-terminated uuid.
* ``/proc/uptime`` — two whitespace-separated decimal seconds: the seconds
  since boot, and the seconds spent idle.
* the plant's own previous scan receipt, whose ``acquisition.start.boot_id``
  identifies the old boot and whose ``acquisition.start.utc_ns`` precedes
  that scan's observations.

An unreadable, missing or malformed file is an explicit problem value, never
a silently absent reading: a consumer keeps the sense UNKNOWN rather than
reading a missing file as "no reboot".

The boot instant is derived by the consumer as ``now - uptime``. It is exact
while the wall clock has not stepped since the boot; a clock step shifts the
derived instant by that step, so the adapter returns the uptime and the boot
id and leaves the arithmetic to the consumer that owns the clock.
"""

from __future__ import annotations

import re
from pathlib import Path

BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
UPTIME_PATH = Path("/proc/uptime")

_BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
"""The kernel's boot id format: a uuid without braces."""

_MAX_UPTIME_SECONDS = 400 * 24 * 3600
"""An uptime beyond this is a misread value, not a host that never reboots."""


def read_boot_state(root: Path | None = None) -> dict:
    """Return the decoded boot id, uptime and any problem values.

    ``root`` is the procfs root, so a caller can read a fixture instead of the
    live host. ``problems`` names every unreadable or malformed input; an
    empty list means both values were read and are well formed.
    """
    base = Path(root) if root is not None else Path("/proc")
    problems: list[str] = []

    boot_id: str | None = None
    boot_path = base / "sys/kernel/random/boot_id"
    try:
        text = boot_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        problems.append("boot-id-missing")
    except OSError:
        problems.append("boot-id-unreadable")
    else:
        candidate = text.strip()
        if not _BOOT_ID.fullmatch(candidate):
            problems.append("boot-id-malformed")
        else:
            boot_id = candidate

    uptime_s: float | None = None
    uptime_path = base / "uptime"
    try:
        text = uptime_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        problems.append("uptime-missing")
    except OSError:
        problems.append("uptime-unreadable")
    else:
        fields = text.split()
        if not fields:
            problems.append("uptime-empty")
        else:
            try:
                value = float(fields[0])
            except ValueError:
                problems.append("uptime-malformed")
            else:
                if value < 0 or value > _MAX_UPTIME_SECONDS:
                    problems.append("uptime-out-of-range")
                else:
                    uptime_s = value

    return {"boot_id": boot_id, "uptime_s": uptime_s, "problems": problems,
            "sources": {"boot_id": str(boot_path), "uptime": str(uptime_path)}}


def previous_scan(snapshot: object) -> dict | None:
    """Decode the previous scan's boot id, timestamps and acquisition start.

    The acquisition start precedes the scan's observations and can provide a
    conservative time anchor for an outage bound. ``created`` remains the
    publication timestamp for display only.
    """
    if not isinstance(snapshot, dict):
        return None
    acquisition = snapshot.get("acquisition")
    start = acquisition.get("start") if isinstance(acquisition, dict) else None
    boot_id = start.get("boot_id") if isinstance(start, dict) else None
    start_utc_ns = start.get("utc_ns") if isinstance(start, dict) else None
    created = snapshot.get("created")
    return {"boot_id": boot_id if isinstance(boot_id, str) else None,
            "created": created if isinstance(created, str) else None,
            "acquisition_start_epoch": _utc_epoch(start_utc_ns)}


def _utc_epoch(value: object) -> float | None:
    """Decode endpoint UTC nanoseconds to epoch seconds, or None if unusable."""
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 < value <= 2**63 - 1):
        return None
    return value / 1_000_000_000


