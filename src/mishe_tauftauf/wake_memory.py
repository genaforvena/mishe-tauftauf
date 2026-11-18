"""A unit's own memory floor, restored when a protected host drop-in shadows it.

The generated wake and producer units declare ``MemoryHigh=1536M``. A protected
host drop-in (``mishe-.service.d/zzzzzz-host-containment.conf``) declares a
lower ``MemoryHigh=1G``, and it wins over the generated value: systemd applies
the lexically later drop-in directory per directive, and the host's ``zzzzzz``
sorts after the runtime's ``10-slice`` one.

At 1 GiB the wake's engine is throttled at the ``memory.high`` mark: the kernel
reclaims and the engine stalls. Evidence 2026-11-10: the steward wake unit's
``memory.events`` read ``high 588`` (588 throttle events) with ``memory.peak``
at 1074180096 bytes, and five MiniMax wakes (health, senses, rust-plan, genome,
steward) ended by engine timeout at 1800 s with their work thrown away. The
units that ran with a higher floor produced their yields.

The same shadowing stalled a producer: the readings producer's declared 1536M
never took effect, and 15 runs on 2026-11-13/14 peaked at exactly 1.0G, 8 of
them EngineTimeout, each blocked on I/O at cpu/elapsed 0.03. A producer pinned
at its throttle cannot finish either, so it restores its own floor too.

The floor is set on the *running* unit at start, from inside the unit itself, by
writing the unit's own cgroup ``memory.high`` directly, so it does not edit the
protected host drop-in and needs no systemd reload. A later ``daemon-reload``
reverts it, because the reload re-reads the protected drop-in and writes the
host's 1G back onto every running unit's cgroup (measured 2026-11-18 03:35Z on a
running wake: one ``systemctl --user daemon-reload`` moved ``memory.high``
1610612736 -> 1073741824). Delivery reloads on every pin move, so a start-only
restore lasts only until the next one: the 03:09:56 reload left system1's engine
throttled at 1G for the rest of its run (host memory ``full`` PSI 39-61 %).
:class:`MemoryFloorGuard` therefore keeps re-asserting the floor for as long as
the unit runs, and records every revert it repaired.

It must not go through ``systemctl --user set-property --runtime``: that writes a
runtime drop-in under ``/run/user/<uid>/systemd/user.control/`` and, for a unit
that was started by a timer, leaves ``NeedDaemonReload=yes`` set on the unit even
after it exits (measured 2026-11-15). The moving-pin observation reads exactly
that property as "the on-disk unit was changed but not reloaded" and fails the
delivery (5d608cd9 rolled back at 02:37Z on
``pending daemon-reload: mishe-producer-pub-publish-privacy-gate.service``, a
producer whose timer fired inside the observation window). The cgroup write
applies the same throttle with no config-file change and no reload mark.

This is a floor, not a ceiling: it never lowers an already higher limit, and it
never touches ``MemoryMax``. A host limit below the floor stays below it — the
unit then records what it saw, so the throttle stays visible instead of being
silently worked around.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import work_boundary
from .units import WAKE_MEMORY_HIGH_BYTES

# How often a running unit re-reads its own ``memory.high``. A reload rewrites the file within
# its ~1 s, so a two-second tick bounds the throttled window to about one engine allocation
# burst, and the read costs a single sysfs open.
FLOOR_POLL_SECONDS = 2.0


def running_unit_name() -> str | None:
    """The systemd unit the calling process runs in, or None when it names none.

    The cgroup path names the running unit exactly (``.../mishe-wake@health.service``
    or ``.../mishe-producer-readings.service``), so a scratch namespace with another
    prefix is still named right.
    """
    import os
    from .proc_table import cgroup_unit_name
    return cgroup_unit_name(str(os.getpid()))


def own_unit_name(role: str, prefix: str = "mishe") -> str:
    """The unit this wake is running in, or the conventional name for ``role``."""
    return running_unit_name() or f"{prefix}-wake@{role}.service"


def _memory_bytes(control_file: Path) -> int | None:
    """A cgroup memory control file as a byte count, or None when it is not a finite number.

    The kernel prints ``max`` for an unset limit; ``int`` rejects that, so the
    caller must not treat it as a byte count.
    """
    try:
        text = control_file.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def current_memory_high(unit: str) -> int | None:
    """The unit's effective ``MemoryHigh`` in bytes, or None when it cannot be read."""
    path = memory_high_path(unit)
    return None if path is None else _memory_bytes(path / "memory.high")


