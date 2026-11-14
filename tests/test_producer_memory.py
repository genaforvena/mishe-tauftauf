"""A producer restores its own memory floor when a protected host drop-in shadows it.

The generated producer unit declares ``MemoryHigh=1536M``; the protected host
drop-in declares ``MemoryHigh=1G`` and wins per systemd's drop-in precedence. At
the host's throttle the producer stalls instead of finishing (2026-11-13/14: 15
slow readings runs peaked at exactly 1.0G, 8 of them EngineTimeout, each blocked
on I/O at cpu/elapsed 0.03), so the producer raises its own floor at run start,
the way a wake does.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from mishe_tauftauf import producers, signal_store as store, units

NOW = 7000.0
PY = sys.executable


@pytest.fixture
def home(tmp_path):
    store.register(tmp_path, "readings", max_age=900, subscribers=["senses"])
    return tmp_path


class FakeSystemd:
    """A stand-in for systemctl that answers about one unit's memory properties."""

    def __init__(self, *, memory_high, memory_max="2147483648", settable=True):
        self.memory_high = memory_high
        self.memory_max = memory_max
        self.settable = settable
        self.calls = []

    def __call__(self, argv, capture_output=True, text=True, timeout=10, **kw):
        self.calls.append(list(argv))
        if argv[:3] == ["systemctl", "--user", "show"]:
            names = argv[argv.index("-p") + 1].split(",")
            values = {"MemoryHigh": self.memory_high, "MemoryMax": self.memory_max}
            lines = [f"{name}={values.get(name, '')}" for name in names]
            return subprocess.CompletedProcess(argv, 0, "\n".join(lines) + "\n", "")
        if argv[:4] == ["systemctl", "--user", "set-property", "--runtime"]:
            if not self.settable:
                return subprocess.CompletedProcess(argv, 1, "", "not permitted")
            self.memory_high = argv[5].split("=", 1)[1]
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


def fake_unit(monkeypatch, unit, fake):
    """Make the producer believe it runs in ``unit``, and answer about that unit."""
    monkeypatch.setattr(producers.wake_memory, "running_unit_name", lambda: unit)
    monkeypatch.setattr(producers.wake_memory, "memory_high_path", lambda u: None)
    monkeypatch.setattr(subprocess, "run", fake)


def test_a_throttled_producer_raises_its_own_floor(home, monkeypatch):
    fake = FakeSystemd(memory_high="1073741824")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    assert producers.run(home, "readings", [PY, "-c", "print('fine')"], now=NOW, timeout=10) == 0
    assert fake.memory_high == str(units.PRODUCER_MEMORY_HIGH_BYTES)
    record = json.loads((home / "signals" / "readings.memory.json").read_text())
    assert record["unit"] == "mishe-producer-readings.service"
    assert record["before"] == 1073741824
    assert record["applied"] is True
    assert record["after"] >= units.PRODUCER_MEMORY_HIGH_BYTES


def test_the_floor_is_restored_before_the_check_runs(home, monkeypatch):
    """A producer that raises its floor after the check has already stalled has not been helped."""
    seen = {}

    class Watcher(FakeSystemd):
        def __call__(self, argv, capture_output=True, text=True, timeout=10, **kw):
            if argv[:3] == ["systemctl", "--user", "show"] and argv[3].endswith("readings.service"):
                seen["high_at_check"] = self.memory_high
            return super().__call__(argv, capture_output=capture_output, text=text, timeout=timeout, **kw)

    fake = Watcher(memory_high="1073741824")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    assert producers.run(home, "readings", [PY, "-c", "pass"], now=NOW, timeout=10) == 0
    assert seen["high_at_check"] == str(units.PRODUCER_MEMORY_HIGH_BYTES)


def test_a_producer_above_the_floor_is_left_alone(home, monkeypatch):
    fake = FakeSystemd(memory_high="2147483648")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    assert producers.run(home, "readings", [PY, "-c", "pass"], now=NOW, timeout=10) == 0
    assert not any(call[:4] == ["systemctl", "--user", "set-property", "--runtime"] for call in fake.calls)


def test_a_refused_set_property_is_not_reported_as_applied(home, monkeypatch):
    fake = FakeSystemd(memory_high="1073741824", settable=False)
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
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
    fake = FakeSystemd(memory_high="1073741824")
    fake_unit(monkeypatch, "mishe-producer-readings.service", fake)
    assert producers.run(home, "readings", [PY, "-c", "import sys; sys.exit(2)"], now=NOW, timeout=10) == 0
    assert store.read(home, "readings", now=NOW).value == "RED"
