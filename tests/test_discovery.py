from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import pytest

from mishe_tauftauf.discovery import _cpu_busy, latest, renew_scan, scan, scan_age
from mishe_tauftauf import discovery
from mishe_tauftauf.feed import Feed


def test_scan_writes_real_bounded_local_evidence(tmp_path: Path) -> None:
    home = tmp_path / "site"
    artifact = scan(home)
    snapshot = json.loads(artifact.read_text())
    assert latest(home) == snapshot
    observed = {row["id"]: row for row in snapshot["observations"]}
    assert observed["sense.proc.loadavg"]["state"] in {"verified", "unknown"}
    assert observed["sense.input.keyboard-interrupt-count"]["kind"] == "counter"
    assert observed["command.git"]["state"] == "available"
    assert observed["sense.proc.memory-pressure"]["state"] in {"verified", "unknown"}
    if observed["sense.proc.memory-pressure"]["state"] == "verified":
        assert "some avg10=" in observed["sense.proc.memory-pressure"]["sample"]
        assert "total=" in observed["sense.proc.memory-pressure"]["sample"]


    assert all("/dev/input" not in str(row["sample"]) for row in observed.values())
    shown = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                            "discover", "show"], capture_output=True, text=True)
    assert shown.returncode == 0
    assert "sense.proc.loadavg" in shown.stdout

def test_memory_pressure_reports_unavailable_source_as_unknown(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    original_read = discovery._read

    def without_pressure(path: Path, limit: int = 65536) -> str | None:
        if path == Path("/proc/pressure/memory"):
            return None
        return original_read(path, limit)

    with patch("mishe_tauftauf.discovery._read", side_effect=without_pressure):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}

    assert observed["sense.proc.memory-pressure"] == {
        "id": "sense.proc.memory-pressure",
        "state": "unknown",
        "sample": "pressure data unavailable",
        "kind": "read",
    }


def test_unchanged_scan_updates_artifact_without_log_spam(tmp_path: Path) -> None:
    home = tmp_path / "site"
    first = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": [
        {"id": "sense.value", "state": "verified", "sample": 1, "kind": "read"},
        {"id": "sense.missing", "state": "unknown", "sample": "source unavailable", "kind": "read"},
    ]}
    second = {"created": "2026-01-01T00:00:01Z", "node": "node", "observations": [
        {"id": "sense.value", "state": "verified", "sample": 2, "kind": "read"},
        {"id": "sense.missing", "state": "unknown", "sample": "source unavailable", "kind": "read"},
    ]}
    with patch("mishe_tauftauf.discovery.sample", side_effect=[first, second]):
        scan(home)
        scan(home)
    assert len(Feed(home).entries()) == 1


def test_discovery_notices_existing_cpu_class_crossings_not_numeric_drift(tmp_path: Path) -> None:
    home = tmp_path / "site"
    snapshots = [
        {"created": f"2026-01-01T00:00:0{second}Z", "node": "node", "observations": [
            {"id": "sense.proc.cpu-busy", "state": "verified", "sample": cpu, "kind": "read"},
            {"id": "sense.journal.kernel-error-count", "state": "verified",
             "sample": f"last-10min kernel-error-count={count}", "kind": "read"},
        ]}
        for second, cpu, count in [
            (0, "short-window=0.1s busy=57.0% idle=43.0%", 0),
            (1, "short-window=0.1s busy=58.0% idle=42.0%", 4),
            (2, "short-window=0.1s busy=95.0% idle=5.0% high", 4),
            (3, "short-window=0.1s busy=96.0% idle=4.0% high", 8),
        ]
    ]
    with patch("mishe_tauftauf.discovery.sample", side_effect=snapshots):
        artifacts = [scan(home) for _ in snapshots]

    entries = Feed(home).entries()
    assert len(entries) == 2
    assert "sense.proc.cpu-busy class not-high -> high" in entries[-1].body
    assert "short-window=0.1s busy=58.0% idle=42.0%" in entries[-1].body
    assert "kernel-error-count=8" in json.dumps(latest(home))
    assert all(path.exists() for path in artifacts)


def test_discovery_notices_state_availability_and_reason_transitions(tmp_path: Path) -> None:
    home = tmp_path / "site"
    snapshots = [
        {"created": f"2026-01-01T00:00:0{second}Z", "node": "node", "observations": [
            {"id": "sense.memory", "state": state, "sample": reason, "kind": "read"},
            {"id": "command.evtest", "state": command_state, "sample": command_sample,
             "kind": "declaration"},
        ]}
        for second, state, reason, command_state, command_sample in [
            (0, "verified", "12 kB", "available", "/usr/bin/evtest"),
            (1, "unknown", "memory source unreadable", "available", "/usr/bin/evtest"),
            (2, "unknown", "pressure file missing", "available", "/usr/bin/evtest"),
            (3, "unavailable", "no memory source on this host", "unavailable", "not on PATH"),
        ]
    ]
    with patch("mishe_tauftauf.discovery.sample", side_effect=snapshots):
        for _ in snapshots:
            scan(home)

    entries = Feed(home).entries()
    assert len(entries) == 4
    assert "sense.memory" in entries[1].body
    assert "memory source unreadable" in entries[1].body
    assert "pressure file missing" in entries[2].body
    assert "command.evtest" in entries[3].body
    assert "no memory source on this host" in entries[3].body


def test_thermal_identity_changes_notify_but_temperature_drift_does_not(tmp_path: Path) -> None:
    home = tmp_path / "site"
    snapshots = [
        {"created": f"2026-01-01T00:00:0{second}Z", "node": "node", "observations": [
            {"id": "sense.thermal.hwmon", "state": "verified",
             "sample": f"nvme:temp{index}({label})={value}C",
             "identity": [{"channel": f"nvme:temp{index}({label})", "parent": None}],
             "kind": "read"},
        ]}
        for second, index, label, value in [
            (0, "1", "Composite", "42.0"),
            (1, "1", "Composite", "43.0"),
            (2, "2", "Sensor", "43.0"),
        ]
    ]
    with patch("mishe_tauftauf.discovery.sample", side_effect=snapshots):
        for _ in snapshots:
            scan(home)

    entries = Feed(home).entries()
    assert len(entries) == 2
    assert "sense.thermal.hwmon" in entries[-1].body
    assert "nvme:temp2(Sensor)=43.0C" in entries[-1].body


def test_cpu_pressure_reports_valid_sample_and_unavailable_source(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    original_read = discovery._read

    def with_cpu_pressure(path: Path, limit: int = 65536) -> str | None:
        if path == Path("/proc/pressure/cpu"):
            return "some avg10=0.05 avg60=0.10 avg300=0.20 total=12345\n"
        if path == Path("/proc/pressure/memory"):
            return None
        return original_read(path, limit)

    with patch("mishe_tauftauf.discovery._read", side_effect=with_cpu_pressure):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}

    assert observed["sense.proc.cpu-pressure"] == {
        "id": "sense.proc.cpu-pressure",
        "state": "verified",
        "sample": "some avg10=0.05 avg60=0.10 avg300=0.20 total=12345",
        "kind": "read",
    }

    def malformed_cpu_pressure(path: Path, limit: int = 65536) -> str | None:
        if path == Path("/proc/pressure/cpu"):
            return "some avg10=bad avg60=0.10 avg300=0.20 total=12345\n"
        if path == Path("/proc/pressure/memory"):
            return None
        return original_read(path, limit)

    with patch("mishe_tauftauf.discovery._read", side_effect=malformed_cpu_pressure):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}

    assert observed["sense.proc.cpu-pressure"] == {
        "id": "sense.proc.cpu-pressure",
        "state": "unknown",
        "sample": "pressure data unavailable",
        "kind": "read",
    }
def test_cpu_pressure_reports_missing_source_as_unknown(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    original_read = discovery._read

    def without_cpu_pressure(path: Path, limit: int = 65536) -> str | None:
        if path == Path("/proc/pressure/cpu"):
            return None
        if path == Path("/proc/pressure/memory"):
            return None
        return original_read(path, limit)

    with patch("mishe_tauftauf.discovery._read", side_effect=without_cpu_pressure):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}

    assert observed["sense.proc.cpu-pressure"] == {
        "id": "sense.proc.cpu-pressure",
        "state": "unknown",
        "sample": "pressure data unavailable",
        "kind": "read",
    }


