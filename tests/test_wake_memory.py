"""A wake restores its own memory floor when a protected host drop-in shadows it.

The generated unit declares ``MemoryHigh=1536M``; a protected host drop-in
declares ``MemoryHigh=1G`` and wins per systemd's drop-in precedence. At the
host's throttle the engine stalls and the wake runs out its budget instead of
working (2026-11-10: five MiniMax wakes timed out at 1800 s; the steward
unit's cgroup read ``memory.events high 588``). The floor is raised by writing
the unit's own cgroup ``memory.high``, which — unlike ``set-property --runtime``
— leaves no ``NeedDaemonReload`` mark (2026-11-15: a timer-started producer's
mark failed the moving-pin observation of 5d608cd9).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from mishe_tauftauf import wake_memory

FLOOR = wake_memory.WAKE_MEMORY_HIGH_BYTES


class FakeCgroup:
    """A stand-in for one unit's cgroup directory holding memory.high/memory.max."""

    def __init__(self, tmp_path, *, memory_high, memory_max="max"):
        self.dir = Path(tmp_path)
        (self.dir / "memory.high").write_text(memory_high, encoding="utf-8")
        (self.dir / "memory.max").write_text(memory_max, encoding="utf-8")

    def high(self) -> str:
        return (self.dir / "memory.high").read_text(encoding="utf-8")


def install(monkeypatch, fake):
    monkeypatch.setattr(wake_memory, "memory_high_path", lambda unit: fake.dir)


def test_a_throttled_unit_is_raised_to_the_floor(monkeypatch, tmp_path):
    fake = FakeCgroup(tmp_path, memory_high="1073741824")
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is True
    assert record["before"] == 1073741824
    assert record["after"] >= FLOOR
    assert fake.high() == str(FLOOR)


def test_a_unit_already_above_the_floor_is_left_alone(monkeypatch, tmp_path):
    fake = FakeCgroup(tmp_path, memory_high="2147483648")
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert record["before"] == 2147483648
    assert fake.high() == "2147483648"


def test_an_unreadable_limit_never_claims_a_raise(monkeypatch, tmp_path):
    fake = FakeCgroup(tmp_path, memory_high="max")
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert record["before"] is None


def test_a_hard_ceiling_below_the_floor_is_reported_not_overridden(monkeypatch, tmp_path):
    fake = FakeCgroup(tmp_path, memory_high="1073741824", memory_max="1073741824")
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert "MemoryMax" in record["reason"]
    assert fake.high() == "1073741824"


def test_a_refused_write_is_not_reported_as_applied(monkeypatch, tmp_path):
    fake = FakeCgroup(tmp_path, memory_high="1073741824")
    install(monkeypatch, fake)
    monkeypatch.setattr(wake_memory, "_write_memory_high", lambda control_file, value: False)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert record["after"] == 1073741824


def test_the_floor_is_never_above_the_hard_ceiling(monkeypatch, tmp_path):
    fake = FakeCgroup(tmp_path, memory_high="1073741824", memory_max=str(FLOOR))
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is True


def test_an_unreadable_cgroup_is_recorded_not_raised(monkeypatch):
    monkeypatch.setattr(wake_memory, "memory_high_path", lambda unit: None)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert record["before"] is None
    assert "cgroup" in record["reason"]


def test_a_reload_that_lowers_the_floor_is_repaired(monkeypatch, tmp_path):
    """One `daemon-reload` writes the host's 1G back; the guard must put the floor back."""
    fake = FakeCgroup(tmp_path, memory_high=str(FLOOR))
    install(monkeypatch, fake)
    guard = wake_memory.MemoryFloorGuard("mishe-wake@health.service")
    guard.check()                       # at the floor already: nothing to do
    assert (guard.reverts, guard.repaired) == (0, 0)
    (fake.dir / "memory.high").write_text("1073741824", encoding="utf-8")
    guard.check()                       # the reload's write arrives after the start
    assert guard.reverts == 1
    assert guard.repaired == 1
    assert guard.failed == 0
    assert guard.last_revert_at is not None
    assert fake.high() == str(FLOOR)


def test_a_guard_above_the_floor_counts_no_revert(monkeypatch, tmp_path):
    fake = FakeCgroup(tmp_path, memory_high=str(FLOOR))
    install(monkeypatch, fake)
    guard = wake_memory.MemoryFloorGuard("mishe-wake@health.service")
    guard.check()
    assert (guard.reverts, guard.repaired, guard.failed) == (0, 0, 0)
    assert fake.high() == str(FLOOR)


def test_a_revert_the_guard_cannot_repair_is_counted(monkeypatch, tmp_path):
    fake = FakeCgroup(tmp_path, memory_high="1073741824")
    install(monkeypatch, fake)
    monkeypatch.setattr(wake_memory, "_write_memory_high", lambda control_file, value: False)
    guard = wake_memory.MemoryFloorGuard("mishe-wake@health.service")
    guard.check()
    assert (guard.reverts, guard.repaired, guard.failed) == (1, 0, 1)
    assert fake.high() == "1073741824"


def test_the_guard_repairs_while_the_unit_runs(monkeypatch, tmp_path):
    """The thread, not just `check`, has to see a reload's write and undo it."""
    fake = FakeCgroup(tmp_path, memory_high=str(FLOOR))
    install(monkeypatch, fake)
    guard = wake_memory.MemoryFloorGuard("mishe-wake@health.service", interval=0.01).start()
    try:
        (fake.dir / "memory.high").write_text("1073741824", encoding="utf-8")
        deadline = time.monotonic() + 5.0
        while fake.high() != str(FLOOR) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fake.high() == str(FLOOR)
    finally:
        guard.stop()
    assert guard.repaired >= 1


def test_the_guard_record_is_added_to_the_evidence_not_replacing_it(tmp_path):
    path = tmp_path / "wake.memory.json"
    path.write_text('{"unit": "mishe-wake@health.service", "applied": true}', encoding="utf-8")
    guard = wake_memory.MemoryFloorGuard("mishe-wake@health.service")
    wake_memory.record_guard(path, guard)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["applied"] is True
    assert record["guard"]["unit"] == "mishe-wake@health.service"
    assert record["guard"]["reverts"] == 0
