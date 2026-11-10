"""A wake's own memory floor, restored when a protected host drop-in shadows it.

The generated wake unit declares ``MemoryHigh=1536M``. A protected host drop-in
(``mishe-.service.d/zzzzzz-host-containment.conf``) declares a lower
``MemoryHigh=1G``, and it wins over the generated value: systemd applies the
lexically later drop-in directory per directive, and the host's ``zzzzzz``
sorts after the runtime's ``10-slice`` one.

At 1 GiB the wake's engine is throttled at the ``memory.high`` mark: the kernel
reclaims and the engine stalls. Evidence 2026-11-10: the steward wake unit's
``memory.events`` read ``high 588`` (588 throttle events) with ``memory.peak``
at 1074180096 bytes, and five MiniMax wakes (health, senses, rust-plan, genome,
steward) ended by engine timeout at 1800 s with their work thrown away. The
units that ran with a higher floor produced their yields.

The floor is set on the *running* unit at wake start, from inside the wake
itself, so it does not edit the protected host drop-in and needs no systemd
reload. ``systemctl --user set-property --runtime`` writes the drop-in under
``/run/user/<uid>/systemd/user.control/`` and applies it to the running unit at
once.

This is a floor, not a ceiling: it never lowers an already higher limit, and it
never touches ``MemoryMax``. A host limit below the floor stays below it — the
wake then records what it saw, so the throttle stays visible instead of being
silently worked around.
"""
from __future__ import annotations

from pathlib import Path

from . import work_boundary
from .units import WAKE_MEMORY_HIGH_BYTES


def own_unit_name(role: str, prefix: str = "mishe") -> str:
    """The unit this wake is running in, or the conventional name for ``role``.

    The cgroup path names the running unit exactly (``.../mishe-wake@health.service``),
    so a scratch namespace with another prefix is still named right.
    """
    import os
    from .proc_table import cgroup_unit_name
    return cgroup_unit_name(str(os.getpid())) or f"{prefix}-wake@{role}.service"


def current_memory_high(unit: str) -> int | None:
    """The unit's effective ``MemoryHigh`` in bytes, or None when systemd cannot say."""
    return work_boundary.unit_memory_bytes(unit, "MemoryHigh")


def ensure_wake_memory_high(unit: str, *, floor: int = WAKE_MEMORY_HIGH_BYTES) -> dict:
    """Raise ``unit``'s runtime memory throttle to ``floor`` when it sits below it.

    Returns what happened, for the wake's own record: ``{"unit", "before",
    "after", "applied"}``. ``applied`` is False when the unit was already at or
    above the floor, when systemd cannot say what the limit is, when the hard
    ceiling (``MemoryMax``) sits below the floor, or when the property could
    not be set — never a claim that a limit was raised when it was not.
    """
    before = current_memory_high(unit)
    record = {"unit": unit, "before": before, "after": before, "applied": False}
    if before is None or before >= floor:
        return record
    ceiling = work_boundary.unit_memory_bytes(unit, "MemoryMax")
    if ceiling is not None and ceiling < floor:
        record["reason"] = f"MemoryMax {ceiling} is below the floor {floor}"
        return record
    applied = work_boundary.set_unit_memory_high(unit, floor)
    after = current_memory_high(unit)
    record["after"] = after
    record["applied"] = applied and after is not None and after >= floor
    return record


def memory_high_path(unit: str) -> Path | None:
    """The cgroup path of ``unit``'s memory controls, or None when unreadable.

    Used by the wake's own evidence to record the throttle it ran under, so a
    future failure can be told apart from a plain slow engine.
    """
    from .proc_table import cgroup_dir
    pid = work_boundary.unit_properties(unit, "MainPID").get("MainPID", "")
    if not pid.isdigit() or pid == "0":
        return None
    return cgroup_dir(pid)