def test_io_pressure_requires_a_valid_some_row(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    original_read = discovery._read
    cases = (
        ("some avg10=0.05 avg60=0.10 avg300=0.20 total=12345\n"
         "full avg10=0.01 avg60=0.02 avg300=0.03 total=456\n", "verified"),
        ("some avg10=bad avg60=0.10 avg300=0.20 total=12345\n", "unknown"),
        ("full avg10=0.01 avg60=0.02 avg300=0.03 total=456\n", "unknown"),
        (None, "unknown"),
    )
    for io_sample, expected_state in cases:
        def with_io_pressure(path: Path, limit: int = 65536) -> str | None:
            if path == Path("/proc/pressure/io"):
                return io_sample
            return original_read(path, limit)

        with patch("mishe_tauftauf.discovery._read", side_effect=with_io_pressure):
            observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
        reading = observed["sense.proc.io-pressure"]
        assert reading["state"] == expected_state
        assert reading["kind"] == "read"
        if expected_state == "verified":
            assert "some avg10=0.05" in reading["sample"]
            assert "full avg10=0.01" in reading["sample"]
        else:
            assert reading["sample"] == "pressure data unavailable"


def test_inode_availability_uses_unprivileged_statvfs_count_and_fails_unknown(
        tmp_path: Path) -> None:
    from types import SimpleNamespace

    from mishe_tauftauf import discovery

    with patch("mishe_tauftauf.discovery.os.statvfs",
               return_value=SimpleNamespace(f_bavail=12, f_frsize=4096, f_favail=7)):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    assert observed["sense.disk.free"]["sample"] == 12 * 4096
    assert observed["sense.disk.inodes-available"] == {
        "id": "sense.disk.inodes-available",
        "state": "verified",
        "sample": 7,
        "kind": "read",
    }

    with patch("mishe_tauftauf.discovery.os.statvfs", side_effect=OSError):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    assert observed["sense.disk.inodes-available"] == {
        "id": "sense.disk.inodes-available",
        "state": "unknown",
        "sample": "statvfs unavailable",
        "kind": "read",
    }
    assert observed["sense.disk.free"]["state"] == "unknown"


def test_keyboard_counter_distinguishes_absent_source_from_unreadable_counter(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    original_read = discovery._read
    real_interrupts = original_read(Path("/proc/interrupts"), 65536)
    has_keyboard_line = any(re.search(r"i8042|atkbd|keyboard", line, re.IGNORECASE)
                            for line in (real_interrupts or "").splitlines())
    keyboard_line = "  12:        3        4   IR-IO-APIC    2-edge      atkbd\n"
    if has_keyboard_line:
        # This host really names a keyboard interrupt line, so only the
        # verified and unreadable branches are reachable here.
        cases = {"verified": real_interrupts, "unknown": None}
    else:
        # The source is readable but names no keyboard, which must read as a
        # structural absence rather than a transient read failure.
        cases = {"unavailable": real_interrupts, "unknown": None}
    for expected_state, interrupts in cases.items():
        def with_interrupts(path: Path, limit: int = 65536) -> str | None:
            if path == Path("/proc/interrupts"):
                return interrupts
            return original_read(path, limit)

        with patch("mishe_tauftauf.discovery._read", side_effect=with_interrupts):
            observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
        reading = observed["sense.input.keyboard-interrupt-count"]
        if expected_state == "verified":
            assert reading["state"] == "verified"
            assert reading["sample"] == 7
        else:
            assert reading["state"] == expected_state
            assert isinstance(reading["sample"], str)
            assert reading["sample"] != ""
    # A named keyboard line must always be counted, on any host.
    if not has_keyboard_line:
        def with_keyboard_line(path: Path, limit: int = 65536) -> str | None:
            if path == Path("/proc/interrupts"):
                return keyboard_line
            return original_read(path, limit)

        with patch("mishe_tauftauf.discovery._read", side_effect=with_keyboard_line):
            observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
        assert observed["sense.input.keyboard-interrupt-count"] == {
            "id": "sense.input.keyboard-interrupt-count",
            "state": "verified",
            "sample": 7,
            "kind": "counter",
        }
    # The sample must never expose device paths or key content.
    with patch("mishe_tauftauf.discovery._read", side_effect=with_interrupts):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    assert "/dev/input" not in str(observed["sense.input.keyboard-interrupt-count"]["sample"])

def test_thermal_hwmon_is_deterministic_and_real_host_is_observational(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    chip = tmp_path / "chip"
    chip.mkdir()
    valid = chip / "temp1_input"
    malformed = chip / "temp2_input"
    valid.write_text("42500\n", encoding="utf-8")
    malformed.write_text("not-a-number\n", encoding="utf-8")
    (chip / "name").write_text("testchip\n", encoding="utf-8")
    with patch("mishe_tauftauf.discovery._thermal_slots", return_value=[valid, malformed]):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    assert observed["sense.thermal.hwmon"] == {
        "id": "sense.thermal.hwmon", "state": "verified",
        "sample": "testchip:temp1=42.5C",
        "identity": [{"channel": "testchip:temp1", "parent": None}], "kind": "read",
    }

    real = discovery._thermal_slots(Path("/sys/class/hwmon"))
    readable = [
        slot for slot in real
        if (value := discovery._read(slot, 64)) is not None
        and value.strip().lstrip("-").isdigit()
        and -273150 <= int(value.strip()) <= 200000
    ]
    if readable:
        with patch("mishe_tauftauf.discovery._thermal_slots", return_value=readable):
            observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
        assert observed["sense.thermal.hwmon"]["state"] == "verified"
    with patch("mishe_tauftauf.discovery._thermal_slots", return_value=[]):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    assert observed["sense.thermal.hwmon"]["state"] == "unknown"
    assert observed["sense.thermal.hwmon"]["sample"] == "no readable hwmon temperature sensor"


def test_thermal_hwmon_reports_chip_name_and_skips_malformed_sensor(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    chip = tmp_path / "chip"
    chip.mkdir()
    synthetic = (chip / "temp1_input", chip / "temp2_input")
    for slot, value in zip(synthetic, ("42500\n", "not-a-number\n")):
        slot.write_text(value, encoding="utf-8")
    (chip / "name").write_text("testchip\n", encoding="utf-8")
    (chip / "temp1_label").write_text("Package id 0\n", encoding="utf-8")
    with patch("mishe_tauftauf.discovery._thermal_slots", return_value=list(synthetic)):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    reading = observed["sense.thermal.hwmon"]
    assert reading["state"] == "verified"
    assert reading["sample"] == "testchip:temp1(Package id 0)=42.5C"
    assert reading["identity"] == [
        {"channel": "testchip:temp1(Package id 0)", "parent": None}
    ]
    # A malformed sensor must not break the read or appear in it.
    assert "not-a-number" not in str(reading["sample"])


def test_thermal_parent_identity_distinguishes_same_named_channels(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    slots = []
    parents = []
    for name in ("nvme-a", "nvme-b"):
        chip = tmp_path / name
        chip.mkdir()
        (chip / "name").write_text("nvme\n", encoding="utf-8")
        (chip / "temp1_input").write_text("42000\n", encoding="utf-8")
        (chip / "temp1_label").write_text("Composite\n", encoding="utf-8")
        target = tmp_path / f"device-{name}"
        target.mkdir()
        (chip / "device").symlink_to(target, target_is_directory=True)
        slots.append(chip / "temp1_input")
        parents.append(str(target.resolve()))

    with patch("mishe_tauftauf.discovery._thermal_slots", return_value=slots):
        reading = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}[
            "sense.thermal.hwmon"
        ]

    # The channel itself distinguishes the two drives; the parent records the
    # device it was read from so a name change can still be reported as a move.
    assert [identity["channel"] for identity in reading["identity"]] == [
        "nvme@device-nvme-a:temp1(Composite)", "nvme@device-nvme-b:temp1(Composite)"
    ]
    assert [identity["parent"] for identity in reading["identity"]] == parents
    assert reading["sample"] == (
        "nvme@device-nvme-a:temp1(Composite)=42.0C, nvme@device-nvme-b:temp1(Composite)=42.0C"
    )
    assert len({identity["channel"] for identity in reading["identity"]}) == 2


def test_thermal_leaves_a_sole_chip_unqualified(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    # A qualifier costs bytes on a sample line the dashboard truncates, so a chip
    # whose name no other chip shares keeps the shorter form.
    chip = tmp_path / "nvme"
    chip.mkdir()
    (chip / "name").write_text("nvme\n", encoding="utf-8")
    (chip / "temp1_input").write_text("42000\n", encoding="utf-8")
    (chip / "temp1_label").write_text("Composite\n", encoding="utf-8")
    target = tmp_path / "device-nvme"
    target.mkdir()
    (chip / "device").symlink_to(target, target_is_directory=True)

    with patch("mishe_tauftauf.discovery._thermal_slots",
               return_value=[chip / "temp1_input"]):
        reading = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}[
            "sense.thermal.hwmon"
        ]

    assert reading["identity"] == [
        {"channel": "nvme:temp1(Composite)", "parent": str(target.resolve())}
    ]
    assert reading["sample"] == "nvme:temp1(Composite)=42.0C"


def test_thermal_qualifier_falls_back_when_parent_device_is_unresolvable(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    # The collision still has to be reported even when neither chip resolves a
    # parent device, so the qualifier falls back to a label that separates them
    # on this host rather than collapsing the two readings into one name.
    slots = []
    for name in ("nvme-a", "nvme-b"):
        chip = tmp_path / name
        chip.mkdir()
        (chip / "name").write_text("nvme\n", encoding="utf-8")
        (chip / "temp1_input").write_text("42000\n", encoding="utf-8")
        (chip / "temp1_label").write_text("Composite\n", encoding="utf-8")
        (chip / "device").symlink_to(tmp_path / "gone", target_is_directory=True)
        slots.append(chip / "temp1_input")

    with patch("mishe_tauftauf.discovery._thermal_slots", return_value=slots):
        reading = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}[
            "sense.thermal.hwmon"
        ]

    assert [identity["channel"] for identity in reading["identity"]] == [
        "nvme@nvme-a:temp1(Composite)", "nvme@nvme-b:temp1(Composite)"
    ]


def test_thermal_hwmon_skips_out_of_range_sensor_values(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "name").write_text("testchip\n", encoding="utf-8")
    cases = (("999000\n", 999000), ("-300000\n", -300000), ("\n", 0))
    for text, _ in cases:
        slot = chip / "temp1_input"
        slot.write_text(text, encoding="utf-8")
        with patch("mishe_tauftauf.discovery._thermal_slots",
                   return_value=[slot]):
            observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
        reading = observed["sense.thermal.hwmon"]
        assert reading["state"] == "unknown", text
        assert reading["sample"] == "no readable hwmon temperature sensor", text


def test_cpu_busy_uses_idle_subtraction_and_includes_steal(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    stat = tmp_path / "stat"
    original_read = discovery._read
    cases = [
        (["cpu 60 0 0 40 0 0 0 0 0\n", "cpu 120 0 0 60 0 0 0 20 0\n"], 80.0),
        (["cpu 0 0 0 0 0 0 0 0 0\n", "cpu 0 0 0 100 0 0 0 0 0\n"], 0.0),
    ]
    for samples, expected_busy in cases:
        with patch("mishe_tauftauf.discovery._read",
                   side_effect=lambda path, limit=65536: samples.pop(0) if path == stat else original_read(path, limit)):
            reading = discovery._cpu_busy(path=stat, samples=2, interval=0)
        assert reading == {"busy": expected_busy, "idle": 100.0 - expected_busy}

    for samples in (
        ["", "cpu 10 0 0 20 0 0 0 0 0\n"],
        ["\n", "cpu 10 0 0 20 0 0 0 0 0\n"],
        ["cpu 10 0 0 10 0 0 0 0 0\n", "cpu 9 0 0 20 0 0 0 0 0\n"],
        ["cpu 10 0 0 10 0 0 0 0 0\n", "cpu 10 0 0 10 0 0 0 0 0\n"],
    ):
        with patch("mishe_tauftauf.discovery._read",
                   side_effect=lambda path, limit=65536: samples.pop(0) if path == stat else original_read(path, limit)):
            assert _cpu_busy(path=stat, samples=2, interval=0) == {}

def test_cpu_busy_sample_is_short_window_and_unreadable_is_unknown(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    reading = observed["sense.proc.cpu-busy"]
    assert reading["kind"] == "read"
    assert reading["state"] in {"verified", "unknown"}
    if reading["state"] == "verified":
        assert "short-window=0.1s" in str(reading["sample"])
        assert "busy=" in str(reading["sample"])
        assert "idle=" in str(reading["sample"])
    with patch("mishe_tauftauf.discovery._cpu_busy", return_value={"busy": 100.0, "idle": 0.0}):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    assert observed["sense.proc.cpu-busy"]["state"] == "verified"
    assert " high" in str(observed["sense.proc.cpu-busy"]["sample"])
    with patch("mishe_tauftauf.discovery._cpu_busy", return_value={}):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    assert observed["sense.proc.cpu-busy"]["state"] == "unknown"
    assert observed["sense.proc.cpu-busy"]["sample"] == "/proc/stat cpu fields unavailable"
    assert observed["sense.proc.cpu-busy"]["kind"] == "read"


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")




_JOURNAL_BOOT = "a" * 32
_JOURNAL_NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
_JOURNAL_UPPER = 1791201600000000


def _journal_fixture(monkeypatch, output, boots=None):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return _JOURNAL_NOW

    monkeypatch.setattr(discovery, "datetime", Clock)
    monkeypatch.setattr(discovery, "_journal_boot",
                        lambda: next(boots) if boots is not None else _JOURNAL_BOOT)
    monkeypatch.setattr(discovery, "_journal_command", lambda cmd: output)
    return discovery._journal_error_window()


def _journal_record(**fields):
    return {"__CURSOR": "cursor1", "__REALTIME_TIMESTAMP": str(_JOURNAL_UPPER),
            "_BOOT_ID": _JOURNAL_BOOT, "_TRANSPORT": "kernel", "PRIORITY": "3",
            **fields}


def _journal_bytes(*records):
    return b"".join((json.dumps(record) + "\n").encode() for record in records)


@pytest.mark.parametrize("message,expected", [
    (None, "unattributed"), ([0, 255], "unattributed"),
    ("unrelated error", "unrelated error"),
    ("Failed to resubmit video URB", "Failed to resubmit video URB"),
    ("first line\nsecond line", "first line")])
def test_journal_count_is_cursor_based_and_message_only_shapes_the_class(
        monkeypatch, message, expected):
    # A malformed message must not break the read or the count; it only decides
    # which class the entry is attributed to.
    row = _journal_record()
    if message is not None:
        row["MESSAGE"] = message
    result = _journal_fixture(monkeypatch, _journal_bytes(row))
    assert result["state"] == "verified"
    assert result["count"] == 1
    assert result["classes"] == {expected: 1}


def test_journal_groups_by_reporting_source_before_the_first_colon(monkeypatch):
    # A repeating driver message and a real fault in one window must be readable
    # apart, since the count alone is dominated by the driver.
    result = _journal_fixture(monkeypatch, _journal_bytes(
        _journal_record(__CURSOR="c1", MESSAGE="uvcvideo 1-6:1.1: Failed to resubmit video URB (-1)."),
        _journal_record(__CURSOR="c2", MESSAGE="uvcvideo 1-6:1.1: Failed to resubmit video URB (-1)."),
        _journal_record(__CURSOR="c3", MESSAGE="Memory cgroup out of memory: Killed process 101894 (mesh-capcheck)."),
        _journal_record(__CURSOR="c4", MESSAGE="Out of memory: Killed process 1 (python)."),
        _journal_record(__CURSOR="c5", MESSAGE="usb 1-6: 3:1: cannot get freq at ep 0x84")))
    assert result["state"] == "verified"
    assert result["count"] == 5
    assert result["classes"] == {"uvcvideo 1-6:1.1": 2, "Memory cgroup out of memory": 1,
                                 "Out of memory": 1, "usb 1-6": 1}
    assert sum(result["classes"].values()) == result["count"]
    # Largest first, so the dominant class leads the line a reader scans.
    assert result["sample"] == ("last-10min kernel-error-count=5 uvcvideo 1-6:1.1=2 "
                                "Memory cgroup out of memory=1 Out of memory=1 usb 1-6=1")


def test_journal_sample_names_the_largest_classes_and_folds_the_tail(monkeypatch):
    limit = discovery.KERNEL_SOURCE_LIMIT
    rows = [_journal_record(__CURSOR=f"c{index}", MESSAGE=f"source{index}: failed")
            for index in range(limit + 2)]
    result = _journal_fixture(monkeypatch, _journal_bytes(*rows))
    assert result["count"] == limit + 2
    assert len(result["classes"]) == limit + 2
    assert f"source{limit - 1}=1" in result["sample"]
    assert f"source{limit}=1" not in result["sample"]
    assert "other=2" in result["sample"]


@pytest.mark.parametrize("offset,verified", [
    (-600000001, False), (-600000000, True), (0, True), (1, False)])
def test_journal_window_validates_exact_microsecond_boundaries(monkeypatch, offset, verified):
    row = _journal_record(__REALTIME_TIMESTAMP=str(_JOURNAL_UPPER + offset))
    result = _journal_fixture(monkeypatch, _journal_bytes(row))
    assert result["state"] == ("verified" if verified else "unknown")
    if verified:
        assert result["count"] == 1


@pytest.mark.parametrize("fields", [
    {"_TRANSPORT": "syslog"}, {"_BOOT_ID": "b" * 32}, {"PRIORITY": "4"},
    {"PRIORITY": ["3"]}, {"__CURSOR": ""}, {"__CURSOR": ["x"]},
    {"__REALTIME_TIMESTAMP": ["1"]}, {"__REALTIME_TIMESTAMP": "-1"},
    {"_TRANSPORT": ["kernel"]}, {"_BOOT_ID": [_JOURNAL_BOOT]}])
def test_journal_invalid_scope_or_identity_is_unknown(monkeypatch, fields):
    result = _journal_fixture(monkeypatch, _journal_bytes(_journal_record(**fields)))
    assert result["state"] == "unknown"
    assert "count" not in result


@pytest.mark.parametrize("field", list(_journal_record()))
def test_journal_missing_metadata_is_unknown(monkeypatch, field):
    row = _journal_record()
    del row[field]
    assert _journal_fixture(monkeypatch, _journal_bytes(row))["state"] == "unknown"


@pytest.mark.parametrize("output", [None, b"{\n", b"[]\n", b"\xff\n",
                                   _journal_bytes(_journal_record(), _journal_record())])
def test_journal_failed_or_invalid_acquisition_never_verifies_zero(monkeypatch, output):
    result = _journal_fixture(monkeypatch, output)
    assert result["state"] == "unknown"
    assert "count" not in result


def test_journal_empty_complete_window_and_distinct_entries(monkeypatch):
    result = _journal_fixture(monkeypatch, b"")
    assert result["state"] == "verified"
    assert result["count"] == 0
    result = _journal_fixture(monkeypatch, _journal_bytes(
        _journal_record(), _journal_record(__CURSOR="cursor2", PRIORITY="0")))
    assert result["count"] == 2


@pytest.mark.parametrize("boots", [[None], [_JOURNAL_BOOT, "b" * 32],
                                   [_JOURNAL_BOOT, None]])
def test_journal_boot_loss_or_change_invalidates_even_empty_output(monkeypatch, boots):
    assert _journal_fixture(monkeypatch, b"", iter(boots))["state"] == "unknown"


def test_journal_monotonic_regression_invalidates_output(monkeypatch):
    clock = iter([20, 19])
    monkeypatch.setattr(discovery.time, "monotonic_ns", lambda: next(clock))
    assert _journal_fixture(monkeypatch, b"")["state"] == "unknown"


def test_journal_command_rejects_incomplete_and_over_cap_output() -> None:
    import sys
    with patch("mishe_tauftauf.discovery._JOURNAL_OUTPUT_CAP", 4):
        assert discovery._journal_command([sys.executable, "-c", "print('x')"]) == b"x\n"
        assert discovery._journal_command([sys.executable, "-c", "print('x', end='')"]) is None
        assert discovery._journal_command(
            [sys.executable, "-c", "import sys; sys.stdout.write('12345')"]) is None


def test_journal_cleanup_wait_uses_only_remaining_deadline() -> None:
    class Child:
        killed = False
        wait_timeout = None

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.wait_timeout = timeout
            raise subprocess.TimeoutExpired(["journalctl"], timeout)

    child = Child()
    with patch("mishe_tauftauf.discovery.time.monotonic", return_value=10.0):
        discovery._kill_journal_process(child, 10.25)
    assert child.killed
    assert child.wait_timeout == 0.25


def _unit_failure_record(**fields):
    return {"__CURSOR": "cursor1", "__REALTIME_TIMESTAMP": str(_JOURNAL_UPPER),
            "_BOOT_ID": _JOURNAL_BOOT, "SYSLOG_IDENTIFIER": "systemd",
            "MESSAGE": "cron.service: Failed with result 'oom-kill'.", **fields}


def _unit_failure_fixture(monkeypatch, output, boots=None, restart_context=None):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return _JOURNAL_NOW

    monkeypatch.setattr(discovery, "datetime", Clock)
    monkeypatch.setattr(discovery, "_journal_boot",
                        lambda: next(boots) if boots is not None else _JOURNAL_BOOT)
    monkeypatch.setattr(discovery, "_journal_command", lambda cmd: output)
    monkeypatch.setattr(discovery, "_unit_restart_context",
                        lambda units: restart_context or {})
    return discovery._journal_unit_failure_window()


def test_unit_failure_counts_and_groups_by_unit_and_class(monkeypatch):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(),
        _unit_failure_record(__CURSOR="cursor2",
                             MESSAGE="mesh-heavy-1.scope: Failed with result 'oom-kill'."),
        _unit_failure_record(__CURSOR="cursor3",
                             MESSAGE="mesh-pull-t1.service: Failed with result 'exit-code'.")))
    assert result["state"] == "verified"
    assert result["count"] == 3
    assert result["classes"] == {"oom-kill": 2, "exit-code": 1}
    assert result["units"] == {"cron.service": 1, "mesh-heavy-1.scope": 1,
                               "mesh-pull-t1.service": 1}
    assert result["sample"] == "last-10min unit-failure-count=3 exit-code=1 oom-kill=2"


def test_unit_failure_counts_a_unit_name_containing_a_colon(monkeypatch):
    # systemd.unit(5) allows ':' in a unit name prefix.
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(MESSAGE="foo:bar.service: Failed with result 'exit-code'.")))
    assert result["state"] == "verified"
    assert result["units"] == {"foo:bar.service": 1}
    assert result["classes"] == {"exit-code": 1}


def test_unit_failure_ignores_records_that_are_not_unit_failures(monkeypatch):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(MESSAGE="Started Daily apt download activities."),
        _unit_failure_record(__CURSOR="cursor2", MESSAGE="Failed to start Foo.")))
    assert result["state"] == "verified"
    assert result["count"] == 0
    assert result["sample"] == "last-10min unit-failure-count=0"