def _write_memory_high(control_file: Path, value: int) -> bool:
    """Write a cgroup ``memory.high``; True on success. Never raises."""
    try:
        control_file.write_text(str(value), encoding="utf-8")
    except OSError:
        return False
    return True


def ensure_memory_high(unit: str, *, floor: int = WAKE_MEMORY_HIGH_BYTES) -> dict:
    """Raise ``unit``'s own cgroup memory throttle to ``floor`` when it sits below it.

    Writes the running unit's ``memory.high`` directly (see the module docstring
    for why not ``set-property --runtime``). Returns what happened, for the
    caller's own record: ``{"unit", "before", "after", "applied"}``. ``applied``
    is False when the unit was already at or above the floor, when its cgroup
    cannot be read, when the hard ceiling (``memory.max``) sits below the floor,
    or when the write failed — never a claim that a limit was raised when it was
    not.
    """
    path = memory_high_path(unit)
    before = None if path is None else _memory_bytes(path / "memory.high")
    record = {"unit": unit, "before": before, "after": before, "applied": False}
    if path is None:
        record["reason"] = "no readable cgroup for the unit"
        return record
    if before is None or before >= floor:
        return record
    ceiling = _memory_bytes(path / "memory.max")
    if ceiling is not None and ceiling < floor:
        record["reason"] = f"MemoryMax {ceiling} is below the floor {floor}"
        return record
    if not _write_memory_high(path / "memory.high", floor):
        record["reason"] = "the cgroup memory.high was not writable"
        return record
    after = _memory_bytes(path / "memory.high")
    record["after"] = after
    record["applied"] = after is not None and after >= floor
    return record


def ensure_wake_memory_high(unit: str, *, floor: int = WAKE_MEMORY_HIGH_BYTES) -> dict:
    """Restore a wake's own memory floor (see :func:`ensure_memory_high`)."""
    return ensure_memory_high(unit, floor=floor)


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


class MemoryFloorGuard:
    """Keeps a running unit's memory floor applied when a reload lowers it again.

    Only ownership of the running unit's cgroup makes this possible: the write is the same
    ``memory.high`` write the start-time restore performs, repeated. Counters belong in the
    caller's evidence so a recurrence is visible instead of silent — ``reverts`` counts reads
    below the floor, ``repaired`` the writes that put it back, ``failed`` those that did not.
    """

    def __init__(self, unit: str, *, floor: int = WAKE_MEMORY_HIGH_BYTES,
                 interval: float = FLOOR_POLL_SECONDS):
        self.unit, self.floor, self.interval = unit, floor, interval
        self.reverts = 0
        self.repaired = 0
        self.failed = 0
        self.last_revert_at: float | None = None
        self._path: Path | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "MemoryFloorGuard":
        """Resolve the cgroup once and begin re-checking it until :meth:`stop`."""
        self._path = memory_high_path(self.unit)
        self._thread = threading.Thread(target=self._run, name=f"memory-floor:{self.unit}",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the re-check and wait briefly for it so its counters are stable."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.interval * 2))

    def record(self) -> dict:
        return {"unit": self.unit, "floor": self.floor, "reverts": self.reverts,
                "repaired": self.repaired, "failed": self.failed,
                "last_revert_at": self.last_revert_at}

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.check()

    def check(self) -> None:
        """Re-apply the floor once when the unit's own ``memory.high`` sits below it."""
        path = self._path or memory_high_path(self.unit)
        if path is None:
            return
        self._path = path
        before = _memory_bytes(path / "memory.high")
        if before is None or before >= self.floor:
            return
        self.reverts += 1
        self.last_revert_at = time.time()
        if _write_memory_high(path / "memory.high", self.floor):
            after = _memory_bytes(path / "memory.high")
            if after is not None and after >= self.floor:
                self.repaired += 1
                return
        self.failed += 1


def record_guard(path: Path, guard: MemoryFloorGuard) -> None:
    """Add a guard's counters to an existing evidence record. Never raises.

    Called as the unit's work ends, so an unreadable record or a full disk cannot turn a
    finished run into a failed one.
    """
    from .state_files import read_json, write_json
    path = Path(path)
    try:
        record = read_json(path)
    except ValueError:
        record = None
    except OSError:
        return
    if not isinstance(record, dict):
        record = {}
    record["guard"] = guard.record()
    try:
        write_json(path, record)
    except OSError:
        return
