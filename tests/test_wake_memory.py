"""A wake restores its own memory floor when a protected host drop-in shadows it.

The generated unit declares ``MemoryHigh=1536M``; a protected host drop-in
declares ``MemoryHigh=1G`` and wins per systemd's drop-in precedence. At the
host's throttle the engine stalls and the wake runs out its budget instead of
working (2026-11-10: five MiniMax wakes timed out at 1800 s; the steward
unit's cgroup read ``memory.events high 588``).
"""
from __future__ import annotations

import subprocess

from mishe_tauftauf import wake_memory, work_boundary


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
            unit = argv[3]
            names = argv[argv.index("-p") + 1].split(",")
            values = {"MemoryHigh": self.memory_high, "MemoryMax": self.memory_max}
            lines = [f"{name}={values.get(name, '')}" for name in names]
            return subprocess.CompletedProcess(argv, 0, "\n".join(lines) + "\n", "")
        if argv[:4] == ["systemctl", "--user", "set-property", "--runtime"]:
            unit = argv[4]
            if not self.settable:
                return subprocess.CompletedProcess(argv, 1, "", "not permitted")
            self.memory_high = argv[5].split("=", 1)[1]
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


def install(monkeypatch, fake):
    monkeypatch.setattr(subprocess, "run", fake)


def test_a_throttled_unit_is_raised_to_the_floor(monkeypatch):
    fake = FakeSystemd(memory_high="1073741824")
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is True
    assert record["before"] == 1073741824
    assert record["after"] >= wake_memory.WAKE_MEMORY_HIGH_BYTES
    assert any(call[:4] == ["systemctl", "--user", "set-property", "--runtime"] for call in fake.calls)


def test_a_unit_already_above_the_floor_is_left_alone(monkeypatch):
    fake = FakeSystemd(memory_high="2147483648")
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert record["before"] == 2147483648
    assert not any(call[:4] == ["systemctl", "--user", "set-property", "--runtime"] for call in fake.calls)


def test_an_unreadable_limit_never_claims_a_raise(monkeypatch):
    fake = FakeSystemd(memory_high="infinity")
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert record["before"] is None


def test_a_hard_ceiling_below_the_floor_is_reported_not_overridden(monkeypatch):
    fake = FakeSystemd(memory_high="1073741824", memory_max="1073741824")
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert "MemoryMax" in record["reason"]
    assert fake.memory_high == "1073741824"


def test_a_refused_set_property_is_not_reported_as_applied(monkeypatch):
    fake = FakeSystemd(memory_high="1073741824", settable=False)
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is False
    assert record["after"] == 1073741824


def test_the_floor_is_never_above_the_hard_ceiling(monkeypatch):
    fake = FakeSystemd(memory_high="1073741824", memory_max=str(wake_memory.WAKE_MEMORY_HIGH_BYTES))
    install(monkeypatch, fake)
    record = wake_memory.ensure_wake_memory_high("mishe-wake@health.service")
    assert record["applied"] is True


def test_the_boundary_reads_systemd_properties_as_bytes(monkeypatch):
    fake = FakeSystemd(memory_high="1073741824")
    install(monkeypatch, fake)
    assert work_boundary.unit_memory_bytes("mishe-wake@health.service", "MemoryHigh") == 1073741824
    assert work_boundary.unit_memory_bytes("mishe-wake@health.service", "MemoryMax") == 2147483648