def test_unit_failure_empty_complete_window_verifies_zero(monkeypatch):
    result = _unit_failure_fixture(monkeypatch, b"")
    assert result["state"] == "verified"
    assert result["count"] == 0


@pytest.mark.parametrize("fields", [
    {"_BOOT_ID": "b" * 32}, {"SYSLOG_IDENTIFIER": "kernel"}, {"__CURSOR": ""},
    {"__CURSOR": ["x"]}, {"__REALTIME_TIMESTAMP": ["1"]},
    {"__REALTIME_TIMESTAMP": "-1"}, {"SYSLOG_IDENTIFIER": ["systemd"]}])
def test_unit_failure_invalid_scope_or_identity_is_unknown(monkeypatch, fields):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(**fields)))
    assert result["state"] == "unknown"
    assert "count" not in result


def test_unit_failure_duplicate_cursor_is_unknown(monkeypatch):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(), _unit_failure_record()))
    assert result["state"] == "unknown"


def test_unit_failure_boot_loss_or_change_invalidates(monkeypatch):
    assert _unit_failure_fixture(monkeypatch, b"", iter([None]))["state"] == "unknown"
    assert _unit_failure_fixture(
        monkeypatch, b"", iter([_JOURNAL_BOOT, "b" * 32]))["state"] == "unknown"


def test_unit_failure_incomplete_output_is_unknown(monkeypatch):
    assert _unit_failure_fixture(monkeypatch, None)["state"] == "unknown"


@pytest.mark.parametrize("offset,verified", [
    (-600000001, False), (-600000000, True), (0, True), (1, False)])
def test_unit_failure_window_validates_exact_boundaries(monkeypatch, offset, verified):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(__REALTIME_TIMESTAMP=str(_JOURNAL_UPPER + offset))))
    assert result["state"] == ("verified" if verified else "unknown")

def test_unit_failure_includes_restart_context_in_sample(monkeypatch):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(MESSAGE="mesh-cleaner.service: Failed with result 'exit-code'.")),
        restart_context={"mesh-cleaner.service": {"restarts": 1, "active": "running"}})
    assert result["state"] == "verified"
    assert result["unit_context"] == {"mesh-cleaner.service": {"restarts": 1, "active": "running"}}
    assert "mesh-cleaner.service:restarts=1,active=running" in result["sample"]


def test_unit_failure_omits_restart_context_when_unavailable(monkeypatch):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(MESSAGE="mesh-cleaner.service: Failed with result 'exit-code'.")))
    assert result["state"] == "verified"
    assert result["unit_context"] == {}
    assert "restarts=" not in result["sample"]


def test_unit_restart_context_queries_systemctl(monkeypatch):
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class Result:
            returncode = 0
            stdout = "Id=mesh-cleaner.service\nNRestarts=1\nActiveState=running\n\n"
        return Result()
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    result = discovery._unit_restart_context(["mesh-cleaner.service"])
    assert result == {"mesh-cleaner.service": {"restarts": 1, "active": "running"}}
    assert calls[0][:3] == ["systemctl", "--user", "show"]


def test_unit_restart_context_falls_back_to_system_scope(monkeypatch):
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class Result:
            returncode = 1 if "--user" in cmd else 0
            stdout = "" if "--user" in cmd else "Id=cron.service\nNRestarts=0\nActiveState=failed\n\n"
        return Result()
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    result = discovery._unit_restart_context(["cron.service"])
    assert result == {"cron.service": {"restarts": 0, "active": "failed"}}
    assert len(calls) == 2
    assert "--user" in calls[0]
    assert "--user" not in calls[1]


def test_unit_restart_context_returns_empty_when_systemctl_unavailable(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("no systemctl")
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    result = discovery._unit_restart_context(["mesh-cleaner.service"])
    assert result == {}


def _omp_log_line(timestamp: str, message: str, **fields) -> str:
    record = {"timestamp": timestamp, "level": "debug", "pid": 1, "message": message}
    record.update(fields)
    return json.dumps(record)


def _write_omp_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_WEDGE_NOW = datetime(2026, 10, 6, 14, 0, 0, tzinfo=timezone.utc)


def _wedge_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return _WEDGE_NOW
    monkeypatch.setattr(discovery, "datetime", Clock)


def _retry(timestamp: str, token: int, source: str = "automatic-retry") -> str:
    return _omp_log_line(timestamp, "agent.continue scheduled",
                         source=source, schedulerToken=token)


def _success(timestamp: str) -> str:
    return _omp_log_line(timestamp, "agent_end maintenance routing",
                         stopReason="stop")


def test_wedge_chain_3_span_30min_is_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:30:00+00:00", 1),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _retry("2026-10-06T13:50:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 3, "span_minutes": 30.0,
                      "sources": {"automatic-retry": 3}}


def test_wedge_chain_below_3_is_not_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:50:00+00:00", 1),
        _retry("2026-10-06T13:55:00+00:00", 2),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 2, "span_minutes": 10.0,
                      "sources": {"automatic-retry": 2}}


def test_wedge_span_below_15min_is_not_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:50:00+00:00", 1),
        _retry("2026-10-06T13:55:00+00:00", 2),
        _retry("2026-10-06T13:58:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 3, "span_minutes": 10.0,
                      "sources": {"automatic-retry": 3}}


def test_wedge_chain_resets_on_success(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:00:00+00:00", 1),
        _success("2026-10-06T13:30:00+00:00"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 0, "span_minutes": 0.0, "sources": {}}


def test_wedge_no_log_returns_none(monkeypatch):
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: None)
    assert discovery._omp_continue_chain(12345, _WEDGE_NOW) is None


def test_wedge_empty_log_is_chain_zero(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 0, "span_minutes": 0.0, "sources": {}}


def test_wedge_sense_flags_wedged_mind(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:30:00+00:00", 1),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _retry("2026-10-06T13:50:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "research-methods|12345\nbody-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert len(result["suspects"]) == 1
    assert result["suspects"][0]["window"] == "research-methods"
    assert result["suspects"][0]["pid"] == 12345
    assert result["suspects"][0]["chain"] == 3
    assert result["suspects"][0]["span_minutes"] == 30.0
    assert result["suspects"][0]["sources"] == {"automatic-retry": 3}
    assert ("research-methods(pid=12345,chain=3,span=30.0min,"
            "src=automatic-retry:3)") in result["sample"]
    assert "panes=2 with_log=1" in result["sample"]


def test_wedge_sense_no_suspects(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:50:00+00:00", 1),
        _success("2026-10-06T13:55:00+00:00"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "research-methods|12345\nbody-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert result["suspects"] == []
    assert result["sample"] == "suspects=0 panes=2 with_log=1"
    assert result["panes"] == 2
    assert result["with_log"] == 1


def test_wedge_sense_tmux_failure_is_unknown(monkeypatch):
    _wedge_clock(monkeypatch)
    monkeypatch.setattr(discovery.subprocess, "run",
                        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": ""})())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "unknown"
    assert result["suspects"] == []


def test_wedge_sense_no_logs_is_unknown(monkeypatch):
    _wedge_clock(monkeypatch)
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: None)

    class FakeResult:
        returncode = 0
        stdout = "research-methods|12345\nbody-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "unknown"
    assert result["sample"] == "suspects=0 panes=2 with_log=0"
    assert result["with_log"] == 0


def test_wedge_chain_counts_all_continue_sources(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:30:00+00:00", 1, source="stream-stall-continue"),
        _retry("2026-10-06T13:40:00+00:00", 2, source="todo-reminder"),
        _retry("2026-10-06T13:50:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 3, "span_minutes": 30.0,
                      "sources": {"automatic-retry": 1,
                                  "stream-stall-continue": 1,
                                  "todo-reminder": 1}}


def test_wedge_chain_only_success_resets(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:00:00+00:00", 1),
        _omp_log_line("2026-10-06T13:10:00+00:00",
                      "agent_end maintenance routing", stopReason="error"),
        _retry("2026-10-06T13:20:00+00:00", 2),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 2, "span_minutes": 60.0,
                      "sources": {"automatic-retry": 2}}


def test_wedge_chain_records_sourceless_continue(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _omp_log_line("2026-10-06T13:00:00+00:00", "agent.continue scheduled",
                      schedulerToken=1),
        _omp_log_line("2026-10-06T13:20:00+00:00", "agent.continue scheduled",
                      schedulerToken=2),
        _omp_log_line("2026-10-06T13:40:00+00:00", "agent.continue scheduled",
                      schedulerToken=3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 3, "span_minutes": 60.0,
                      "sources": {"unknown": 3}}


def _write_patch(home: Path, name: str, record: object) -> None:
    store = home / "patches"
    store.mkdir(parents=True, exist_ok=True)
    (store / name).write_text(json.dumps(record) + "\n", encoding="utf-8")


def test_ledger_invariant_clean_store(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "applied", "delivery_verified": True})
    _write_patch(home, "b.json", {"phase": "reverted", "delivery_verified": False})
    _write_patch(home, "c.json", {"phase": "review-refused", "delivery_verified": False})
    _write_patch(home, "d.json", {"phase": "reviewed"})
    result = discovery._ledger_delivery_invariant(home)
    assert result["state"] == "verified"
    assert result["sample"] == "violations=0 records=4 bool_dv=3"
    assert result["violations"] == {}
    assert result["records"] == 4
    assert result["bool_dv"] == 3


def test_ledger_invariant_flags_phase_outside_set(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "applied", "delivery_verified": True})
    _write_patch(home, "b.json", {"phase": "reviewed", "delivery_verified": True})
    _write_patch(home, "c.json", {"phase": "prepared", "delivery_verified": False})
    result = discovery._ledger_delivery_invariant(home)
    assert result["state"] == "verified"
    assert result["sample"] == "violations=prepared:1 reviewed:1 records=3 bool_dv=3"
    assert result["violations"] == {"reviewed": 1, "prepared": 1}


def test_ledger_invariant_aggregates_same_phase(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "reviewed", "delivery_verified": True})
    _write_patch(home, "b.json", {"phase": "reviewed", "delivery_verified": False})
    result = discovery._ledger_delivery_invariant(home)
    assert result["sample"] == "violations=reviewed:2 records=2 bool_dv=2"
    assert result["violations"] == {"reviewed": 2}


def test_ledger_invariant_missing_store_is_unknown(tmp_path):
    result = discovery._ledger_delivery_invariant(tmp_path / "site")
    assert result["state"] == "unknown"
    assert result["sample"] == "patch store unavailable"
    assert result["records"] == 0
    assert result["bool_dv"] == 0


def test_ledger_invariant_empty_store_is_unknown(tmp_path):
    (tmp_path / "site" / "patches").mkdir(parents=True)
    result = discovery._ledger_delivery_invariant(tmp_path / "site")
    assert result["state"] == "unknown"
    assert result["sample"] == "records=0 bool_dv=0"


def test_ledger_invariant_unreadable_record_is_unknown(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "applied", "delivery_verified": True})
    (home / "patches" / "broken.json").write_text("not json\n", encoding="utf-8")
    result = discovery._ledger_delivery_invariant(home)
    assert result["state"] == "unknown"
    assert result["sample"] == "records=1 bool_dv=1 unreadable=broken.json"

def test_ledger_invariant_unreadable_record_keeps_detected_violation(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "reviewed", "delivery_verified": True})
    (home / "patches" / "broken.json").write_text("not json\n", encoding="utf-8")
    result = discovery._ledger_delivery_invariant(home)
    assert result["state"] == "unknown"
    assert result["sample"] == ("records=1 bool_dv=1 unreadable=broken.json "
                                "violations=reviewed:1")
    assert result["violations"] == {"reviewed": 1}


