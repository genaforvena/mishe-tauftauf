"""A producer restores its own memory floor when a protected host drop-in shadows it.

The generated producer unit declares ``MemoryHigh=1536M``; the protected host
drop-in declares ``MemoryHigh=1G`` and wins per systemd's drop-in precedence. At
the host's throttle the producer stalls instead of finishing (2026-11-13/14: 15
slow readings runs peaked at exactly 1.0G, 8 of them EngineTimeout, each blocked
on I/O at cpu/elapsed 0.03), so the producer raises its own floor at run start,
the way a wake does. The floor is raised by writing the unit's own cgroup
``memory.high``, which leaves no ``NeedDaemonReload`` mark (2026-11-15).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import producers, signal_store as store, units

NOW = 7000.0
PY = sys.executable


@pytest.fixture
def home(tmp_path):
    store.register(tmp_path, "readings", max_age=900, subscribers=["senses"])
    return tmp_path


class FakeCgroup:
    """A stand-in for one unit's cgroup directory holding memory.high/memory.max."""

    def __init__(self, base, *, memory_high, memory_max="max"):
        self.dir = Path(base) / "cgroup"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "memory.high").write_text(memory_high, encoding="utf-8")
        (self.dir / "memory.max").write_text(memory_max, encoding="utf-8")

    def high(self) -> str:
        return (self.dir / "memory.high").read_text(encoding="utf-8")


def fake_unit(monkeypatch, unit, fake):
    """Make the producer believe it runs in ``unit``, with ``fake`` as its cgroup."""
    monkeypatch.setattr(producers.wake_memory, "running_unit_name", lambda: unit)
    monkeypatch.setattr(producers.wake_memory, "memory_high_path", lambda u: fake.dir)


def test_a_throttled_producer_raises_its_own_floor(home, monkeypatch):
    fake = FakeCgroup(home, memory_high="1073741824")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    assert producers.run(home, "readings", [PY, "-c", "print('fine')"], now=NOW, timeout=10) == 0
    assert fake.high() == str(units.PRODUCER_MEMORY_HIGH_BYTES)
    record = json.loads((home / "signals" / "readings.memory.json").read_text())
    assert record["unit"] == "mishe-producer-readings.service"
    assert record["before"] == 1073741824
    assert record["applied"] is True
    assert record["after"] >= units.PRODUCER_MEMORY_HIGH_BYTES


def test_the_floor_is_restored_before_the_check_runs(home, monkeypatch):
    """A producer that raises its floor after the check has already stalled has not been helped."""
    fake = FakeCgroup(home, memory_high="1073741824")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    target = fake.dir / "memory.high"
    code = ("import pathlib, sys; "
            "sys.exit(0 if pathlib.Path(sys.argv[1]).read_text() == sys.argv[2] else 2)")
    assert producers.run(home, "readings", [PY, "-c", code, str(target),
                                            str(units.PRODUCER_MEMORY_HIGH_BYTES)],
                         now=NOW, timeout=10) == 0


def test_a_producer_above_the_floor_is_left_alone(home, monkeypatch):
    fake = FakeCgroup(home, memory_high="2147483648")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    assert producers.run(home, "readings", [PY, "-c", "pass"], now=NOW, timeout=10) == 0
    assert fake.high() == "2147483648"


def test_a_refused_write_is_not_reported_as_applied(home, monkeypatch):
    fake = FakeCgroup(home, memory_high="1073741824")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    monkeypatch.setattr(producers.wake_memory, "_write_memory_high", lambda control_file, value: False)
    assert producers.run(home, "readings", [PY, "-c", "pass"], now=NOW, timeout=10) == 0
    record = json.loads((home / "signals" / "readings.memory.json").read_text())
    assert record["applied"] is False
    assert record["after"] == 1073741824


def test_a_process_outside_a_named_unit_records_no_claim(home, monkeypatch):
    monkeypatch.setattr(producers.wake_memory, "running_unit_name", lambda: None)
    assert producers.run(home, "readings", [PY, "-c", "pass"], now=NOW, timeout=10) == 0
    record = json.loads((home / "signals" / "readings.memory.json").read_text())
    assert record["unit"] is None
    assert record["applied"] is False


def test_the_check_still_owns_the_signal_outcome(home, monkeypatch):
    """Raising the floor must not turn a failing check into a passing one."""
    fake = FakeCgroup(home, memory_high="1073741824")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    assert producers.run(home, "readings", [PY, "-c", "import sys; sys.exit(2)"], now=NOW, timeout=10) == 0
    assert store.read(home, "readings", now=NOW).value == "RED"