def test_ledger_invariant_non_boolean_dv_ignored(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "reviewed", "delivery_verified": "yes"})
    _write_patch(home, "b.json", {"phase": "reviewed", "delivery_verified": None})
    result = discovery._ledger_delivery_invariant(home)
    assert result["state"] == "verified"
    assert result["sample"] == "violations=0 records=2 bool_dv=0"


def test_ledger_invariant_review_refused_dv_accepted(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "culture-audit-repair.json",
                 {"phase": "review-refused", "delivery_verified": False})
    result = discovery._ledger_delivery_invariant(home)
    assert result["state"] == "verified"
    assert result["sample"] == "violations=0 records=1 bool_dv=1"


def test_ledger_invariant_missing_phase_counts_as_unknown(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"delivery_verified": True})
    result = discovery._ledger_delivery_invariant(home)
    assert result["state"] == "verified"
    assert result["sample"] == "violations=unknown:1 records=1 bool_dv=1"
    assert result["violations"] == {"unknown": 1}




def _scan_endpoint(ns: int) -> dict:
    return {"clock": "CLOCK_BOOTTIME", "bounds_ns": [ns, ns],
            "boot_id": "12345678-1234-1234-1234-123456789abc",
            "time_namespace": "time:[1]",
            "offsets": {"monotonic": [0, 0], "boottime": [0, 0]},
            "utc_ns": ns}


def test_renew_scan_refreshes_expired_evidence_without_log_spam(tmp_path: Path, monkeypatch) -> None:
    from mishe_tauftauf import scan_freshness

    home = tmp_path / "site"
    now = 1_000_000_000
    monkeypatch.setattr(discovery, "endpoint", lambda: _scan_endpoint(now))
    monkeypatch.setattr(scan_freshness, "endpoint", lambda: _scan_endpoint(now))
    value = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": [
        {"id": "sense.value", "state": "verified", "sample": 1, "kind": "read"},
    ]}
    with patch("mishe_tauftauf.discovery.sample", return_value=value):
        original = renew_scan(home)
        original_bytes = original.read_bytes()
        baseline = Feed(home).entries()
        now += 600_000_000_000
        assert scan_age(home) == 600
        assert renew_scan(home) is None
        assert (home / "discovery/latest.json").read_bytes() == original_bytes
        now += 1
        renewed = renew_scan(home)
        assert renewed != original
        assert original.read_bytes() == original_bytes
        assert scan_age(home) == 0
        assert renew_scan(home) is None
    assert Feed(home).entries() == baseline


@pytest.mark.parametrize("created", ["2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z"])
def test_legacy_scan_renews_without_promoting_wall_timestamp(tmp_path: Path, created: str) -> None:
    root = tmp_path / "discovery"
    root.mkdir()
    legacy = {"created": created, "node": "node", "observations": []}
    (root / "latest.json").write_text(json.dumps(legacy))
    assert scan_age(tmp_path) is None
    with patch("mishe_tauftauf.discovery.sample", return_value=legacy):
        artifact = renew_scan(tmp_path)
    assert artifact is not None
    assert latest(tmp_path)["scan_id"]


@pytest.mark.parametrize("stage", ["acquisition", "publication", "notification"])
def test_failed_scan_keeps_correct_receipt_and_visible_stage(tmp_path: Path, stage: str) -> None:
    value = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": []}
    with patch("mishe_tauftauf.discovery.sample", return_value=value):
        original = scan(tmp_path)
    before = original.read_bytes()
    changed = {**value, "observations": [
        {"id": "sense.value", "state": "unknown", "sample": "unreadable", "kind": "read"}]}
    original_replace = discovery.os.replace

    def fail_publication(source, destination):
        if Path(destination).name == "latest.json":
            raise OSError("controlled publication failure")
        return original_replace(source, destination)

    failure = (patch("mishe_tauftauf.discovery.sample", side_effect=OSError("controlled acquisition failure"))
               if stage == "acquisition" else
               patch("mishe_tauftauf.discovery.os.replace", side_effect=fail_publication)
               if stage == "publication" else
               patch("mishe_tauftauf.discovery.Feed.append", side_effect=OSError("controlled notification failure")))
    with patch("mishe_tauftauf.discovery.sample", return_value=changed), failure:
        with pytest.raises(OSError, match=f"controlled {stage} failure"):
            scan(tmp_path)
    assert original.read_bytes() == before
    attempt = discovery.scan_attempt(tmp_path)
    assert attempt["status"] == "failed"
    assert attempt["stage"] == stage
    if stage == "notification":
        assert latest(tmp_path)["scan_id"] == attempt["scan_id"]
        assert latest(tmp_path)["observations"] == changed["observations"]
        assert (tmp_path / "discovery" / attempt["artifact"]).read_bytes() == (tmp_path / "discovery/latest.json").read_bytes()
    else:
        assert (tmp_path / "discovery/latest.json").read_bytes() == before


def test_resident_ticks_renew_freshness_evidence_even_when_top_panes_fail(tmp_path: Path, monkeypatch) -> None:
    from mishe_tauftauf import seed, tmux

    from mishe_tauftauf import scan_freshness

    clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = 1_000_000_000
    monkeypatch.setattr(discovery, "endpoint", lambda: _scan_endpoint(now))
    monkeypatch.setattr(scan_freshness, "endpoint", lambda: _scan_endpoint(now))
    monkeypatch.setattr(tmux, "owns_session", lambda *args: True)
    stale = {"created": (clock - timedelta(seconds=601)).isoformat(),
             "node": "node", "observations": [
                 {"id": "sense.value", "state": "verified", "sample": 1, "kind": "read"},
             ]}
    fresh = {**stale, "created": clock.isoformat()}
    for stopped in (False, True):
        monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *args: stopped)
        monkeypatch.setattr(tmux, "capture_raw", lambda *args: "UNKNOWN — top-pain render failed")
        for slug in ("discover", "senses", "genome"):
            home = tmp_path / f"{slug}-{stopped}"
            with patch("mishe_tauftauf.discovery.sample", return_value=stale):
                scan(home)
            baseline = Feed(home).entries()
            now += 601_000_000_000
            with patch("mishe_tauftauf.discovery.sample", return_value=fresh):
                assert seed.tick(home, "session", slug).startswith("UNKNOWN seed")
            assert latest(home)["created"] == (fresh if slug in ("discover", "senses") else stale)["created"]
            assert Feed(home).entries() == baseline


def test_top_cpu_processes_ranks_by_sampled_rate_not_lifetime_average() -> None:
    from mishe_tauftauf import discovery

    # A long-lived pane at a high lifetime average that burns no tick in the
    # window must rank below a young process that is actually working now.
    stdout = "\n".join([
        "  42  1001  99.0  9000 /usr/bin/llama-server --port 7000",
        "   7     1 200.0     1 python3 worker.py  arg with   spaces",
        " 300    42  99.9     0 /home/user/.venv/bin/python -m long " + "x" * 400,
    ])
    jiffies = {42: [1000, 1000], 7: [1, 2], 300: [5, 10]}
    reads: list[int] = []

    def jreader(pid: int) -> int | None:
        # Two reads per pid: the baseline, then the value after the window.
        seen = reads.count(pid)
        reads.append(pid)
        return jiffies[pid][min(seen, len(jiffies[pid]) - 1)]

    with patch("mishe_tauftauf.discovery.subprocess.run",
               return_value=_completed(stdout)), \
            patch("mishe_tauftauf.discovery._cpu_jiffies", side_effect=jreader), \
            patch("mishe_tauftauf.discovery.time.sleep"):
        rows = discovery._top_cpu_processes(rows=2)
    # Five clock ticks in one second is 5% of a core, not 500%: a jiffy is 1/100 s.
    assert rows[0]["rate"] == 100.0 * 5 / discovery._CLOCK_TICK / discovery.TOP_CPU_SAMPLE_SECONDS
    assert rows[1]["rate"] == 100.0 / discovery._CLOCK_TICK / discovery.TOP_CPU_SAMPLE_SECONDS
    assert rows[0]["args"] == "/home/user/.venv/bin/python -m long " + "x" * 124
    assert len(rows[1]["args"]) == 33
    # etimes=0 ranks on its measured rate, not on an undefined lifetime division.
    assert rows[0]["etimes"] == 0 and rows[0]["pcpu"] == 99.9
    assert rows[0]["ppid"] == 42


def test_top_cpu_processes_breaks_rate_ties_on_the_lifetime_average() -> None:
    from mishe_tauftauf import discovery

    stdout = "\n".join([
        "  10  100  9.0  300 slower-lifetime",
        "  11   10  3.0   12 faster-lifetime",
    ])
    jiffies = {10: [0, 1], 11: [0, 1]}
    reads: list[int] = []

    def jreader(pid: int) -> int | None:
        seen = reads.count(pid)
        reads.append(pid)
        return jiffies[pid][min(seen, len(jiffies[pid]) - 1)]

    with patch("mishe_tauftauf.discovery.subprocess.run",
               return_value=_completed(stdout)), \
            patch("mishe_tauftauf.discovery._cpu_jiffies", side_effect=jreader), \
            patch("mishe_tauftauf.discovery.time.sleep"):
        # Both earn exactly one tick, so the tie falls to the lifetime average
        # rather than to whichever pid the dictionary happened to visit last.
        rows = discovery._top_cpu_processes()
    assert [row["pid"] for row in rows] == [10, 11]
    assert rows[0]["rate"] == rows[1]["rate"]


def test_top_cpu_processes_keeps_unmeasured_rows_below_measured_ones() -> None:
    from mishe_tauftauf import discovery

    # `ps` lines that cannot be parsed are skipped before any /proc read, so a
    # pid that never appears in the table cannot be retained by its jiffies.
    stdout = "\n".join([
        "  10    1  5.0  30 real-process",
        "not-a-pid  5.0  30 unparseable pid",
        "  11 abc  30 non-numeric pcpu",
        "  12   10  5.0  45 earns-two-ticks",
        "  13    1  2.0   9 unreadable-proc",
        "",
    ])
    # Two reads each: pid 10 earns no tick, pid 12 earns two, pid 13 has no
    # readable /proc entry on either read.
    jiffies = {10: [0, 0], 12: [45, 47], 13: [None, None]}
    reads: list[int] = []

    def jreader(pid: int) -> int | None:
        seen = reads.count(pid)
        reads.append(pid)
        return jiffies[pid][min(seen, len(jiffies[pid]) - 1)]

    with patch("mishe_tauftauf.discovery.subprocess.run",
               return_value=_completed(stdout)), \
            patch("mishe_tauftauf.discovery._cpu_jiffies", side_effect=jreader), \
            patch("mishe_tauftauf.discovery.time.sleep"):
        rows = discovery._top_cpu_processes()
    # The measured row leads; the idle row keeps its place so the retained
    # sample still names it; the unmeasurable row is retained below both.
    assert [row["pid"] for row in rows] == [12, 10, 13]
    assert rows[0]["rate"] == 100.0 * 2 / discovery._CLOCK_TICK / discovery.TOP_CPU_SAMPLE_SECONDS
    assert rows[1]["rate"] == 0.0
    assert rows[2].get("rate") is None


def test_top_cpu_processes_reports_unknown_when_ps_is_unavailable() -> None:
    from mishe_tauftauf import discovery

    original = discovery.subprocess.run
    for failing in (OSError("ps unavailable"),
                    _completed("", returncode=1),
                    subprocess.TimeoutExpired(cmd=["ps"], timeout=10)):
        def broken(*args, **kwargs):
            if isinstance(failing, BaseException):
                raise failing
            return failing
        with patch("mishe_tauftauf.discovery.subprocess.run", side_effect=broken):
            rows = discovery._top_cpu_processes()
            assert rows == []
            observed = {row["id"]: row for row in discovery.sample(Path("/nonexistent"))["observations"]}
        reading = observed["sense.proc.top-cpu"]
        assert reading["state"] == "unknown"
        assert reading["sample"] == "ps process sample unavailable"
        assert reading["kind"] == "read"
    assert discovery.subprocess.run is original


def test_sample_carries_a_bounded_top_cpu_read() -> None:
    from mishe_tauftauf import discovery

    stdout = "  42  1001  117.0  1500 /usr/bin/llama-server --port 7000\n"
    with patch("mishe_tauftauf.discovery.subprocess.run",
               return_value=_completed(stdout)), \
            patch("mishe_tauftauf.discovery._cpu_jiffies", side_effect=lambda pid: 100), \
            patch("mishe_tauftauf.discovery.time.sleep"):
        observed = {row["id"]: row for row in discovery.sample(Path("/nonexistent"))["observations"]}
    reading = observed["sense.proc.top-cpu"]
    assert reading["state"] == "verified"
    assert reading["kind"] == "read"
    assert reading["processes"] == [{"pid": 42, "ppid": 1001, "pcpu": 117.0,
                                     "etimes": 1500,
                                     "args": "/usr/bin/llama-server --port 7000",
                                     "rate": 0.0}]
    # No session was named, so the row carries no in_session key rather than
    # attributing the process to a session the scan never named.
    assert "in_session" not in reading["processes"][0]
    assert "pid=42 ppid=1001 rate=0.0% pcpu=117.0% etimes=1500s" in reading["sample"]
    assert reading["sample"].endswith(" key=delta-rate-over-1.0s")


def test_top_cpu_row_count_is_bounded_on_a_busy_host() -> None:
    from mishe_tauftauf import discovery

    stdout = "\n".join(f"  {pid}  {pid + 1}  {1.0 + pid / 10:.1f}  {pid} proc-{pid}" for pid in range(64))
    with patch("mishe_tauftauf.discovery.subprocess.run",
               return_value=_completed(stdout)), \
            patch("mishe_tauftauf.discovery._cpu_jiffies", side_effect=lambda pid: pid), \
            patch("mishe_tauftauf.discovery.time.sleep"):
        rows = discovery._top_cpu_processes()
    assert len(rows) == discovery.TOP_CPU_ROWS
    # Every process earns one tick, so the bound is reached by the row limit and
    # the tie order falls back to the lifetime average, highest first.
    rates = [row["rate"] for row in rows]
    assert rates == sorted(rates, reverse=True)
    pids = [row["pid"] for row in rows]
    assert pids == sorted(pids, reverse=True)


def test_session_server_asks_tmux_for_the_hosting_server_not_the_argv() -> None:
    from mishe_tauftauf import discovery

    # One tmux server hosts every session on a socket, and the server is raised
    # behind `systemd-run --scope`, so its argv cannot identify the session it
    # hosts. The pid has to come from tmux itself.
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["tmux", "has-session"]:
            return _completed("", returncode=0 if argv[3] != "absent-session" else 1)
        if argv[:3] == ["tmux", "display-message", "-p"]:
            return _completed("3652039\n", returncode=0)
        return _completed("", returncode=1)

    with patch("mishe_tauftauf.discovery.subprocess.run", side_effect=fake_run):
        assert discovery._session_server("mishe-self-development-current") == 3652039
        assert discovery._session_server("absent-session") is None

    def for_session(name):
        return [call for call in calls
                if name in call and call[1] in ("has-session", "display-message")]

    # An exited session still has a live server hosting others, so existence is
    # checked before the pid is trusted.
    assert for_session("mishe-self-development-current") == [
        ["tmux", "has-session", "-t", "mishe-self-development-current"],
        ["tmux", "display-message", "-p", "-t",
         "mishe-self-development-current", "#{pid}"]]
    # A session that has exited is not asked for a pid: its server is still live
    # and hosting others, so the pid would name the wrong session.
    assert for_session("absent-session") == [
        ["tmux", "has-session", "-t", "absent-session"]]


def test_session_server_reports_none_when_tmux_is_missing() -> None:
    from mishe_tauftauf import discovery

    with patch("mishe_tauftauf.discovery.shutil.which", return_value=None):
        assert discovery._session_server("mishe-self-development-current") is None
        assert discovery._session_pane_roots("mishe-self-development-current") == []


def test_session_pane_roots_reads_the_pids_tmux_reports() -> None:
    from mishe_tauftauf import discovery

    stdout = "3652080\n3654275\nnot-a-pid\n\n"
    with patch("mishe_tauftauf.discovery.subprocess.run",
               return_value=_completed(stdout)):
        assert discovery._session_pane_roots("mishe-current") == [3652080, 3654275]
    failed = _completed("can't find window: nope", returncode=1)
    with patch("mishe_tauftauf.discovery.subprocess.run", return_value=failed):
        assert discovery._session_pane_roots("nope") == []


def test_session_membership_needs_both_a_session_pane_and_the_server() -> None:
    from mishe_tauftauf import discovery

    parents = {1: 0, 10: 1, 11: 10, 12: 11, 20: 1, 21: 20, 30: 1}
    # The pane root reaches the server, so a process beneath it is a member.
    assert discovery._session_membership(12, parents, 1, [11]) is True
    # A process beneath a root that is the server itself is a member: the root
    # reaches the ancestor it is asked about.
    assert discovery._session_membership(21, parents, 1, [20]) is True
    # A process under no pane root is outside the session.
    assert discovery._session_membership(30, parents, 1, [11, 20]) is False
    # Without a server the session is unreadable, not empty.
    assert discovery._session_membership(12, parents, None, [11]) is None
    # Without pane roots the session is unreadable, not empty.
    assert discovery._session_membership(12, parents, 1, []) is None
    # A chain that ends before the only root decides nothing: the pid's parent is
    # absent from the snapshot, so the root is neither reached nor ruled out.
    assert discovery._session_membership(12, {12: 99}, 1, [11]) is None
    # A pid above the only root reaches no decision either: it does not descend
    # from the root, which is a complete no-reached answer and not evidence of a
    # broken session, so it reads outside rather than undetermined.
    assert discovery._session_membership(10, parents, 1, [11]) is False


def test_descends_from_reports_unknown_rather_than_false_for_a_broken_chain() -> None:
    from mishe_tauftauf import discovery

    parents = {10: 11, 11: 12, 12: 0}
    # 13 exited before this snapshot, so its parent is unknown: descent is
    # undetermined, not absent, and must not be reported as outside the session.
    assert discovery._descends_from(10, parents, 12) is True
    assert discovery._descends_from(10, parents, 99) is False
    assert discovery._descends_from(13, parents, 12) is None
    assert discovery._descends_from(10, {}, 12) is None
    assert discovery._descends_from(10, {10: 11, 11: 10}, 12) is None


def test_top_cpu_processes_records_descent_from_the_named_session() -> None:
    from mishe_tauftauf import discovery

    # A pane renderer and an agent inside the session both descend from a pane
    # root of the session; a systemd-supervised seed and an unrelated tool do
    # not. Both outside rows name a parent the snapshot contains, so their chains
    # reach ppid 0 and "outside" is a complete decision rather than a broken one.
    stdout = "\n".join([
        "    1     0  0.0  9000 /sbin/init",
        " 1236     1  0.0  9000 /lib/systemd/systemd --user",
        " 1584  1236  0.1  9000 tmux new-session -d -s s1 -n docs -c /srv sh",
        " 2461  1584  5.0   30 pane-renderer",
        " 9001  2461  3.0   12 /opt/bin/omp --model any",
        " 4039  1236  2.0   60 supervised-seed",
        " 7000  6999  4.0    2 unrelated-tool",
        " 6999     0  0.0   60 parent-with-no-parent",
    ])

    def fake_run(argv, **kwargs):
        if argv[:2] == ["tmux", "has-session"]:
            return _completed("", returncode=0)
        if argv[:3] == ["tmux", "display-message", "-p"]:
            return _completed("1584\n", returncode=0)
        if argv[:2] == ["tmux", "list-panes"]:
            return _completed("2461\n", returncode=0)
        return _completed(stdout, returncode=0)

    with patch("mishe_tauftauf.discovery.subprocess.run", side_effect=fake_run), \
            patch("mishe_tauftauf.discovery._cpu_jiffies", side_effect=lambda pid: pid), \
            patch("mishe_tauftauf.discovery.time.sleep"):
        rows = discovery._top_cpu_processes("s1")
    by_pid = {row["pid"]: row for row in rows}
    # The pane root reaches the server, so it and the agent beneath it are members.
    assert by_pid[2461]["in_session"] is True
    assert by_pid[9001]["in_session"] is True
    # The server is not itself a pane root, so it is not a member: membership is
    # claimed per pane, not per server, and one server hosts every session.
    assert by_pid[1584]["in_session"] is False
    assert by_pid[4039]["in_session"] is False
    assert by_pid[7000]["in_session"] is False
    assert "in-session" in discovery._row_text(by_pid[2461])
    assert "outside-session" in discovery._row_text(by_pid[4039])


def test_top_cpu_processes_reports_unknown_descent_when_the_server_is_absent() -> None:
    from mishe_tauftauf import discovery

    stdout = "\n".join([
        " 1236     1  0.0  9000 /lib/systemd/systemd --user",
        " 2461  1236  5.0   30 pane-renderer",
    ])
    def fake_run(argv, **kwargs):
        # The session does not exist, so has-session fails before a pid or any
        # pane root is asked for.
        if argv[:2] == ["tmux", "has-session"]:
            return _completed("", returncode=1)
        return _completed(stdout, returncode=0)

    with patch("mishe_tauftauf.discovery.subprocess.run", side_effect=fake_run), \
            patch("mishe_tauftauf.discovery._cpu_jiffies", side_effect=lambda pid: pid), \
            patch("mishe_tauftauf.discovery.time.sleep"):
        rows = discovery._top_cpu_processes("no-such-session")
    by_pid = {row["pid"]: row for row in rows}
    # The named session is not running, so descent is undetermined for every row
    # rather than being reported as outside it.
    assert by_pid[2461]["in_session"] is None
    assert "in-session" not in discovery._row_text(by_pid[2461])
    assert "outside-session" not in discovery._row_text(by_pid[2461])


def test_top_cpu_processes_treats_an_empty_session_as_unnamed() -> None:
    from mishe_tauftauf import discovery

    stdout = "\n".join([
        " 1236     1  0.0  9000 /lib/systemd/systemd --user",
        " 2461  1236  5.0   30 pane-renderer",
    ])

    def fake_run(argv, **kwargs):
        # An empty tmux target resolves to the calling session and would answer
        # for a session nobody named, so no tmux call may be reached at all.
        assert argv[0] != "tmux", f"tmux was consulted for an empty session: {argv}"
        return _completed(stdout, returncode=0)

    with patch("mishe_tauftauf.discovery.subprocess.run", side_effect=fake_run), \
            patch("mishe_tauftauf.discovery._cpu_jiffies", side_effect=lambda pid: pid), \
            patch("mishe_tauftauf.discovery.time.sleep"):
        rows = discovery._top_cpu_processes("")
    # No session was named, so descent is not assessed rather than attributed to
    # the session that happened to run the scan.
    assert all("in_session" not in row for row in rows)
    assert rows and rows[0]["pid"] == 2461


def _unit_block(ident: str, pythonpath: str) -> str:
    """One `systemctl --user show` record naming an effective PYTHONPATH."""
    return f"Id={ident}\nEnvironment=PYTHONPATH={pythonpath}\n"


def test_import_root_drops_a_trailing_src_component() -> None:
    from mishe_tauftauf import discovery

    assert discovery._import_root("/srv/releases/abc/src") == "/srv/releases/abc"
    assert discovery._import_root("/srv/checkout") == "/srv/checkout"
    assert discovery._import_root("") is None


def _root_with_sensors(root: Path, identifiers: list[str]) -> Path:
    """Write a release-shaped source root emitting the named sensor ids."""
    (root / "src" / "mishe_tauftauf").mkdir(parents=True, exist_ok=True)
    lines = ["def sample(home):", "    observed = []"]
    for identifier in identifiers:
        lines.append(f'    observed.append({{"id": "{identifier}", "kind": "read"}})')
    lines.append("    return observed")
    (root / "src" / "mishe_tauftauf" / "discovery.py").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_sensor_names_reads_the_ids_a_source_root_can_emit() -> None:
    from mishe_tauftauf import discovery

    root = _root_with_sensors(Path("/tmp") / f"sensor-names-{os.getpid()}", [
        "sense.proc.top-cpu", "sense.tmux.windows"])
    try:
        names, failure = discovery._sensor_names(str(root))
    finally:
        shutil.rmtree(root)
    assert failure is None
    assert names == {"sense.proc.top-cpu", "sense.tmux.windows"}


def test_sensor_names_reports_an_unparseable_release_instead_of_importing_it() -> None:
    from mishe_tauftauf import discovery

    root = _root_with_sensors(Path("/tmp") / f"sensor-broken-{os.getpid()}", [])
    (root / "src" / "mishe_tauftauf" / "discovery.py").write_text(
        "def broken(:\n", encoding="utf-8")
    try:
        names, failure = discovery._sensor_names(str(root))
    finally:
        shutil.rmtree(root)
    assert names == set()
    assert failure.startswith("module unparseable: invalid syntax")


def test_sensor_coverage_names_a_sensor_the_pin_emits_but_a_service_dropped() -> None:
    from mishe_tauftauf import discovery

    pin = _root_with_sensors(Path("/tmp") / f"sensor-pin-{os.getpid()}", [
        "sense.proc.top-cpu", "sense.runtime.drift"])
    stale = _root_with_sensors(Path("/tmp") / f"sensor-stale-{os.getpid()}", [
        "sense.runtime.drift"])
    try:
        with patch("mishe_tauftauf.discovery._pinned_root",
                   return_value=(str(pin), None)):
            reading = discovery._sensor_coverage(Path("/tmp"), [str(stale)])
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(stale)
    assert reading["state"] == "drift"
    assert reading["sample"] == f"{stale.name}=1 {stale.name} missing=sense.proc.top-cpu"
    assert reading["identity"]["missing"] == {str(stale): ["sense.proc.top-cpu"]}
    assert reading["identity"]["extra"] == {}


def test_sensor_coverage_reports_verified_when_a_service_emits_the_pins_set() -> None:
    from mishe_tauftauf import discovery

    pin = _root_with_sensors(Path("/tmp") / f"sensor-match-pin-{os.getpid()}", [
        "sense.proc.top-cpu", "sense.runtime.drift"])
    live = _root_with_sensors(Path("/tmp") / f"sensor-match-live-{os.getpid()}", [
        "sense.runtime.drift", "sense.proc.top-cpu"])
    try:
        with patch("mishe_tauftauf.discovery._pinned_root",
                   return_value=(str(pin), None)):
            reading = discovery._sensor_coverage(Path("/tmp"), [str(live)])
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(live)
    assert reading["state"] == "verified"
    assert reading["sample"] == f"{live.name}=2"


def test_sensor_coverage_stays_unknown_without_an_imported_root() -> None:
    from mishe_tauftauf import discovery

    reading = discovery._sensor_coverage(Path("/tmp"), [])
    assert reading["state"] == "unknown"
    assert reading["sample"] == "no imported root to read"


def test_sensor_coverage_keeps_an_unreadable_pin_unknown_without_claiming_drift() -> None:
    from mishe_tauftauf import discovery

    live = _root_with_sensors(Path("/tmp") / f"sensor-weakpin-{os.getpid()}", [
        "sense.proc.top-cpu"])
    try:
        with patch("mishe_tauftauf.discovery._pinned_root",
                   return_value=(None, "pin invalid: not a clean worktree")):
            reading = discovery._sensor_coverage(Path("/tmp"), [str(live)])
    finally:
        shutil.rmtree(live)
    # The pin's own sensor set is unreadable, so the comparison has no ground
    # truth and the read stays unknown rather than naming every sensor missing.
    assert reading["state"] == "unknown"
    assert "pin sensor set unreadable: pin invalid: not a clean worktree" in reading["sample"]


def test_sensor_coverage_names_a_sensor_a_service_added_that_the_pin_lacks() -> None:
    from mishe_tauftauf import discovery

    pin = _root_with_sensors(Path("/tmp") / f"sensor-extra-pin-{os.getpid()}", [
        "sense.runtime.drift"])
    live = _root_with_sensors(Path("/tmp") / f"sensor-extra-live-{os.getpid()}", [
        "sense.runtime.drift", "sense.tmux.windows"])
    try:
        with patch("mishe_tauftauf.discovery._pinned_root",
                   return_value=(str(pin), None)):
            reading = discovery._sensor_coverage(Path("/tmp"), [str(live)])
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(live)
    assert reading["state"] == "drift"
    assert reading["sample"] == f"{live.name}=2 {live.name} added=sense.tmux.windows"

def test_sensor_coverage_exempts_the_declared_checkout_leading_the_pin() -> None:
    from mishe_tauftauf import discovery

    checkout = _root_with_sensors(Path("/tmp") / f"sensor-checkout-{os.getpid()}", [
        "sense.runtime.drift", "sense.tmux.windows"])
    home = checkout / "site"
    home.mkdir()
    pin = _root_with_sensors(Path("/tmp") / f"sensor-declared-pin-{os.getpid()}", [
        "sense.runtime.drift"])
    try:
        with patch("mishe_tauftauf.discovery._pinned_root",
                   return_value=(str(pin), None)):
            reading = discovery._sensor_coverage(home, [str(checkout)])
    finally:
        shutil.rmtree(checkout)
        shutil.rmtree(pin)
    # The coordinator is declared to import the development checkout, which may
    # lead the pin by a commit; an extra sensor there is expected, not drift.
    assert reading["state"] == "verified"
    assert reading["sample"] == f"{checkout.name}=2"
    assert reading["identity"]["extra"] == {}


def test_sensor_coverage_still_flags_a_sensor_the_declared_checkout_dropped() -> None:
    from mishe_tauftauf import discovery

    checkout = _root_with_sensors(Path("/tmp") / f"sensor-checkout-drop-{os.getpid()}", [
        "sense.runtime.drift"])
    home = checkout / "site"
    home.mkdir()
    pin = _root_with_sensors(Path("/tmp") / f"sensor-declared-pin-drop-{os.getpid()}", [
        "sense.runtime.drift", "sense.proc.top-cpu"])
    try:
        with patch("mishe_tauftauf.discovery._pinned_root",
                   return_value=(str(pin), None)):
            reading = discovery._sensor_coverage(home, [str(checkout)])
    finally:
        shutil.rmtree(checkout)
        shutil.rmtree(pin)
    # The exemption is one-directional: a sensor the checkout drops is the
    # 2026-10-04 frontier regression and must still read drift.
    assert reading["state"] == "drift"
    assert reading["sample"] == f"{checkout.name}=1 {checkout.name} missing=sense.proc.top-cpu"
    assert reading["identity"]["missing"] == {str(checkout): ["sense.proc.top-cpu"]}



def _top_pain(home: Path, slug: str, body: str) -> None:
    directory = home / "top-pains"
    directory.mkdir(exist_ok=True)
    path = directory / slug
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _renderer_root(root: Path, modules: dict[str, str]) -> Path:
    package = root / "src" / "mishe_tauftauf"
    package.mkdir(parents=True, exist_ok=True)
    for name, source in modules.items():
        (package / f"{name}.py").write_text(source, encoding="utf-8")
    return root


def test_top_pain_roots_expands_the_scripts_home_variable() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              "home=/srv/site\nexport PYTHONPATH=$home/artifacts/trial/runtime/src\n"
              "exec python -m mishe_tauftauf.wall_view --home \"$home\" --role senses\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert roots == [("/srv/site/artifacts/trial/runtime", "wall_view", "exported", "senses")]
    assert uncovered == []


def test_top_pain_roots_reads_every_entry_a_script_names() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "health",
              "export PYTHONPATH=/srv/snapshot/src\n"
              "python -m mishe_tauftauf.seed_culture_views --view health\n"
              "exec python -m mishe_tauftauf.wall_view --role health\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert roots == [("/srv/snapshot", "seed_culture_views", "exported", "health"),
                     ("/srv/snapshot", "wall_view", "exported", "health")]
    assert uncovered == []


def test_top_pain_roots_returns_an_inherited_renderer_with_marker() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "permissions",
              "exec python -m mishe_tauftauf.seed_culture_views --view permissions\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert roots == [("", "seed_culture_views", "inherited", "permissions")]
    assert uncovered == []


def test_top_pain_roots_reports_an_unresolvable_import_root() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              "export PYTHONPATH=$elsewhere/src\nexec python -m mishe_tauftauf.wall_view\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert roots == []
    assert uncovered == []
    assert failure == "top-pain senses import root unresolvable"


def test_top_pain_roots_reads_a_quoted_pythonpath() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              'export PYTHONPATH="/srv/snapshot/src"\n'
              "exec python -m mishe_tauftauf.wall_view\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert roots == [("/srv/snapshot", "wall_view", "exported", "senses")]
    assert uncovered == []


def test_top_pain_roots_leaves_a_longer_variable_unresolved() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              "home=/srv/site\nexport PYTHONPATH=$home_extra/src\n"
              "exec python -m mishe_tauftauf.wall_view\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert roots == []
    assert uncovered == []
    assert failure == "top-pain senses import root unresolvable"


def test_package_imports_reads_relative_and_absolute_forms() -> None:
    from mishe_tauftauf import discovery

    names = discovery._package_imports(
        "from . import seed_culture_views\n"
        "from .ci_watch import line\n"
        "from mishe_tauftauf import observations\n"
        "from mishe_tauftauf.feed import Feed\n"
        "import mishe_tauftauf.wall\n"
        "import os\n")
    assert names == {"seed_culture_views", "ci_watch", "observations", "feed", "wall"}


def test_renderer_coverage_names_a_renderer_root_stale_against_the_pin() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"renderer-pin-{os.getpid()}",
                         {"wall_view": "VALUE = 1\n"})
    renderer = _renderer_root(Path("/tmp") / f"renderer-stale-{os.getpid()}",
                              {"wall_view": "VALUE = 2\n"})
    _top_pain(home, "senses",
              f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value={}):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    assert reading["state"] == "drift"
    assert reading["sample"] == "renderers=1 drift=wall_view=wall_view"
    assert reading["identity"]["drift"] == [
        {"root": str(renderer), "entry": "wall_view", "modules": ["wall_view"]}]


def test_renderer_coverage_names_a_module_the_renderer_root_lacks() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    source = {"wall_view": "from . import feed\n", "feed": "VALUE = 1\n"}
    pin = _renderer_root(Path("/tmp") / f"renderer-closure-pin-{os.getpid()}", dict(source))
    renderer = _renderer_root(Path("/tmp") / f"renderer-closure-short-{os.getpid()}",
                              {"wall_view": source["wall_view"]})
    _top_pain(home, "senses",
              f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value={}):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    assert reading["state"] == "drift"
    assert reading["identity"]["drift"] == [
        {"root": str(renderer), "entry": "wall_view", "modules": ["feed"]}]


def test_renderer_coverage_reports_verified_when_the_renderer_matches_the_pin() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    modules = {"wall_view": "from . import feed\n", "feed": "VALUE = 1\n"}
    pin = _renderer_root(Path("/tmp") / f"renderer-match-pin-{os.getpid()}", dict(modules))
    renderer = _renderer_root(Path("/tmp") / f"renderer-match-live-{os.getpid()}", dict(modules))
    _top_pain(home, "senses",
              f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value={}):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    assert reading["state"] == "verified"
    assert reading["sample"] == "renderers=1"


def test_renderer_coverage_is_unavailable_without_a_package_renderer() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    reading = discovery._renderer_coverage(home)
    assert reading["state"] == "unavailable"
    assert reading["sample"] == "no Top Pain runs a package module"


def test_renderer_coverage_keeps_an_unreadable_pin_unknown() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    renderer = _renderer_root(Path("/tmp") / f"renderer-weakpin-{os.getpid()}",
                              {"wall_view": "VALUE = 1\n"})
    _top_pain(home, "senses",
              f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root",
                   return_value=(None, "pin invalid: not a clean worktree")):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(renderer)
    assert reading["state"] == "unknown"
    assert reading["sample"] == "pin unreadable: pin invalid: not a clean worktree"

def test_pane_info_reads_role_names_from_start_commands() -> None:
    from mishe_tauftauf import discovery
    stdout = (
        "%0|4101|env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf pain watch senses --interval 5|/srv/site\n"
        "%1|4102|env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf pain watch health --interval 5|/srv/site\n"
        "%2|4103||/srv/site\n"
    )
    with patch("mishe_tauftauf.discovery.subprocess.run", return_value=_completed(stdout)):
        info = discovery._pane_info()
    assert info == {
        "senses": ("env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf pain watch senses --interval 5", "/srv/site", "4101"),
        "health": ("env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf pain watch health --interval 5", "/srv/site", "4102"),
    }


def test_pane_info_returns_empty_when_tmux_fails() -> None:
    from mishe_tauftauf import discovery
    with patch("mishe_tauftauf.discovery.subprocess.run", return_value=_completed("", returncode=1)):
        assert discovery._pane_info() == {}


def test_renderer_coverage_resolves_inherited_renderer_from_pane() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    modules = {"wall_view": "VALUE = 1\n"}
    pin = _renderer_root(Path("/tmp") / f"renderer-inherit-pin-{os.getpid()}", dict(modules))
    renderer = _renderer_root(Path("/tmp") / f"renderer-inherit-live-{os.getpid()}", dict(modules))
    _top_pain(home, "permissions",
              "exec python -m mishe_tauftauf.seed_culture_views --view permissions\n")
    pane = {"permissions": (f"env PYTHONPATH={renderer}/src python -m mishe_tauftauf pain watch permissions", str(home), "")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    assert reading["state"] == "verified"
    assert reading["sample"] == "renderers=1"
    assert reading["identity"]["renderers"] == [f"{renderer}:seed_culture_views:inherited"]

def test_renderer_coverage_identity_shows_effective_root_for_inherited_renderer() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    modules = {"wall_view": "VALUE = 1\n"}
    pin = _renderer_root(Path("/tmp") / f"renderer-eff-pin-{os.getpid()}", dict(modules))
    live = _renderer_root(Path("/tmp") / f"renderer-eff-live-{os.getpid()}", dict(modules))
    _top_pain(home, "permissions",
              "exec python -m mishe_tauftauf.seed_culture_views --view permissions\n")
    pane = {"permissions": (f"env PYTHONPATH={live}/src python -m mishe_tauftauf pain watch permissions", str(home), "")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(live)
    assert reading["state"] == "verified"
    assert reading["identity"]["renderers"] == [f"{live}:seed_culture_views:inherited"]
    assert reading["identity"]["renderers"] != [f"{pin}:seed_culture_views:inherited"]


def test_renderer_coverage_reports_unknown_for_unresolvable_inherited_renderer() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "permissions",
              "exec python -m mishe_tauftauf.seed_culture_views --view permissions\n")
    with patch("mishe_tauftauf.discovery._pinned_root", return_value=("/srv/pin", None)), \
            patch("mishe_tauftauf.discovery._pane_info", return_value={}):
        reading = discovery._renderer_coverage(home)
    assert reading["state"] == "unknown"
    assert "inherited_unknown=permissions" in reading["sample"]


def test_renderer_coverage_detects_cwd_shadowing() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"renderer-shadow-pin-{os.getpid()}",
                         {"wall_view": "VALUE = 1\n"})
    renderer = _renderer_root(Path("/tmp") / f"renderer-shadow-live-{os.getpid()}",
                              {"wall_view": "VALUE = 1\n"})
    cwd = Path("/tmp") / f"renderer-shadow-cwd-{os.getpid()}"
    cwd_package = cwd / "mishe_tauftauf"
    cwd_package.mkdir(parents=True)
    (cwd_package / "wall_view.py").write_text("VALUE = 2\n", encoding="utf-8")
    _top_pain(home, "senses",
              f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    pane = {"senses": (f"env PYTHONPATH={renderer}/src python -m mishe_tauftauf pain watch senses", str(cwd), "")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
        shutil.rmtree(cwd)
    assert reading["state"] == "drift"
    assert reading["identity"]["drift"] == [
        {"root": str(cwd), "entry": "wall_view", "modules": ["wall_view"]}]


def test_renderer_coverage_ignores_cwd_without_package() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    modules = {"wall_view": "VALUE = 1\n"}
    pin = _renderer_root(Path("/tmp") / f"renderer-noshadow-pin-{os.getpid()}", dict(modules))
    renderer = _renderer_root(Path("/tmp") / f"renderer-noshadow-live-{os.getpid()}", dict(modules))
    cwd = Path("/tmp") / f"renderer-noshadow-cwd-{os.getpid()}"
    cwd.mkdir(parents=True)
    _top_pain(home, "senses",
              f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    pane = {"senses": (f"env PYTHONPATH={renderer}/src python -m mishe_tauftauf pain watch senses", str(cwd), "")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
        shutil.rmtree(cwd)
    assert reading["state"] == "verified"
    assert reading["sample"] == "renderers=1"


def test_top_pain_roots_marks_a_conditional_export_unresolved() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              'if [ -z "$PYTHONPATH" ]; then export PYTHONPATH=$home/snapshot/src; fi\n'
              "exec python -m mishe_tauftauf.wall_view\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert uncovered == []
    assert roots == [("", "wall_view", "conditional", "senses")]


def test_top_pain_roots_marks_a_guarded_export_block_unresolved() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              'if [ -z "$PYTHONPATH" ]; then\n'
              "  export PYTHONPATH=/srv/snapshot/src\n"
              "fi\n"
              "exec python -m mishe_tauftauf.wall_view\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert roots == [("", "wall_view", "conditional", "senses")]


def test_top_pain_roots_marks_a_self_referential_export_unresolved() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              "export PYTHONPATH=/srv/pin/src:$PYTHONPATH\n"
              "exec python -m mishe_tauftauf.wall_view\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert roots == [("", "wall_view", "conditional", "senses")]


def test_top_pain_roots_names_a_renderer_with_no_package_entry() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "observability", "printf '%s\\n' 'pane liveness'\n")
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert roots == []
    assert uncovered == ["observability"]


def test_top_pain_roots_marks_an_export_naming_no_root_unresolved() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              'export PYTHONPATH=""\nexec python -m mishe_tauftauf.wall_view\n')
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert uncovered == []
    assert roots == [("", "wall_view", "unresolvable", "senses")]


def test_top_pain_roots_marks_a_bare_separator_export_unresolved() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              'export PYTHONPATH=":"\nexec python -m mishe_tauftauf.wall_view\n')
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert uncovered == []
    assert roots == [("", "wall_view", "unresolvable", "senses")]


def test_top_pain_roots_ignores_a_file_that_cannot_render() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "observability", "printf '%s\\n' 'pane liveness'\n")
    (home / "top-pains" / "observability").chmod(0o644)
    roots, uncovered, failure = discovery._top_pain_roots(home)
    assert failure is None
    assert roots == []
    assert uncovered == []


def test_renderer_coverage_names_renderers_without_a_package_entry() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    modules = {"wall_view": "VALUE = 1\n"}
    pin = _renderer_root(Path("/tmp") / f"renderer-uncovered-pin-{os.getpid()}", dict(modules))
    renderer = _renderer_root(Path("/tmp") / f"renderer-uncovered-live-{os.getpid()}", dict(modules))
    _top_pain(home, "senses",
              f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    _top_pain(home, "observability", "printf 'pane liveness\\n'\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value={}):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    assert reading["state"] == "verified"
    assert reading["sample"] == "renderers=1 uncovered=observability"
    assert reading["identity"]["uncovered"] == ["observability"]


def test_renderer_coverage_reports_a_conditional_export_unknown() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              'if [ -z "$PYTHONPATH" ]; then export PYTHONPATH=/srv/snapshot/src; fi\n'
              "exec python -m mishe_tauftauf.wall_view\n")
    with patch("mishe_tauftauf.discovery._pinned_root", return_value=("/srv/pin", None)), \
            patch("mishe_tauftauf.discovery._pane_info", return_value={}):
        reading = discovery._renderer_coverage(home)
    assert reading["state"] == "unknown"
    assert reading["sample"] == "renderers=1 conditional_unknown=senses"


def test_renderer_coverage_reports_an_export_naming_no_root_unknown() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    _top_pain(home, "senses",
              'export PYTHONPATH=""\nexec python -m mishe_tauftauf.wall_view\n')
    with patch("mishe_tauftauf.discovery._pinned_root", return_value=("/srv/pin", None)), \
            patch("mishe_tauftauf.discovery._pane_info", return_value={}):
        reading = discovery._renderer_coverage(home)
    assert reading["state"] == "unknown"
    assert reading["sample"] == "renderers=1 export_unknown=senses"
    assert reading["identity"]["renderers"] == [":wall_view:unresolvable"]


def test_renderer_coverage_prefers_the_pane_environment_over_the_command() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    modules = {"seed_culture_views": "VALUE = 1\n"}
    pin = _renderer_root(Path("/tmp") / f"renderer-env-pin-{os.getpid()}", dict(modules))
    env_root = _renderer_root(Path("/tmp") / f"renderer-env-live-{os.getpid()}", dict(modules))
    cmd_root = _renderer_root(Path("/tmp") / f"renderer-env-cmd-{os.getpid()}",
                              {"seed_culture_views": "VALUE = 2\n"})
    _top_pain(home, "permissions",
              "exec python -m mishe_tauftauf.seed_culture_views --view permissions\n")
    pane = {"permissions": (f"env PYTHONPATH={cmd_root}/src python -m mishe_tauftauf pain watch permissions", str(home), "999")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane), \
                patch("mishe_tauftauf.discovery._pane_pythonpath", return_value=[f"{env_root}/src"]):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(env_root)
        shutil.rmtree(cmd_root)
    assert reading["state"] == "verified"
    assert reading["identity"]["drift"] == []


def test_pane_pythonpath_reads_a_live_process_environment() -> None:
    from mishe_tauftauf import discovery

    # Wait for the child to exec before reading /proc/<pid>/environ: at fork
    # the kernel still reports the parent's environment, so an immediate read
    # is a race, not evidence.
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.stdout.write('ready'); sys.stdout.flush(); time.sleep(5)"],
        stdout=subprocess.PIPE, env={**os.environ, "PYTHONPATH": "/srv/live/src"})
    try:
        assert proc.stdout is not None and proc.stdout.read(5) == b"ready"
        assert discovery._pane_pythonpath(str(proc.pid)) == ["/srv/live/src"]
    finally:
        proc.kill()
        proc.wait()


def test_pane_pythonpath_returns_empty_for_an_unreadable_pid() -> None:
    from mishe_tauftauf import discovery

    assert discovery._pane_pythonpath("") == []
    assert discovery._pane_pythonpath("not-a-pid") == []


def test_service_import_roots_parses_and_deduplicates_pythonpath() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    stdout = (
        _unit_block("a.service", "/srv/releases/x/src:/srv/releases/x")
        + "\n" + _unit_block("b.service", "/srv/checkout/src")
        + "\n" + _unit_block("c.service", "/srv/releases/x/src"))
    completed = subprocess.CompletedProcess(["systemctl"], 0, stdout, "")
    with patch("mishe_tauftauf.discovery.subprocess.run", return_value=completed):
        roots, failure = discovery._service_import_roots(home)
    assert failure is None
    assert roots == ["/srv/releases/x", "/srv/checkout"]


def test_service_import_roots_reports_an_unreadable_manifest() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    (home / "health" / "services.json").write_text("{not json", encoding="utf-8")
    roots, failure = discovery._service_import_roots(home)
    assert roots == []
    assert failure == "service manifest unreadable"


def test_service_import_roots_reports_a_failed_systemctl() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    completed = subprocess.CompletedProcess(["systemctl"], 1, "", "unit not found")
    with patch("mishe_tauftauf.discovery.subprocess.run", return_value=completed):
        roots, failure = discovery._service_import_roots(home)
    assert roots == []
    assert failure == "systemctl failed"


def test_service_import_roots_reports_an_empty_manifest() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    (home / "health" / "services.json").write_text("[]", encoding="utf-8")
    roots, failure = discovery._service_import_roots(home)
    assert roots == []
    assert failure == "service manifest empty"



def _release_tree(root: Path, sha_out: list[str]) -> None:
    """A clean git release worktree, reporting its real HEAD.

    The runtime pin agrees only with a clean tree whose HEAD equals its ``sha``,
    so a release the sensor compares is a real repository and the pin is written
    from the HEAD it actually has rather than a sha invented for the test.
    """
    (root / "src" / "mishe_tauftauf").mkdir(parents=True)
    (root / "src" / "mishe_tauftauf" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "mishe_tauftauf" / "discovery.py").write_text(
        "SENSOR_ID = 'sense.stand-in'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "senses", "GIT_AUTHOR_EMAIL": "senses@plant",
           "GIT_COMMITTER_NAME": "senses", "GIT_COMMITTER_EMAIL": "senses@plant",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "release"],
                   check=True, env=env)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, env=env, capture_output=True,
                          text=True).stdout.strip()
    sha_out.append(head)


def _pin(home: Path, root: Path, sha: str, session: str) -> Path:
    """A site home whose runtime-release pin names ``root`` at exactly ``sha``."""
    (home / "health").mkdir(parents=True, exist_ok=True)
    (home / "health" / "runtime-release.json").write_text(
        json.dumps({"version": 1, "source": str(root),
                    "sha": sha, "session": session}), encoding="utf-8")
    return home


def _linked_registry(home: Path, sites: list[dict[str, str]]) -> None:
    (home / "health").mkdir(parents=True, exist_ok=True)
    (home / "health" / "linked-sites.json").write_text(
        json.dumps({"sites": sites, "version": 1}), encoding="utf-8")


def test_drift_across_sites_names_a_unit_running_a_release_its_site_pin_rejects(
        tmp_path: Path) -> None:
    # The site's own manifest does not list the unit, so the manifest-scoped
    # sensor cannot see it; only the shared bus and the session prefix can.
    pinned_sha: list[str] = []
    stale_sha: list[str] = []
    pinned = tmp_path / "releases" / "pinned"
    stale = tmp_path / "releases" / "stale"
    _release_tree(pinned, pinned_sha)
    _release_tree(stale, stale_sha)
    other = tmp_path / "site-other"
    _pin(other, pinned, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    with patch.object(discovery, "_active_site_units",
                      return_value=(["mishe-other-silence.service"], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={"mishe-other-silence.service": str(stale)}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "drift"
    assert f"stale=mishe-other-silence.service@{stale.name}" in row["sample"]
    assert row["identity"]["stale"] == {"mishe-other-silence.service": str(stale)}


def test_drift_across_sites_declares_the_coordination_checkout_root(
        tmp_path: Path) -> None:
    # The release coordinator runs its site's checkout on purpose, so that root
    # is a declared exception rather than a stale release.
    pinned_sha: list[str] = []
    pinned = tmp_path / "releases" / "pinned"
    _release_tree(pinned, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, pinned, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-other-coordination.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={unit: str(tmp_path)}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "verified"
    assert row["identity"]["stale"] == {}


def test_drift_across_sites_reads_verified_when_every_unit_matches_its_own_pin(
        tmp_path: Path) -> None:
    # A second site's units are checked against the second site's pin, not the
    # scanning site's, so a different-but-agreeing site is not a drift.
    own_sha: list[str] = []
    other_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    other_pin = tmp_path / "releases" / "other"
    _release_tree(own_pin, own_sha)
    _release_tree(other_pin, other_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, other_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": other_sha[0]}])
    units = ["mishe-other-senses.service", "mishe-other-witness.service"]
    with patch.object(discovery, "_active_site_units", return_value=(units, None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={unit: str(other_pin) for unit in units}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "verified"
    assert row["identity"]["stale"] == {}
    assert row["identity"]["unattributed"] == []


def test_drift_across_sites_falls_back_to_seed_raised_for_own_session(
        tmp_path: Path) -> None:
    own_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    _release_tree(own_pin, own_sha)
    home = tmp_path / "plant"
    _pin(home, own_pin, own_sha[0], "mishe-self")
    _linked_registry(home, [{"home": str(tmp_path / "other"),
                             "session": "mishe-other", "sha": own_sha[0]}])
    (home / ".seed-raised").write_text("mishe-self wake-1\n", encoding="utf-8")
    units = ["mishe-self-discover.service", "mishe-cleaner-recovered.service"]
    with patch.object(discovery, "_active_site_units", return_value=(units, None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={units[0]: str(own_pin)}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.dict(os.environ, {}, clear=True):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert row["identity"]["sites"] == ["mishe-other", "mishe-self"]
    assert row["identity"]["unattributed"] == [units[1]]


def test_drift_across_sites_includes_the_scanning_sites_own_units(
        tmp_path: Path) -> None:
    # The registry lists the *other* sites; the scanning site's own units share
    # its bus, so they are checked against its own pin rather than reported as
    # unattributed.
    own_sha: list[str] = []
    other_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    _release_tree(own_pin, own_sha)
    _release_tree(tmp_path / "releases" / "other", other_sha)
    other = tmp_path / "site-other"
    _pin(other, tmp_path / "releases" / "other", other_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _pin(home, own_pin, own_sha[0], "mishe-self")
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": other_sha[0]}])
    with patch.object(discovery, "_active_site_units",
                      return_value=(["mishe-self-senses.service"], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={"mishe-self-senses.service": str(own_pin)}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "verified"
    assert row["identity"]["stale"] == {}
    assert row["identity"]["unattributed"] == []


def test_drift_across_sites_is_unknown_when_active_unit_is_unattributed(
        tmp_path: Path) -> None:
    pinned_sha: list[str] = []
    other_pin = tmp_path / "releases" / "other"
    _release_tree(other_pin, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-unlisted-senses.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert row["identity"]["stale"] == {}
    assert row["identity"]["unattributed"] == [unit]


def test_drift_across_sites_names_a_foreign_unit_declared_on_the_checkout(
        tmp_path: Path) -> None:
    # A foreign unit that carries no PYTHONPATH and has no live child at scan
    # time still declares the coupling in its own ExecStart text; the reading
    # names the consumer and the checkout instead of an anonymous coverage gap.
    pinned_sha: list[str] = []
    other_pin = tmp_path / "releases" / "other"
    _release_tree(other_pin, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-cleaner-recovered.service"
    checkout = tmp_path.resolve()
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_foreign_checkout_consumers",
                         return_value={unit: str(checkout)}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert f"foreign={unit}@{checkout.name}" in row["sample"]
    assert row["identity"]["foreign"] == {unit: str(checkout)}
    assert row["identity"]["unattributed"] == []


def test_drift_across_sites_names_a_foreign_unit_importing_the_checkout(
        tmp_path: Path) -> None:
    # The coupling is observed directly when the unit's own environment names a
    # path inside the checkout, without waiting for a short-lived child.
    pinned_sha: list[str] = []
    other_pin = tmp_path / "releases" / "other"
    _release_tree(other_pin, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-unlisted.service"
    checkout = tmp_path.resolve()
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={unit: str(checkout / "src")}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert f"foreign={unit}@{checkout.name}" in row["sample"]
    assert row["identity"]["foreign"] == {unit: str(checkout / "src")}
    assert row["identity"]["unattributed"] == []


def test_drift_across_sites_keeps_a_foreign_unit_off_the_checkout_unattributed(
        tmp_path: Path) -> None:
    # A foreign unit whose import root is another release, and whose ExecStart
    # names no plant path, is a coverage gap rather than a coupling.
    pinned_sha: list[str] = []
    other_pin = tmp_path / "releases" / "other"
    _release_tree(other_pin, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-unlisted.service"
    outside = tmp_path.parent / "other-release"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={unit: str(outside)}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert row["identity"]["unattributed"] == [unit]
    assert row["identity"]["foreign"] == {}


def test_drift_across_sites_still_latches_drift_with_a_foreign_consumer(
        tmp_path: Path) -> None:
    # A named foreign consumer is not drift, but it must not mask a stale release
    # on a mapped site: the drift state wins.
    pinned_sha: list[str] = []
    stale_sha: list[str] = []
    pinned = tmp_path / "releases" / "pinned"
    stale = tmp_path / "releases" / "stale"
    _release_tree(pinned, pinned_sha)
    _release_tree(stale, stale_sha)
    other = tmp_path / "site-other"
    _pin(other, pinned, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    mapped = "mishe-other-silence.service"
    foreign_unit = "mishe-cleaner-recovered.service"
    checkout = tmp_path.resolve()
    with patch.object(discovery, "_active_site_units",
                      return_value=([mapped, foreign_unit], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={mapped: str(stale)}), \
            patch.object(discovery, "_foreign_checkout_consumers",
                         return_value={foreign_unit: str(checkout)}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "drift"
    assert f"stale={mapped}@{stale.name}" in row["sample"]
    assert f"foreign={foreign_unit}@{checkout.name}" in row["sample"]


def test_names_checkout_matches_path_and_home_relative_forms() -> None:
    root = Path("/home/someone/mishe-tauftauf")
    assert discovery._names_checkout('CORE = Path.home() / "mishe-tauftauf"', root)
    assert discovery._names_checkout("PYTHONPATH=/home/someone/mishe-tauftauf/src", root)
    assert discovery._names_checkout("cd ~/mishe-tauftauf && exec", root)
    assert discovery._names_checkout("HOME=${HOME}/mishe-tauftauf", root)
    assert not discovery._names_checkout("a launcher for an unrelated service", root)
    assert not discovery._names_checkout("/home/someone/mishe-tauftauf-extra", root)


def test_imports_checkout_covers_the_root_and_its_subpaths(tmp_path: Path) -> None:
    checkout = tmp_path.resolve()
    assert discovery._imports_checkout(str(checkout), checkout)
    assert discovery._imports_checkout(str(checkout / "src"), checkout)
    assert not discovery._imports_checkout(str(tmp_path.parent / "elsewhere"), checkout)


def test_exec_start_files_reads_the_wrapper_argument(tmp_path: Path) -> None:
    script = tmp_path / "run-cleaner"
    script.write_text("#!/bin/sh\nexec true\n", encoding="utf-8")
    value = ("{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
             f"{script} follow ; ignore_errors=no ; start_time=[n/a] ; "
             "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }")
    files = discovery._exec_start_files(value)
    assert script in files
    assert tmp_path / "absent" not in files


def test_drift_across_sites_marks_mapped_unit_without_import_root_unknown(
        tmp_path: Path) -> None:
    pinned_sha: list[str] = []
    pinned = tmp_path / "releases" / "pinned"
    _release_tree(pinned, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, pinned, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-other-core-self-observer.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert f"uninspectable={unit}" in row["sample"]
    assert row["identity"]["uninspectable"] == [unit]
    assert row["identity"]["stale"] == {}


def test_drift_across_sites_reads_the_scanning_site_with_no_registered_sites(
        tmp_path: Path) -> None:
    # The registry lists the *other* sites, so an empty one says nothing about
    # the scanning site's own units: they share its bus and still need its pin.
    own_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    _release_tree(own_pin, own_sha)
    home = tmp_path / "plant"
    _pin(home, own_pin, own_sha[0], "mishe-self")
    _linked_registry(home, [])
    unit = "mishe-self-senses.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={unit: str(own_pin)}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "verified"
    assert row["identity"]["sites"] == ["mishe-self"]
    assert row["identity"]["stale"] == {}
    assert "sites=1" in row["sample"]


def test_drift_across_sites_names_a_stale_own_unit_with_no_registered_sites(
        tmp_path: Path) -> None:
    # An empty registry must not hide the scanning site's own stale release; the
    # unit is named even though no other site is registered to compare against.
    pinned_sha: list[str] = []
    stale_sha: list[str] = []
    pinned = tmp_path / "releases" / "pinned"
    stale = tmp_path / "releases" / "stale"
    _release_tree(pinned, pinned_sha)
    _release_tree(stale, stale_sha)
    home = tmp_path / "plant"
    _pin(home, pinned, pinned_sha[0], "mishe-self")
    _linked_registry(home, [])
    unit = "mishe-self-core-self-observer.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={unit: str(stale)}), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "drift"
    assert f"stale={unit}@{stale.name}" in row["sample"]
    assert row["identity"]["stale"] == {unit: str(stale)}


def test_drift_across_sites_is_unknown_when_the_registry_has_no_site_list(
        tmp_path: Path) -> None:
    # A registry whose site list is unreadable names no session to map, so the
    # reading stays unknown rather than reporting the scanning site as clean.
    home = tmp_path / "plant"
    (home / "health").mkdir(parents=True, exist_ok=True)
    (home / "health" / "linked-sites.json").write_text(
        json.dumps({"sites": {}, "version": 1}), encoding="utf-8")
    with patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert "no site list" in row["sample"]


def test_active_site_units_reads_the_list_units_table_not_show_properties(
        tmp_path: Path) -> None:
    # ``list-units`` ignores ``-p`` and prints an indented column table, so the
    # state is positional: a mishe service is active only when both columns say so.
    table = "\n".join([
        "  mishe-a-senses.service    loaded    active   running Mishe senses",
        "  mishe-b-stopped.service   loaded    inactive dead    Mishe stopped",
        "  mische-c-degraded.service loaded    active   failed  Mishe degraded",
        "  other.service             loaded    active   running Not a site unit",
    ])
    completed = subprocess.CompletedProcess(["systemctl"], 0, table, "")
    with patch.object(discovery.subprocess, "run", return_value=completed):
        units, failure = discovery._active_site_units()
    assert failure is None
    assert units == ["mishe-a-senses.service"]


def test_active_site_units_reports_a_failed_systemctl() -> None:
    completed = subprocess.CompletedProcess(["systemctl"], 1, "", "unit not found")
    with patch.object(discovery.subprocess, "run", return_value=completed):
        units, failure = discovery._active_site_units()
    assert units == []
    assert failure == "systemctl failed"


def test_unit_import_roots_deduplicates_a_shared_path_component() -> None:
    # ``show`` prints one block per unit; a unit with several PYTHONPATH entries
    # keeps only the first that names a source root, and one with none is absent.
    stdout = "\n".join([
        "Id=mishe-a-senses.service",
        "Environment=PYTHONPATH=/releases/aaa/src PATH=/usr/bin",
        "",
        "Id=mishe-a-witness.service",
        "Environment=PATH=/usr/bin",
    ])
    completed = subprocess.CompletedProcess(["systemctl"], 0, stdout, "")
    with patch.object(discovery.subprocess, "run", return_value=completed):
        roots = discovery._unit_import_roots(["mishe-a-senses.service",
                                              "mishe-a-witness.service"])
    assert roots == {"mishe-a-senses.service": "/releases/aaa"}
def test_unit_import_roots_reads_wrapper_pythonpath_from_live_process() -> None:
    stdout = "\n".join([
        "Id=mishe-a-senses.service",
        "Environment=PATH=/usr/bin",
        "MainPID=1234",
    ])
    initial = subprocess.CompletedProcess(["systemctl"], 0, stdout, "")
    recheck = subprocess.CompletedProcess(
        ["systemctl"], 0, "Id=mishe-a-senses.service\nMainPID=1234", "")
    with patch.object(discovery.subprocess, "run",
                      side_effect=[initial, recheck]), \
            patch.object(Path, "read_bytes",
                         return_value=b"PATH=/usr/bin\0PYTHONPATH=/releases/wrapper/src\0"):
        roots = discovery._unit_import_roots(["mishe-a-senses.service"])
    assert roots == {"mishe-a-senses.service": "/releases/wrapper"}

def test_unit_import_roots_reads_wrapper_pythonpath_from_child_process() -> None:
    stdout = "\n".join([
        "Id=mishe-a-senses.service",
        "Environment=PATH=/usr/bin",
        "MainPID=1234",
        ])
    initial = subprocess.CompletedProcess(["systemctl"], 0, stdout, "")
    recheck = subprocess.CompletedProcess(
        ["systemctl"], 0, "Id=mishe-a-senses.service\nMainPID=1234", "")
    ps_result = subprocess.CompletedProcess(["ps"], 0, "5678", "")

    def mock_read_bytes(self):
        if "1234" in str(self):
            return b"PATH=/usr/bin\0"
        if "5678" in str(self):
            return b"PATH=/usr/bin\0PYTHONPATH=/releases/wrapper/src\0"
        return b""

    with patch.object(discovery.subprocess, "run",
                      side_effect=[initial, ps_result, recheck]), \
            patch.object(Path, "read_bytes", mock_read_bytes):
        roots = discovery._unit_import_roots(["mishe-a-senses.service"])
    assert roots == {"mishe-a-senses.service": "/releases/wrapper"}


def test_unit_import_roots_child_without_pythonpath_remains_uninspectable() -> None:
    stdout = "\n".join([
        "Id=mishe-a-senses.service",
        "Environment=PATH=/usr/bin",
        "MainPID=1234",
        ])
    initial = subprocess.CompletedProcess(["systemctl"], 0, stdout, "")
    recheck = subprocess.CompletedProcess(
        ["systemctl"], 0, "Id=mishe-a-senses.service\nMainPID=1234", "")
    ps_result = subprocess.CompletedProcess(["ps"], 0, "5678", "")

    with patch.object(discovery.subprocess, "run",
                      side_effect=[initial, ps_result, recheck]), \
            patch.object(Path, "read_bytes", return_value=b"PATH=/usr/bin\0"):
        roots = discovery._unit_import_roots(["mishe-a-senses.service"])
    assert roots == {}


def test_unit_import_roots_skips_children_when_main_has_pythonpath() -> None:
    stdout = "\n".join([
        "Id=mishe-a-senses.service",
        "Environment=PATH=/usr/bin",
        "MainPID=1234",
        ])
    initial = subprocess.CompletedProcess(["systemctl"], 0, stdout, "")
    recheck = subprocess.CompletedProcess(
        ["systemctl"], 0, "Id=mishe-a-senses.service\nMainPID=1234", "")

    def mock_run(cmd, **kwargs):
        if cmd[0] == "ps":
            raise AssertionError("ps should not be called when main has PYTHONPATH")
        if "Id,Environment,MainPID" in cmd:
            return initial
        return recheck

    with patch.object(discovery.subprocess, "run", side_effect=mock_run), \
            patch.object(Path, "read_bytes",
                         return_value=b"PATH=/usr/bin\0PYTHONPATH=/releases/main/src\0"):
        roots = discovery._unit_import_roots(["mishe-a-senses.service"])
    assert roots == {"mishe-a-senses.service": "/releases/main"}


def test_unit_import_roots_checks_multiple_children() -> None:
    stdout = "\n".join([
        "Id=mishe-a-senses.service",
        "Environment=PATH=/usr/bin",
        "MainPID=1234",
        ])
    initial = subprocess.CompletedProcess(["systemctl"], 0, stdout, "")
    recheck = subprocess.CompletedProcess(
        ["systemctl"], 0, "Id=mishe-a-senses.service\nMainPID=1234", "")
    ps_result = subprocess.CompletedProcess(["ps"], 0, "5678\n9012", "")

    def mock_read_bytes(self):
        if "1234" in str(self):
            return b"PATH=/usr/bin\0"
        if "5678" in str(self):
            return b"PATH=/usr/bin\0"
        if "9012" in str(self):
            return b"PATH=/usr/bin\0PYTHONPATH=/releases/wrapper/src\0"
        return b""

    with patch.object(discovery.subprocess, "run",
                      side_effect=[initial, ps_result, recheck]), \
            patch.object(Path, "read_bytes", mock_read_bytes):
        roots = discovery._unit_import_roots(["mishe-a-senses.service"])
    assert roots == {"mishe-a-senses.service": "/releases/wrapper"}




def tmp_site_with_services() -> Path:
    import tempfile
    import os as _os

    home = Path(tempfile.mkdtemp(prefix="senses-runtime-"))
    (home / "health").mkdir()
    (home / "health" / "services.json").write_text(
        json.dumps(["a.service", "b.service"]), encoding="utf-8")
    # A site home sits beside its checkout, and the pin names the checkout root.
    (home / "checkout").mkdir()
    atexit_register(home)
    return home


def atexit_register(home: Path) -> None:
    import atexit
    import shutil

    def remove(path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)

    atexit.register(remove, home)
