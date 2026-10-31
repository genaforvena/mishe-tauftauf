from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from mishe_tauftauf.discovery import _cpu_busy, latest, renew_scan, scan, scan_age
from mishe_tauftauf import discovery
from mishe_tauftauf import proc_table
from mishe_tauftauf.feed import Feed
from mishe_tauftauf import seed_culture_views
from mishe_tauftauf.seed_culture_views import discover


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

def test_scan_recovers_from_malformed_previous_receipt(tmp_path: Path) -> None:
    root = tmp_path / "discovery"
    root.mkdir()
    damaged = {"observations": ["invalid previous row"]}
    (root / "latest.json").write_text(json.dumps(damaged))
    value = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": [
        {"id": "sense.value", "kind": "read", "state": "future-state", "sample": [1, 2]},
    ]}
    with patch("mishe_tauftauf.discovery.sample", return_value=value):
        artifact = scan(tmp_path)
    assert discovery.scan_attempt(tmp_path)["status"] == "succeeded"
    assert latest(tmp_path)["observations"] == value["observations"]
    entry = Feed(tmp_path).entries()[-1]
    assert "Initial baseline." in entry.body
    assert "sense.value — [1, 2]" in entry.body
    assert artifact.exists()


def test_scan_includes_producer_provenance(tmp_path: Path) -> None:
    home = tmp_path / "site"
    artifact = scan(home)
    snapshot = json.loads(artifact.read_text())
    assert "producer" in snapshot
    producer = snapshot["producer"]
    # The producer names the package root and the git commit that produced it.
    expected_root = str(Path(discovery.__file__).resolve().parent)
    assert producer.startswith(expected_root + "@")
    sha = producer.rsplit("@", 1)[-1]
    assert sha == "unknown" or re.fullmatch(r"[0-9a-f]{40}", sha)

    # latest.json carries the same producer.
    assert latest(home)["producer"] == producer


def test_producer_label_names_the_root_against_the_pin(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "runtime-release.json").write_text(
        json.dumps({"version": 1, "source": str(tmp_path / "releases" / "abc"),
                    "sha": "a" * 40, "session": "s"}), encoding="utf-8")
    monkeypatch.setattr(discovery, "source_for", lambda _home, _default: tmp_path / "releases" / "abc")

    pinned = tmp_path / "releases" / "abc" / "src" / "mishe_tauftauf"
    assert discovery.producer_label(home, f"{pinned}@{'a' * 40}") == "pin@aaaaaaaaaaaa"
    checkout = tmp_path / "src" / "mishe_tauftauf"
    assert discovery.producer_label(home, f"{checkout}@{'b' * 40}") == "checkout@bbbbbbbbbbbb"
    assert discovery.producer_label(home, f"/elsewhere/src/mishe_tauftauf@{'c' * 40}") == "other@cccccccccccc"
    # No recorded producer and an unreadable pin both stay unevidenced, not "other".
    assert discovery.producer_label(home, None) == "unknown"
    assert discovery.producer_label(home, "no-separator") == "unknown"
    monkeypatch.setattr(discovery, "source_for",
                        lambda _home, _default: (_ for _ in ()).throw(ValueError("bad pin")))
    assert discovery.producer_label(home, f"{pinned}@{'a' * 40}") == "unknown@aaaaaaaaaaaa"


def test_scan_line_and_tape_name_the_producer(tmp_path: Path) -> None:
    home = tmp_path / "site"
    artifact = scan(home)
    sha = json.loads(artifact.read_text())["producer"].rsplit("@", 1)[1][:12]
    # A tmp home declares no pin, so the root cannot be named against one.
    view = discover(home)
    line = next(item for item in view.splitlines() if item.startswith("SCAN: "))
    assert line.endswith(f" producer=unknown@{sha}")
    body = Feed(home).entries()[-1].body
    assert f"Producer: unknown@{sha}." in body.splitlines()


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


_PLANT_SESSION = "mishe-self-development-current"


def _unit_failure_fixture(monkeypatch, output, boots=None, restart_context=None,
                          session=_PLANT_SESSION):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return _JOURNAL_NOW

    monkeypatch.setattr(discovery, "datetime", Clock)
    monkeypatch.setattr(discovery, "_journal_boot",
                        lambda: next(boots) if boots is not None else _JOURNAL_BOOT)
    monkeypatch.setattr(discovery, "_journal_command", lambda cmd: output)
    monkeypatch.setattr(discovery, "_unit_restart_context",
                        lambda units: {u: restart_context[u] for u in units
                                       if restart_context and u in restart_context})
    if session is None:
        monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    else:
        monkeypatch.setenv("MISHE_SEED_SESSION", session)
    return discovery._journal_unit_failure_window(Path("/nonexistent"))


def test_unit_failure_counts_plant_units_and_discloses_foreign(monkeypatch):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(
            MESSAGE=f"{_PLANT_SESSION}-discover.service: Failed with result 'oom-kill'."),
        _unit_failure_record(__CURSOR="cursor2",
                             MESSAGE="tmux-spawn-9f2c1.scope: Failed with result 'oom-kill'."),
        _unit_failure_record(__CURSOR="cursor3",
                             MESSAGE="mishe-cleaner-recovered.service: Failed with result 'oom-kill'."),
        _unit_failure_record(__CURSOR="cursor4",
                             MESSAGE="mesh-pull-t1.service: Failed with result 'exit-code'.")))
    assert result["state"] == "verified"
    # A foreign service that shares the ``mishe-`` prefix, and another node's
    # test transient, are disclosed, not counted as this plant's failures.
    assert result["count"] == 2
    assert result["classes"] == {"oom-kill": 2}
    assert result["units"] == {f"{_PLANT_SESSION}-discover.service": 1,
                               "tmux-spawn-9f2c1.scope": 1}
    assert result["foreign_units"] == {"mishe-cleaner-recovered.service": 1,
                                       "mesh-pull-t1.service": 1}
    assert result["sample"] == ("last-10min unit-failure-count=2 oom-kill=2 "
                                "foreign=mesh-pull-t1.service:1,mishe-cleaner-recovered.service:1")


def test_unit_failure_counts_a_plant_unit_name_containing_a_colon(monkeypatch):
    # systemd.unit(5) allows ':' in a unit name prefix.
    unit = f"{_PLANT_SESSION}-foo:bar.service"
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(MESSAGE=f"{unit}: Failed with result 'exit-code'.")))
    assert result["state"] == "verified"
    assert result["units"] == {unit: 1}
    assert result["classes"] == {"exit-code": 1}


def test_unit_failure_unknown_when_the_plant_session_is_unavailable(monkeypatch):
    # Without a session there is no way to tell this plant's units from another
    # site's or a foreign service's, so the reading stays unknown.
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(
            MESSAGE=f"{_PLANT_SESSION}-discover.service: Failed with result 'exit-code'.")),
        session=None)
    assert result["state"] == "unknown"
    assert result["reason"] == "plant session unavailable"
    assert "count" not in result


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
    unit = f"{_PLANT_SESSION}-discover.service"
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(MESSAGE=f"{unit}: Failed with result 'exit-code'.")),
        restart_context={unit: {"restarts": 1, "active": "running"}})
    assert result["state"] == "verified"
    assert result["unit_context"] == {unit: {"restarts": 1, "active": "running"}}
    assert f"{unit}:restarts=1,active=running" in result["sample"]


def test_unit_failure_omits_restart_context_when_unavailable(monkeypatch):
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(
            MESSAGE=f"{_PLANT_SESSION}-discover.service: Failed with result 'exit-code'.")))
    assert result["state"] == "verified"
    assert result["unit_context"] == {}
    assert "restarts=" not in result["sample"]


def test_unit_failure_never_attaches_restart_context_to_a_foreign_unit(monkeypatch):
    # Restart context is looked up for plant-owned units only: a foreign
    # failure is disclosed, but must not gain a restarts= clause even when
    # systemctl would report one for it.
    unit = "mishe-cleaner-recovered.service"
    result = _unit_failure_fixture(monkeypatch, _journal_bytes(
        _unit_failure_record(MESSAGE=f"{unit}: Failed with result 'exit-code'.")),
        restart_context={unit: {"restarts": 114, "active": "running"}})
    assert result["state"] == "verified"
    assert result["unit_context"] == {}
    assert "restarts=" not in result["sample"]
    assert result["foreign_units"] == {unit: 1}


def test_unit_restart_context_queries_systemctl(monkeypatch):
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class Result:
            returncode = 0
            stdout = "Id=mesh-cleaner.service\nNRestarts=1\nActiveState=running\nLoadState=loaded\n\n"
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
            returncode = 0
            if "--user" in cmd:
                stdout = ("Id=cron.service\nNRestarts=0\nActiveState=inactive\n"
                          "LoadState=not-found\n\n")
            else:
                stdout = ("Id=cron.service\nNRestarts=0\nActiveState=active\n"
                          "LoadState=loaded\n\n")
        return Result()
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    result = discovery._unit_restart_context(["cron.service"])
    assert result == {"cron.service": {"restarts": 0, "active": "active"}}
    assert len(calls) == 2
    assert "--user" in calls[0]
    assert "--user" not in calls[1]


def test_unit_restart_context_returns_empty_on_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5)
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    result = discovery._unit_restart_context(["mesh-cleaner.service"])
    assert result == {}


def test_unit_restart_context_skips_nonzero_returncode(monkeypatch):
    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 1
            stdout = ""
        return Result()
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    result = discovery._unit_restart_context(["mesh-cleaner.service"])
    assert result == {}


def test_unit_restart_context_handles_invalid_nrestarts(monkeypatch):
    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 0
            stdout = ("Id=mesh-cleaner.service\nNRestarts=abc\nActiveState=active\n"
                      "LoadState=loaded\n\n")
        return Result()
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    result = discovery._unit_restart_context(["mesh-cleaner.service"])
    assert result == {"mesh-cleaner.service": {"restarts": 0, "active": "active"}}


def test_unit_restart_context_skips_block_without_id(monkeypatch):
    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 0
            stdout = "NRestarts=0\nActiveState=active\nLoadState=loaded\n\n"
        return Result()
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    result = discovery._unit_restart_context(["mesh-cleaner.service"])
    assert result == {}


def test_unit_restart_context_empty_units_returns_empty():
    result = discovery._unit_restart_context([])
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
                      "sources": {"automatic-retry": 3}, "cause": "none",
                      "last_error_ts": None, "error_count": 0, "foreign": 0}


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
                      "sources": {"automatic-retry": 2}, "cause": "none",
                      "last_error_ts": None, "error_count": 0, "foreign": 0}


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
                      "sources": {"automatic-retry": 3}, "cause": "none",
                      "last_error_ts": None, "error_count": 0, "foreign": 0}


def test_wedge_chain_resets_on_success(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:00:00+00:00", 1),
        _success("2026-10-06T13:30:00+00:00"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 0, "span_minutes": 0.0, "sources": {}, "cause": "none",
                      "last_error_ts": None, "error_count": 0, "foreign": 0}


def test_wedge_no_log_returns_none(monkeypatch):
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: None)
    assert discovery._omp_continue_chain(12345, _WEDGE_NOW) is None


def test_wedge_empty_log_is_chain_zero(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result == {"chain": 0, "span_minutes": 0.0, "sources": {}, "cause": "none",
                      "last_error_ts": None, "error_count": 0, "foreign": 0}


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
        stdout = "mishe-tauftauf|research-methods|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert len(result["suspects"]) == 1
    assert result["suspects"][0]["window"] == "research-methods"
    assert result["suspects"][0]["pid"] == 12345
    assert result["suspects"][0]["session"] == "mishe-tauftauf"
    assert result["suspects"][0]["chain"] == 3
    assert result["suspects"][0]["span_minutes"] == 30.0
    assert result["suspects"][0]["sources"] == {"automatic-retry": 3}
    assert ("research-methods(pid=12345,session=mishe-tauftauf,chain=3,span=30.0min,"
            "src=automatic-retry:3,cause=none)") in result["sample"]
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
        stdout = "mishe-tauftauf|research-methods|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert result["suspects"] == []
    assert result["sample"] == "suspects=0 panes=2 with_log=1 foreign=0"
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
        stdout = "mishe-tauftauf|research-methods|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "unknown"
    assert result["sample"] == "suspects=0 panes=2 with_log=0 foreign=0"
    assert result["with_log"] == 0


def test_watch_wait_sleep_pattern_with_still_content_is_watch_wait(monkeypatch):
    monkeypatch.setattr(discovery, "_ci_run_state", lambda run_id: None)
    result = discovery._watch_wait_verdict("$ sleep 150 ⠋ · 1s", "$ sleep 150 ⠙ · 4s", "now")
    assert result["verdict"] == "watch-wait"
    assert result["pattern"] == "sleep"
    assert result["stable"] is True


def test_watch_wait_ci_poll_confirmed_in_progress_is_watch_wait(monkeypatch):
    monkeypatch.setattr(discovery, "_ci_run_state",
                        lambda run_id: {"status": "in_progress", "conclusion": "",
                                        "updated_at": "2026-10-12T11:31:03Z"})
    result = discovery._watch_wait_verdict("$ gh run view 38242368632 ⠋ · 2m8s",
                                           "$ gh run view 38242368632 ⠙ · 2m23s", "now")
    assert result["verdict"] == "watch-wait"
    assert result["run_id"] == "38242368632"


def test_watch_wait_ci_poll_unreadable_state_is_unknown(monkeypatch):
    monkeypatch.setattr(discovery, "_ci_run_state", lambda run_id: None)
    result = discovery._watch_wait_verdict("$ gh run view 38242368632 ⠋ · 2m8s",
                                           "$ gh run view 38242368632 ⠙ · 2m23s", "now")
    assert result["verdict"] == "unknown"
    assert result["run_id"] == "38242368632"


def test_watch_wait_ci_poll_without_run_id_is_unknown(monkeypatch):
    monkeypatch.setattr(discovery, "_ci_run_state", lambda run_id: None)
    result = discovery._watch_wait_verdict("Waiting for CI poll result ⠋ · 1s",
                                           "Waiting for CI poll result ⠙ · 4s", "now")
    assert result["verdict"] == "unknown"
    assert "names no run id" in result["detail"]


def test_watch_wait_completed_run_is_suspect_not_excused(monkeypatch):
    monkeypatch.setattr(discovery, "_ci_run_state",
                        lambda run_id: {"status": "completed", "conclusion": "success",
                                        "updated_at": "2026-10-12T11:31:03Z"})
    result = discovery._watch_wait_verdict("$ gh run view 38242368632 ⠋ · 2m8s",
                                           "$ gh run view 38242368632 ⠙ · 2m23s", "now")
    assert result["verdict"] == "suspect"


def test_watch_wait_growing_content_is_active(monkeypatch):
    monkeypatch.setattr(discovery, "_ci_run_state", lambda run_id: None)
    result = discovery._watch_wait_verdict("line one", "line one\nline two", "now")
    assert result["verdict"] == "active"


def test_watch_wait_empty_frames_are_pane_dead():
    assert discovery._watch_wait_verdict("", "  \n ", "now")["verdict"] == "pane-dead"


def test_watch_wait_tmux_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(discovery.subprocess, "run",
                        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": ""})())
    result = discovery._mind_watch_wait(Path("/tmp"))
    assert result["id"] == "sense.mind.watch-wait"
    assert result["state"] == "unknown"
    assert result["panes"] == []


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
                                  "todo-reminder": 1},
                      "cause": "none",
                      "last_error_ts": None, "error_count": 0, "foreign": 0}


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
                      "sources": {"automatic-retry": 2}, "cause": "none",
                      "last_error_ts": None, "error_count": 0, "foreign": 0}


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
                      "sources": {"unknown": 3}, "cause": "none",
                      "last_error_ts": None, "error_count": 0, "foreign": 0}


def _provider_error(timestamp: str, status: int | None = None,
                    message: str = "Upstream request failed.",
                    model: str = "longcat-2.5-preview-free") -> str:
    fields = {"level": "warn", "provider": "opencode-go",
              "model": model, "errorMessage": message}
    if status is not None:
        fields["errorStatus"] = status
    return _omp_log_line(timestamp, "agent turn ended with provider error", **fields)


def test_wedge_chain_cause_names_the_dominant_provider_error_class(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 500),
        _retry("2026-10-06T13:30:00+00:00", 1),
        _provider_error("2026-10-06T13:35:00+00:00", 500),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _provider_error("2026-10-06T13:45:00+00:00", None,
                        "The socket connection was closed unexpectedly."),
        _retry("2026-10-06T13:50:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result["cause"] == "provider-error:500"


def test_wedge_chain_cause_reads_the_status_from_the_message(monkeypatch, tmp_path):
    # errorStatus is absent: the [500] in the message names the class.
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", None,
                        "Streaming response failed: [500] DEADLINE_EXCEEDED"),
        _retry("2026-10-06T13:30:00+00:00", 1),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _retry("2026-10-06T13:50:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result["cause"] == "provider-error:500"


def test_wedge_chain_cause_reads_unknown_when_no_status_is_named(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", None,
                        "The socket connection was closed unexpectedly."),
        _retry("2026-10-06T13:30:00+00:00", 1),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _retry("2026-10-06T13:50:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result["cause"] == "provider-error:unknown"


def test_wedge_sense_sample_carries_the_cause(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 500),
        _retry("2026-10-06T13:30:00+00:00", 1),
        _provider_error("2026-10-06T13:35:00+00:00", 500),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _provider_error("2026-10-06T13:45:00+00:00", 500),
        _retry("2026-10-06T13:50:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|health|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["suspects"][0]["cause"] == "provider-error:500"
    assert ("health(pid=12345,session=mishe-tauftauf,chain=3,span=30.0min,"
            "src=automatic-retry:3,cause=provider-error:500)") in result["sample"]

def test_wedge_r2_provider_error_stale_is_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:30:00+00:00", 403),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result["chain"] == 0
    assert result["cause"] == "provider-error:403"
    assert result["last_error_ts"] == "2026-10-06T13:30:00+00:00"
    assert result["error_count"] == 1


def test_wedge_r2_provider_error_fresh_is_not_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:58:00+00:00", 403),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result["chain"] == 0
    assert result["cause"] == "provider-error:403"
    assert result["last_error_ts"] == "2026-10-06T13:58:00+00:00"
    assert result["error_count"] == 1


def test_wedge_r2_error_reset_on_success(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:30:00+00:00", 403),
        _success("2026-10-06T13:35:00+00:00"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result["chain"] == 0
    assert result["cause"] == "none"
    assert result["last_error_ts"] is None
    assert result["error_count"] == 0


def test_wedge_r2_with_chain_is_r1_not_r2(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 500),
        _retry("2026-10-06T13:30:00+00:00", 1),
        _provider_error("2026-10-06T13:35:00+00:00", 500),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _provider_error("2026-10-06T13:45:00+00:00", 500),
        _retry("2026-10-06T13:50:00+00:00", 3),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(12345, _WEDGE_NOW)
    assert result["chain"] == 3
    assert result["cause"] == "provider-error:500"
    assert result["error_count"] == 3


def test_wedge_r2_sense_flags_stuck_pane(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:30:00+00:00", 403),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|docs|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert len(result["suspects"]) == 1
    assert result["suspects"][0]["window"] == "docs"
    assert result["suspects"][0]["pid"] == 12345
    assert result["suspects"][0]["rule"] == "provider-error"
    assert result["suspects"][0]["chain"] == 0
    assert result["suspects"][0]["cause"] == "provider-error:403"
    assert result["suspects"][0]["error_age_minutes"] == 30.0
    assert ("docs(pid=12345,session=mishe-tauftauf,rule=provider-error,chain=0,"
            "error_age=30.0min,cause=provider-error:403)") in result["sample"]
    assert "panes=2 with_log=1" in result["sample"]

def test_wedge_r3_chain2_stale_error_is_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 429),
        _retry("2026-10-06T13:30:00+00:00", 1),
        _provider_error("2026-10-06T13:35:00+00:00", 429),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _provider_error("2026-10-06T13:45:00+00:00", 429),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|genome|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert len(result["suspects"]) == 1
    assert result["suspects"][0]["window"] == "genome"
    assert result["suspects"][0]["rule"] == "chain-stall"
    assert result["suspects"][0]["chain"] == 2
    assert result["suspects"][0]["span_minutes"] == 30.0
    assert result["suspects"][0]["cause"] == "provider-error:429"
    assert result["suspects"][0]["error_age_minutes"] == 15.0
    assert ("genome(pid=12345,session=mishe-tauftauf,rule=chain-stall,chain=2,"
            "span=30.0min,error_age=15.0min,cause=provider-error:429)") in result["sample"]


def test_wedge_r3_chain1_stale_error_is_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 429),
        _retry("2026-10-06T13:30:00+00:00", 1),
        _provider_error("2026-10-06T13:45:00+00:00", 429),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|genome|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert len(result["suspects"]) == 1
    assert result["suspects"][0]["rule"] == "chain-stall"
    assert result["suspects"][0]["chain"] == 1


def test_wedge_r3_chain2_fresh_error_is_not_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:50:00+00:00", 429),
        _retry("2026-10-06T13:55:00+00:00", 1),
        _provider_error("2026-10-06T13:58:00+00:00", 429),
        _retry("2026-10-06T13:59:00+00:00", 2),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|genome|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert result["suspects"] == []


def test_wedge_r3_chain2_no_error_is_not_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _retry("2026-10-06T13:30:00+00:00", 1),
        _retry("2026-10-06T13:40:00+00:00", 2),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|genome|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert result["suspects"] == []


def test_wedge_r3_foreign_error_is_not_suspect(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 429, model="other-model"),
        _retry("2026-10-06T13:30:00+00:00", 1),
        _provider_error("2026-10-06T13:35:00+00:00", 429, model="other-model"),
        _retry("2026-10-06T13:40:00+00:00", 2),
        _provider_error("2026-10-06T13:45:00+00:00", 429, model="other-model"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    monkeypatch.setattr(discovery, "_pane_mind_model", lambda pid: "longcat-2.5-preview-free")
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|genome|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert result["suspects"] == []
    assert result["foreign"] == 3


def test_wedge_sense_reports_pane_session(monkeypatch, tmp_path):
    # Live false positive 2026-10-10: two sites render on this host and share
    # every window name, so six "suspects" on this plant's dashboard were all
    # the linked site's panes. The sample must name the session.
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:30:00+00:00", 403),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "android-body|senses|12345\nmishe-tauftauf|senses|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert len(result["suspects"]) == 1
    assert result["suspects"][0]["window"] == "senses"
    assert result["suspects"][0]["pid"] == 12345
    assert result["suspects"][0]["session"] == "android-body"
    assert ("senses(pid=12345,session=android-body,rule=provider-error,chain=0,"
            "error_age=30.0min,cause=provider-error:403)") in result["sample"]


def test_wedge_r2_sense_fresh_error_not_flagged(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:58:00+00:00", 403),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|docs|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert result["suspects"] == []
    assert result["sample"] == "suspects=0 panes=2 with_log=1 foreign=0"


def test_wedge_r2_sense_success_after_error_not_flagged(monkeypatch, tmp_path):
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:30:00+00:00", 403),
        _success("2026-10-06T13:35:00+00:00"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|docs|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert result["suspects"] == []
    assert result["sample"] == "suspects=0 panes=2 with_log=1 foreign=0"


def test_wedge_chain_excludes_a_provider_error_from_another_model(monkeypatch, tmp_path):
    # A pane's omp session log also carries its title generator's and subagents'
    # turns; a failure on their model is not the mind's wedge.
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 429,
                        model="muse-spark-1.3-contributor"),
        _provider_error("2026-10-06T13:35:00+00:00", 429,
                        model="opencode-go/muse-spark-1.3-contributor"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(
        12345, _WEDGE_NOW, "opencode-go/longcat-2.5-preview-free")
    assert result == {"chain": 0, "span_minutes": 0.0, "sources": {},
                      "cause": "none", "last_error_ts": None, "error_count": 0, "foreign": 2}


def test_wedge_chain_keeps_the_panes_own_model_error(monkeypatch, tmp_path):
    # A launcher writes the provider-qualified name and the log records the bare
    # one; they are the same model, so the mind's own failure must still count.
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [_provider_error("2026-10-06T13:25:00+00:00", 403)])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    result = discovery._omp_continue_chain(
        12345, _WEDGE_NOW, "opencode-go/longcat-2.5-preview-free")
    assert result["cause"] == "provider-error:403"
    assert result["error_count"] == 1


def test_wedge_chain_keeps_a_provider_error_it_cannot_attribute(monkeypatch, tmp_path):
    # An unreadable command line (no mind model) and a record with no model field
    # both stay counted: the reader must not hide a failure it cannot attribute.
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 500),
        _omp_log_line("2026-10-06T13:35:00+00:00",
                      "agent turn ended with provider error", level="warn"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path", lambda pid: log)
    _wedge_clock(monkeypatch)
    assert discovery._omp_continue_chain(12345, _WEDGE_NOW)["error_count"] == 2
    attributed = discovery._omp_continue_chain(
        12345, _WEDGE_NOW, "opencode-go/longcat-2.5-preview-free")
    assert attributed["cause"] == "provider-error:500"
    assert attributed["error_count"] == 2


def test_pane_mind_model_reads_none_for_a_malformed_model_flag(monkeypatch):
    # A launcher with no usable --model value must not be authoritative: the
    # reader reads "cannot attribute" and counts, rather than hides, a failure.
    monkeypatch.setattr(discovery.omp_pane, "command_line",
                        lambda pid: ["omp", "--model="])
    assert discovery._pane_mind_model(12345) is None


def test_wedge_r2_foreign_model_error_is_not_a_suspect(monkeypatch, tmp_path):
    # The live false positive: research-methods' pane log held only its title
    # generator's 429s, so a healthy pane read as a provider-error wedge.
    log = tmp_path / "omp.2026-10-06.12345.log"
    _write_omp_log(log, [
        _provider_error("2026-10-06T13:25:00+00:00", 429,
                        model="muse-spark-1.3-contributor"),
    ])
    monkeypatch.setattr(discovery, "_omp_log_path",
                        lambda pid: log if pid == 12345 else None)
    monkeypatch.setattr(discovery, "_pane_mind_model",
                        lambda pid: "opencode-go/longcat-2.5-preview-free")
    _wedge_clock(monkeypatch)

    class FakeResult:
        returncode = 0
        stdout = "mishe-tauftauf|docs|12345\nandroid-body|body-research|67890"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: FakeResult())
    result = discovery._mind_wedge_suspects()
    assert result["state"] == "verified"
    assert result["suspects"] == []
    assert result["sample"] == "suspects=0 panes=2 with_log=1 foreign=1"
    assert result["foreign"] == 1


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

def test_dv_binding_clean_store(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "applied", "delivery_verified": True,
                                  "verification": {"command": "x", "exit": 0}})
    _write_patch(home, "b.json", {"phase": "reverted", "delivery_verified": False})
    result = discovery._ledger_dv_binding(home)
    assert result["state"] == "verified"
    assert result["sample"] == "unbound=0 records=2 bool_dv=2"
    assert result["unbound"] == 0


def test_dv_binding_flags_unbound_true(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "applied", "delivery_verified": True,
                                  "verification": {"command": "x", "exit": 0}})
    _write_patch(home, "b.json", {"phase": "applied", "delivery_verified": True})
    result = discovery._ledger_dv_binding(home)
    assert result["state"] == "drift"
    assert result["sample"] == "unbound=applied records=2 bool_dv=2"
    assert result["unbound"] == 1


def test_dv_binding_false_without_verification_not_unbound(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "reverted", "delivery_verified": False})
    result = discovery._ledger_dv_binding(home)
    assert result["state"] == "verified"
    assert result["sample"] == "unbound=0 records=1 bool_dv=1"
    assert result["unbound"] == 0


def test_dv_binding_non_boolean_dv_ignored(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "applied", "delivery_verified": "yes"})
    result = discovery._ledger_dv_binding(home)
    assert result["state"] == "verified"
    assert result["sample"] == "unbound=0 records=1 bool_dv=0"


def test_dv_binding_missing_store_is_unknown(tmp_path):
    result = discovery._ledger_dv_binding(tmp_path / "site")
    assert result["state"] == "unknown"
    assert result["sample"] == "patch store unavailable"


def test_dv_binding_empty_store_is_unknown(tmp_path):
    (tmp_path / "site" / "patches").mkdir(parents=True)
    result = discovery._ledger_dv_binding(tmp_path / "site")
    assert result["state"] == "unknown"
    assert result["sample"] == "records=0 bool_dv=0"


def test_dv_binding_unreadable_record_keeps_detected_unbound(tmp_path):
    home = tmp_path / "site"
    _write_patch(home, "a.json", {"phase": "applied", "delivery_verified": True})
    (home / "patches" / "broken.json").write_text("not json\n", encoding="utf-8")
    result = discovery._ledger_dv_binding(home)
    assert result["state"] == "unknown"
    assert result["sample"] == ("records=1 bool_dv=1 unreadable=broken.json "
                                "unbound=applied")
    assert result["unbound"] == 1


def _write_pin(home: Path, sha: str, *, source: object = "auto") -> None:
    """Write a runtime pin; ``source`` ``auto`` creates a present release dir,
    a string is written as named (existing or not), and ``None`` omits it."""
    (home / "health").mkdir(parents=True, exist_ok=True)
    record = {"version": 1, "sha": sha}
    if source == "auto":
        release = home / "releases"
        release.mkdir(parents=True, exist_ok=True)
        record["source"] = str(release)
    elif source is not None:
        record["source"] = source
    (home / "health" / "runtime-release.json").write_text(
        json.dumps(record), encoding="utf-8")


def _mock_pin_git(monkeypatch, head_sha: str, lag: int, oldest_time: str) -> None:
    class _FakeGit:
        TimeoutExpired = subprocess.TimeoutExpired

        def run(self, cmd, **kwargs):
            if cmd[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(cmd, 0, head_sha, "")
            if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[:3] == ["git", "rev-list", "--count"]:
                return subprocess.CompletedProcess(cmd, 0, str(lag), "")
            if cmd[:3] == ["git", "log", "--format=%cI"]:
                return subprocess.CompletedProcess(cmd, 0, oldest_time, "")
            if cmd[:4] == ["git", "log", "--oneline", "-L"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(f"unexpected git command: {cmd}")
    monkeypatch.setattr(discovery, "subprocess", _FakeGit())

def _mock_staleness(monkeypatch, ids: list[str] | None = None,
                     err: str | None = None) -> None:
    """Mock ``_served_staleness`` so pin-lag tests don't invoke git log -L."""
    monkeypatch.setattr(
        discovery, "_served_staleness",
        lambda checkout, pin, head: (ids or [], err))


class _FakeEmitterGit:
    """Fake git for ``_served_staleness``: serves committed bytes and log -L."""

    def __init__(self, committed: dict[str, str], changed: set[str]):
        self.committed = committed      # rel path -> committed bytes
        self.changed = changed          # rel paths whose producing unit changed
        self.specs: list[str] = []

    def run(self, cmd, **kwargs):
        if cmd[:2] == ["git", "show"]:
            rel = cmd[2].split(":", 1)[1]
            if rel not in self.committed:
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: missing")
            return subprocess.CompletedProcess(cmd, 0, self.committed[rel], "")
        if cmd[:4] == ["git", "log", "--oneline", "-L"]:
            self.specs.append(cmd[4])
            rel = cmd[4].split(":", 1)[1]
            body = "abc1234 touch producer\n" if rel in self.changed else ""
            return subprocess.CompletedProcess(cmd, 0, body, "")
        raise AssertionError(f"unexpected git command: {cmd}")


def test_served_staleness_names_module_level_sense_in_peer_emitter(tmp_path, monkeypatch):
    """A ``sense.*`` id defined as a module-level constant in a peer emitter is
    named when a function that references it changed after the pin.

    ``body_perception`` defines ``sense.body.redmi10``/``.light`` as module-level
    constants; the scan must attribute those to their referencing producers
    instead of skipping them for having no enclosing function.
    """
    body_src = (
        'SENSE_ID = "sense.body.redmi10"\n'
        '\n'
        '\n'
        'def read_redmi10():\n'
        '    return {"id": SENSE_ID}\n')
    git = _FakeEmitterGit(
        {"src/mishe_tauftauf/discovery.py": 'def sample():\n    return "sense.inline.only"\n',
         "src/mishe_tauftauf/body_perception.py": body_src},
        changed={"src/mishe_tauftauf/body_perception.py"})
    monkeypatch.setattr(discovery, "subprocess", git)

    ids, err = discovery._served_staleness(tmp_path, "a" * 40, "b" * 40)
    assert err is None
    assert ids == ["sense.body.redmi10"]
    assert any(s.endswith(":src/mishe_tauftauf/body_perception.py") for s in git.specs)


def test_served_staleness_uses_committed_bytes_not_dirty_worktree(tmp_path, monkeypatch):
    """Line numbers come from the committed emitter, so a dirty worktree cannot
    shift them and misattribute staleness to unrelated senses."""
    src = tmp_path / "src" / "mishe_tauftauf"
    src.mkdir(parents=True)
    # The worktree copy has 40 extra lines that would shift every later line.
    (src / "discovery.py").write_text(
        "# dirty local edit\n" * 40 + 'def sample():\n    return "sense.inline.only"\n',
        encoding="utf-8")
    git = _FakeEmitterGit(
        {"src/mishe_tauftauf/discovery.py": 'def sample():\n    return "sense.inline.only"\n',
         "src/mishe_tauftauf/body_perception.py": 'SENSE_ID = "sense.body.redmi10"\n'},
        changed={"src/mishe_tauftauf/discovery.py"})
    monkeypatch.setattr(discovery, "subprocess", git)

    ids, err = discovery._served_staleness(tmp_path, "a" * 40, "b" * 40)
    assert err is None
    assert ids == ["sense.inline.only"]
    # Traced range is the committed file's line 2, not the worktree's line 42.
    assert "2,2:src/mishe_tauftauf/discovery.py" in git.specs


def test_served_staleness_unreadable_emitter_is_unknown(tmp_path, monkeypatch):
    """An emitter that cannot be read at HEAD fails closed."""
    git = _FakeEmitterGit({}, changed=set())
    monkeypatch.setattr(discovery, "subprocess", git)
    ids, err = discovery._served_staleness(tmp_path, "a" * 40, "b" * 40)
    assert ids == []
    assert err == "emitter unreadable"


def test_served_staleness_undecodable_emitter_is_unknown(tmp_path, monkeypatch):
    """Non-UTF-8 emitter bytes fail closed instead of crashing the whole scan."""
    class _FakeGit:
        def run(self, cmd, **kwargs):
            if cmd[:2] == ["git", "show"]:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            raise AssertionError(f"unexpected git command: {cmd}")
    monkeypatch.setattr(discovery, "subprocess", _FakeGit())
    ids, err = discovery._served_staleness(tmp_path, "a" * 40, "b" * 40)
    assert ids == []
    assert err == "emitter unreadable"


def test_pin_lag_equal_to_head(tmp_path, monkeypatch):
    home = tmp_path / "site"
    sha = "a" * 40
    _write_pin(home, sha)
    _mock_pin_git(monkeypatch, sha, 0, "")
    result = discovery._runtime_pin_lag(home)
    assert result["state"] == "verified"
    assert result["sample"] == f"pin={sha[:7]} head={sha[:7]} lag=0 stale_senses=0"
    assert result["lag"] == 0


def test_pin_lag_behind_head(tmp_path, monkeypatch):
    home = tmp_path / "site"
    pin = "a" * 40
    head = "b" * 40
    _write_pin(home, pin)
    oldest = "2026-10-06T16:00:00+00:00\n2026-10-06T17:00:00+00:00"
    _mock_pin_git(monkeypatch, head, 3, oldest)
    _mock_staleness(monkeypatch, [])
    now = datetime(2026, 10, 6, 18, 0, 0, tzinfo=timezone.utc)
    result = discovery._runtime_pin_lag(home, now=now)
    assert result["state"] == "verified"
    assert result["sample"] == f"pin={pin[:7]} head={head[:7]} lag=3 age=1h00m stale_senses=0"
    assert result["lag"] == 3
    assert result["age"] == "1h00m"


def test_pin_lag_missing_pin_file(tmp_path):
    result = discovery._runtime_pin_lag(tmp_path / "site")
    assert result["state"] == "unknown"
    assert result["sample"] == "pin file unavailable"


def test_pin_lag_pin_not_ancestor(tmp_path, monkeypatch):
    home = tmp_path / "site"
    pin = "a" * 40
    head = "b" * 40
    _write_pin(home, pin)

    class _FakeGit:
        def run(self, cmd, **kwargs):
            if cmd[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(cmd, 0, head, "")
            if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            raise AssertionError(f"unexpected git command: {cmd}")
    monkeypatch.setattr(discovery, "subprocess", _FakeGit())
    result = discovery._runtime_pin_lag(home)
    assert result["state"] == "unknown"
    assert result["sample"] == "pin not an ancestor of HEAD"


def test_pin_lag_git_error(tmp_path, monkeypatch):
    home = tmp_path / "site"
    _write_pin(home, "a" * 40)

    class _FakeGit:
        def run(self, cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: not a git repository")
    monkeypatch.setattr(discovery, "subprocess", _FakeGit())
    result = discovery._runtime_pin_lag(home)
    assert result["state"] == "unknown"
    assert "git error" in result["sample"]


def test_pin_lag_release_source_missing_is_unknown(tmp_path, monkeypatch):
    """The pinned release directory is the bytes the services import: a stat
    miss is its own UNKNOWN even though the commit relation still computes."""
    home = tmp_path / "site"
    sha = "a" * 40
    missing = home / "releases" / sha
    _write_pin(home, sha, source=str(missing))
    _mock_pin_git(monkeypatch, sha, 0, "")
    result = discovery._runtime_pin_lag(home)
    assert result["state"] == "unknown"
    assert result["sample"] == f"pin={sha[:7]} release-source-missing path={missing}"
    assert result["source"] == str(missing)


def test_pin_lag_release_source_present_reads_the_lag(tmp_path, monkeypatch):
    home = tmp_path / "site"
    pin = "a" * 40
    head = "b" * 40
    present = tmp_path / "present-release"
    present.mkdir()
    _write_pin(home, pin, source=str(present))
    _mock_pin_git(monkeypatch, head, 3, "2026-10-06T16:00:00+00:00")
    _mock_staleness(monkeypatch, [])
    now = datetime(2026, 10, 6, 18, 0, 0, tzinfo=timezone.utc)
    result = discovery._runtime_pin_lag(home, now=now)
    assert result["state"] == "verified"
    assert result["sample"] == f"pin={pin[:7]} head={head[:7]} lag=3 age=2h00m stale_senses=0"
    assert result["lag"] == 3


@pytest.mark.parametrize("marker", ["unnamed", "not-a-string", "empty"])
def test_pin_lag_unstatable_release_source_is_unknown(tmp_path, marker):
    """A pin record without a usable source string cannot be checked, so the
    reading says so rather than silently skipping the stat."""
    home = tmp_path / "site"
    sha = "a" * 40
    named = {"unnamed": None, "not-a-string": 42, "empty": ""}[marker]
    _write_pin(home, sha, source=named)
    result = discovery._runtime_pin_lag(home)
    assert result["state"] == "unknown"
    assert result["sample"] == "pin release source unnamed"
    assert result["pin"] == sha


def test_pin_lag_record_not_an_object_is_unknown(tmp_path):
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "runtime-release.json").write_text("null", encoding="utf-8")
    result = discovery._runtime_pin_lag(home)
    assert result["state"] == "unknown"
    assert result["sample"] == "pin record not an object"

def test_pin_lag_served_staleness_names_stale_senses(tmp_path, monkeypatch):
    """When _served_staleness finds pre-fix emitters, the sample names the count."""
    home = tmp_path / "site"
    pin = "a" * 40
    head = "b" * 40
    _write_pin(home, pin)
    oldest = "2026-10-06T16:00:00+00:00"
    _mock_pin_git(monkeypatch, head, 3, oldest)
    stale = ["sense.space.host-radio", "sense.coord.dm-disposition-age"]
    _mock_staleness(monkeypatch, stale)
    now = datetime(2026, 10, 6, 18, 0, 0, tzinfo=timezone.utc)
    result = discovery._runtime_pin_lag(home, now=now)
    assert result["state"] == "verified"
    assert result["sample"] == f"pin={pin[:7]} head={head[:7]} lag=3 age=2h00m stale_senses=2"
    assert result["stale_senses"] == 2
    assert result["stale_sense_ids"] == stale


def test_pin_lag_served_staleness_error_reads_unknown(tmp_path, monkeypatch):
    """When the staleness check itself fails, the sample says so honestly."""
    home = tmp_path / "site"
    pin = "a" * 40
    head = "b" * 40
    _write_pin(home, pin)
    oldest = "2026-10-06T16:00:00+00:00"
    _mock_pin_git(monkeypatch, head, 3, oldest)
    _mock_staleness(monkeypatch, err="emitter unreadable: missing")
    now = datetime(2026, 10, 6, 18, 0, 0, tzinfo=timezone.utc)
    result = discovery._runtime_pin_lag(home, now=now)
    assert result["state"] == "verified"
    assert result["sample"] == f"pin={pin[:7]} head={head[:7]} lag=3 age=2h00m stale_senses=unknown"
    assert result["stale_senses"] is None
    assert result["stale_sense_ids"] is None


def _rewind_git(monkeypatch, *, reflog: str, dropped: set[str], applies: dict) -> None:
    """Fake git for the head-rewind sense: reflog text, reachability, tree, paths, patches."""

    def mock_run(cmd, **kwargs):
        if cmd[:2] == ["git", "reflog"]:
            return subprocess.CompletedProcess(cmd, 0, reflog, "")
        if cmd[:2] == ["git", "rev-list"] and "--parents" in cmd:
            # Dropped commits map to a parent from applies["parents"]; absent means a root.
            parents = applies.get("parents", {})
            lines = [f"{sha} {parents[sha]}" if sha in parents else sha
                     for sha in cmd[cmd.index("--no-walk=unsorted") + 1:]]
            return subprocess.CompletedProcess(cmd, 0, "".join(f"{line}\n" for line in lines), "")
        if cmd[:2] == ["git", "rev-list"] and "^HEAD" in cmd:
            # Ancestry of the dropped tips off HEAD; absent means none.
            return subprocess.CompletedProcess(
                cmd, 0, "".join(f"{sha}\n" for sha in applies.get("ancestry", [])), "")
        if cmd[:2] == ["git", "rev-list"]:
            shas = [line.split()[0] for line in reflog.splitlines() if line.strip()]
            ancestors = [sha for sha in shas if sha not in dropped]
            return subprocess.CompletedProcess(
                cmd, 0, "".join(f"{sha}\n" for sha in ancestors), "")
        if cmd[:2] == ["git", "archive"]:
            return subprocess.CompletedProcess(cmd, 0, b"", "")
        if cmd[:2] == ["git", "diff-tree"]:
            return subprocess.CompletedProcess(cmd, 0, applies["paths"], "")
        if cmd[:2] == ["git", "show"]:
            return subprocess.CompletedProcess(cmd, 0, f"PATCH {cmd[-1]}", "")
        if cmd[:2] == ["git", "log"]:
            path = cmd[-1]
            later = applies.get("later", set())
            return subprocess.CompletedProcess(cmd, 0, "later\n" if path in later else "", "")
        if cmd[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "check-ignore"]:
            return subprocess.CompletedProcess(cmd, 0 if applies.get("ignored") else 1, "", "")
        if cmd[:3] == ["git", "cat-file", "blob"]:
            path = cmd[-1].split(":", 1)[1]
            return subprocess.CompletedProcess(cmd, 0, f"PATCH {path}".encode(), "")
        if cmd[:2] == ["git", "cat-file"]:
            path = cmd[-1].split(":", 1)[1]
            present = path not in applies.get("absent_at_head", set())
            return subprocess.CompletedProcess(cmd, 0 if present else 128, "", "")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr(discovery.subprocess, "run", mock_run)
    monkeypatch.setattr(discovery, "_patch_landed",
                        lambda root, dropped: applies.get("patch_landed", 0))
    monkeypatch.setattr(discovery.tarfile, "open", lambda **kwargs: _MockTar())
    monkeypatch.setattr(discovery, "_applies",
                        lambda tree, patch, *, reverse: applies["verdicts"][(patch, reverse)])


class _MockTar:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def extractall(self, path, filter=None):
        pass


def test_head_rewinds_with_no_dropped_commit_reads_verified(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    _rewind_git(monkeypatch, reflog=f"{'a' * 40} reset: moving to HEAD~1\n",
                dropped=set(), applies={"paths": "", "verdicts": {}})
    result = discovery._head_rewinds(home)
    assert result["state"] == "verified"
    assert result["sample"] == ("dropped-commits=0 of last-200-reflog-entries "
                                 "classified-commits=0 dropped-ancestry-commits=0")
    assert result["dropped"] == 0
    assert result["dropped_ancestry_commits"] == 0


def test_head_rewinds_present_change_reads_lossless(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    sha = "b" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/a.py\n",
                         "verdicts": {("PATCH src/a.py", True): True,
                                      ("PATCH src/a.py", False): False}})
    result = discovery._head_rewinds(home)
    assert result["state"] == "verified"
    assert result["sample"] == ("dropped-commits=1 of last-200-reflog-entries "
                                 "classified-commits=1 unexamined-paths=0 lossless-paths=1 "
                                 "needs-review-paths=0 absent-paths=0 preserved-untracked-paths=0 detracked-edited-paths=0 superseded-paths=0 "
                                 "absent-at-head-paths=0 unexplained-paths=0 "
                                 "superseded-intact-paths=0 superseded-rewritten-paths=0 "
                                 "superseded-absent-lines=0/0 patch-landed-commits=0 rewrite-chain-unreachable-commits=0 rewrite-parent-reachable-commits=0 rewrite-root-commits=1 dropped-ancestry-commits=0")


def test_head_rewinds_absent_change_reads_drift(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    sha = "b" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/a.py\n",
                         "verdicts": {("PATCH src/a.py", True): False,
                                      ("PATCH src/a.py", False): True}})
    result = discovery._head_rewinds(home)
    assert result["state"] == "drift"
    assert result["sample"] == ("dropped-commits=1 of last-200-reflog-entries "
                                 "classified-commits=1 unexamined-paths=0 lossless-paths=0 "
                                 "needs-review-paths=0 absent-paths=1 preserved-untracked-paths=0 detracked-edited-paths=0 superseded-paths=0 "
                                 "absent-at-head-paths=0 unexplained-paths=0 "
                                 "superseded-intact-paths=0 superseded-rewritten-paths=0 "
                                 "superseded-absent-lines=0/0 patch-landed-commits=0 rewrite-chain-unreachable-commits=0 rewrite-parent-reachable-commits=0 rewrite-root-commits=1 dropped-ancestry-commits=0")


def test_head_rewinds_recommitted_change_is_its_own_verdict(tmp_path, monkeypatch):
    """A path that applies neither way is needs-review, never folded into lossless."""
    home = tmp_path / "site"
    home.mkdir()
    sha = "b" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/a.py\nsrc/b.py\n",
                         "verdicts": {("PATCH src/a.py", True): False,
                                      ("PATCH src/a.py", False): False,
                                      ("PATCH src/b.py", True): True,
                                      ("PATCH src/b.py", False): False}})
    result = discovery._head_rewinds(home)
    assert result["state"] == "drift"
    assert result["sample"] == ("dropped-commits=1 of last-200-reflog-entries "
                                 "classified-commits=1 unexamined-paths=0 lossless-paths=1 "
                                 "needs-review-paths=1 absent-paths=0 preserved-untracked-paths=0 detracked-edited-paths=0 superseded-paths=0 "
                                 "absent-at-head-paths=0 unexplained-paths=1 "
                                 "superseded-intact-paths=0 superseded-rewritten-paths=0 "
                                 "superseded-absent-lines=0/0 patch-landed-commits=0 rewrite-chain-unreachable-commits=0 rewrite-parent-reachable-commits=0 rewrite-root-commits=1 dropped-ancestry-commits=0")


def test_head_rewinds_same_patch_id_on_head_does_not_clear_lossless(tmp_path, monkeypatch):
    """A re-landed patch-id is no lossless authority; only the bytes on HEAD are.

    Regression: the dropped change re-landed (same patch-id), then a later commit
    reverted it. HEAD no longer holds the bytes, so the path must not read lossless.
    """
    home = tmp_path / "site"
    home.mkdir()
    sha, head = "b" * 40, "c" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/a.py\n", "patch_landed": 1,
                         "verdicts": {("PATCH src/a.py", True): False,
                                      ("PATCH src/a.py", False): False}})
    result = discovery._head_rewinds(home)
    assert result["state"] == "drift"
    assert result["lossless"] == 0
    assert result["needs_review"] == 1
    assert result["patch_landed_commits"] == 1
    assert "lossless-paths=0 needs-review-paths=1" in result["sample"]
    assert result["sample"].endswith("patch-landed-commits=1 rewrite-chain-unreachable-commits=0 rewrite-parent-reachable-commits=0 rewrite-root-commits=1 dropped-ancestry-commits=0")


def test_head_rewinds_different_patch_id_stays_needs_review(tmp_path, monkeypatch):
    """A HEAD commit touching the path with a different patch-id does not clear the path."""
    home = tmp_path / "site"
    home.mkdir()
    sha, head = "b" * 40, "c" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/a.py\n", "patch_landed": 0,
                         "verdicts": {("PATCH src/a.py", True): False,
                                      ("PATCH src/a.py", False): False}})
    result = discovery._head_rewinds(home)
    assert result["state"] == "drift"
    assert result["needs_review"] == 1
    assert result["lossless"] == 0
    assert result["patch_landed_commits"] == 0


def _preserved_case(tmp_path, monkeypatch, *, ignored: bool, on_disk: bytes):
    home = tmp_path / "site"
    home.mkdir()
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "a.py").write_bytes(on_disk)
    sha = "b" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/a.py\n", "ignored": ignored,
                         "verdicts": {("PATCH src/a.py", True): False,
                                      ("PATCH src/a.py", False): True}})
    return discovery._head_rewinds(home)


def test_head_rewinds_ignored_identical_absent_path_is_preserved_on_disk(tmp_path, monkeypatch):
    """Gitignored, untracked, bytes equal the dropped blob: named preserved, not absent."""
    result = _preserved_case(tmp_path, monkeypatch, ignored=True,
                             on_disk=b"PATCH src/a.py")
    assert result["absent"] == 0
    assert result["preserved_untracked"] == 1
    assert result["state"] == "verified"
    assert "absent-paths=0 preserved-untracked-paths=1" in result["sample"]


def test_head_rewinds_not_ignored_identical_absent_path_stays_absent(tmp_path, monkeypatch):
    result = _preserved_case(tmp_path, monkeypatch, ignored=False,
                             on_disk=b"PATCH src/a.py")
    assert result["absent"] == 1
    assert result["preserved_untracked"] == 0
    assert result["state"] == "drift"


def test_head_rewinds_ignored_divergent_absent_path_stays_absent(tmp_path, monkeypatch):
    result = _preserved_case(tmp_path, monkeypatch, ignored=True,
                             on_disk=b"redacted, different bytes")
    assert result["absent"] == 1
    assert result["preserved_untracked"] == 0
    assert result["state"] == "drift"
    # Edited de-tracked bytes are counted separately; the bucket verdict is unchanged.
    assert result["detracked_edited"] == 1
    assert "detracked-edited-paths=1" in result["sample"]


def test_head_rewinds_identical_preserved_path_has_no_edited_count(tmp_path, monkeypatch):
    result = _preserved_case(tmp_path, monkeypatch, ignored=True, on_disk=b"PATCH src/a.py")
    assert result["detracked_edited"] == 0
    assert "detracked-edited-paths=0" in result["sample"]


def test_preserved_on_disk_real_repo_conditions(tmp_path):
    """Synthetic repo: ignored+identical=preserved; not ignored or differing or tracked=not."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args, text=True):
        return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo,
                              capture_output=True, text=text,
                              env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    git("init", "-q")
    (repo / "private").mkdir()
    (repo / "private" / "notes.txt").write_text("old\n")
    git("add", "private/notes.txt")
    git("commit", "-q", "-m", "track notes")
    dropped = git("rev-parse", "HEAD").stdout.strip()
    # HEAD de-tracks the file and ignores it; the bytes stay on disk.
    git("rm", "-q", "--cached", "private/notes.txt")
    (repo / ".gitignore").write_text("private/\n")
    git("add", ".gitignore")
    git("commit", "-q", "-m", "untrack notes")
    empty_tree = tmp_path / "head_tree"
    empty_tree.mkdir()

    def run(*args, text=True):
        return git(*args, text=text)

    state = discovery._detracked_disk_state
    assert state(repo, dropped, "private/notes.txt", empty_tree, run) == "preserved"
    (repo / ".gitignore").write_text("")
    assert state(repo, dropped, "private/notes.txt", empty_tree, run) == "other"
    (repo / ".gitignore").write_text("private/\n")
    (repo / "private" / "notes.txt").write_text("redacted\n")
    assert state(repo, dropped, "private/notes.txt", empty_tree, run) == "edited"
    (repo / "private" / "notes.txt").write_text("old\n")
    (empty_tree / "private").mkdir()
    (empty_tree / "private" / "notes.txt").write_text("old\n")
    assert state(repo, dropped, "private/notes.txt", empty_tree, run) == "other"


def _repo_git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    out = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_patch_landed_matches_cherry_pick_and_reword_not_absent_change(tmp_path):
    """Real-git fixture: whole-commit patch-id matches a re-landed diff, not an absent one.

    X is cherry-picked onto HEAD (new hash, same diff). Z is reworded (amend: new
    hash, same diff). W is dropped with no HEAD copy. Only X and Z count as landed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo_git(repo, "init", "-q")
    (repo / "base.txt").write_text("base\n")
    _repo_git(repo, "add", "base.txt")
    _repo_git(repo, "commit", "-q", "-m", "base")
    base = _repo_git(repo, "rev-parse", "HEAD")

    (repo / "b.txt").write_text("hello\n")
    _repo_git(repo, "add", "b.txt")
    _repo_git(repo, "commit", "-q", "-m", "x")
    dropped_x = _repo_git(repo, "rev-parse", "HEAD")
    _repo_git(repo, "reset", "-q", "--hard", base)
    _repo_git(repo, "cherry-pick", dropped_x)

    (repo / "c.txt").write_text("z\n")
    _repo_git(repo, "add", "c.txt")
    _repo_git(repo, "commit", "-q", "-m", "old message")
    dropped_z = _repo_git(repo, "rev-parse", "HEAD")
    _repo_git(repo, "commit", "-q", "--amend", "-m", "reworded message")

    (repo / "d.txt").write_text("w\n")
    _repo_git(repo, "add", "d.txt")
    _repo_git(repo, "commit", "-q", "-m", "w")
    dropped_w = _repo_git(repo, "rev-parse", "HEAD")
    _repo_git(repo, "reset", "-q", "--hard", "HEAD~1")

    assert discovery._patch_landed(repo, [dropped_x, dropped_z, dropped_w]) == 2
    assert discovery._patch_landed(repo, []) == 0


def test_head_rewinds_counts_rebuilt_line_ancestry_behind_one_tip(tmp_path, monkeypatch):
    # Fixture shape from the live blind spot: a rebuilt line of 10 commits leaves
    # one reflog tip. The tip is classified; its 10 ancestors off HEAD are counted
    # as ancestry, so the coverage gap is visible rather than read as "one rewind".
    home = tmp_path / "site"
    home.mkdir()
    tip = "c" * 40
    ancestors = [f"{i:040x}" for i in range(1, 11)]
    _rewind_git(monkeypatch, reflog=f"{tip} reset: moving to HEAD~1\n", dropped={tip},
                applies={"paths": "src/a.py\n", "ancestry": ancestors,
                         "verdicts": {("PATCH src/a.py", True): True,
                                      ("PATCH src/a.py", False): False}})
    result = discovery._head_rewinds(home)
    assert result["dropped"] == 1
    assert result["classified_commits"] == 1
    assert result["dropped_ancestry_commits"] == 10
    assert "dropped-ancestry-commits=10" in result["sample"]
    # The tip is examined and the line's other ancestors are not: the reading
    # cannot claim verified coverage while counted ancestry is unclassified.
    assert result["state"] == "unknown"


def test_head_rewinds_unreadable_ancestry_reads_unknown(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()
    sha = "d" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/a.py\n", "verdicts": {}})
    real_run = discovery.subprocess.run

    def failing(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-list"] and "^HEAD" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(discovery.subprocess, "run", failing)
    result = discovery._head_rewinds(home)
    assert result["state"] == "unknown"
    assert result["sample"] == "dropped-ancestry unreadable"


def test_head_rewinds_unreadable_reflog_reads_unknown(tmp_path, monkeypatch):
    home = tmp_path / "site"
    home.mkdir()

    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "fatal: bad revision")

    monkeypatch.setattr(discovery.subprocess, "run", mock_run)
    result = discovery._head_rewinds(home)
    assert result["state"] == "unknown"
    assert result["sample"] == "reflog unreadable"


def test_head_rewinds_splits_review_paths_by_later_history(tmp_path, monkeypatch):
    """Neither-applies paths are split: later-edited on HEAD, absent at HEAD, unexplained.

    The split explains the review count; it does not clear it, so the state stays drift.
    """
    home = tmp_path / "site"
    home.mkdir()
    sha = "b" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/later.py\nsrc/gone.py\nsrc/same.py\n",
                         "later": {"src/later.py"},
                         "absent_at_head": {"src/gone.py"},
                         "verdicts": {(f"PATCH {p}", r): False
                                      for p in ("src/later.py", "src/gone.py", "src/same.py")
                                      for r in (True, False)}})
    result = discovery._head_rewinds(home)
    assert result["state"] == "drift"
    assert result["needs_review"] == 3
    assert result["review_basis"] == {"superseded": 1, "absent-at-head": 1, "unexplained": 1}
    assert result["superseded_content"]["intact"] == 0
    assert result["superseded_content"]["rewritten"] == 1
    assert result["sample"].endswith("needs-review-paths=3 absent-paths=0 preserved-untracked-paths=0 detracked-edited-paths=0 "
                                     "superseded-paths=1 absent-at-head-paths=1 "
                                     "unexplained-paths=1 superseded-intact-paths=0 "
                                     "superseded-rewritten-paths=1 superseded-absent-lines=0/0 patch-landed-commits=0 rewrite-chain-unreachable-commits=0 rewrite-parent-reachable-commits=0 rewrite-root-commits=1 dropped-ancestry-commits=0")


def test_content_presence_intact_when_every_added_line_survives(tmp_path):
    """A later edit that kept the dropped hunk's added lines reads intact."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kept.py").write_text("def f():\n    return 1\nextra = 2\n")
    patch = "@@ -1 +1,2 @@\n+def f():\n+    return 1\n"
    assert discovery._content_presence(patch, tmp_path, "src/kept.py") == ("intact", 0, 2)


def test_content_presence_keeps_plus_plus_content_and_skips_file_header(tmp_path):
    """An added line ``++counter;`` shows as ``+++counter;`` in a hunk; it is content, not a header."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "inc.c").write_text("int n;\n++counter;\n")
    patch = ("diff --git a/src/inc.c b/src/inc.c\n"
             "index 1111111..2222222 100644\n"
             "--- a/src/inc.c\n"
             "+++ b/src/inc.c\n"
             "@@ -1 +1,2 @@\n"
             "+int n;\n"
             "+++counter;\n")
    assert discovery._content_presence(patch, tmp_path, "src/inc.c") == ("intact", 0, 2)


def test_content_presence_ignores_second_file_header_in_multi_file_patch(tmp_path):
    """A second file's ``+++ b/...`` header after a hunk is a header, not an added line."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "b.py").write_text("y = 2\n")
    patch = ("diff --git a/src/a.py b/src/a.py\n"
             "--- a/src/a.py\n"
             "+++ b/src/a.py\n"
             "@@ -1 +1 @@\n"
             "+x = 1\n"
             "diff --git a/src/b.py b/src/b.py\n"
             "--- a/src/b.py\n"
             "+++ b/src/b.py\n"
             "@@ -1 +1 @@\n"
             "+y = 2\n")
    # Two added lines, one absent from a.py (b's content); b's header adds nothing.
    assert discovery._content_presence(patch, tmp_path, "src/a.py") == ("rewritten", 1, 2)


def test_content_presence_counts_absent_lines_of_a_rewritten_path(tmp_path):
    """One reworded added line reads rewritten, and the line counts show how much changed."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "edited.py").write_text("def f():\n    return 2\n")
    patch = "@@ -1 +1,2 @@\n+def f():\n+    return 1\n"
    assert discovery._content_presence(patch, tmp_path, "src/edited.py") == ("rewritten", 1, 2)


def test_content_presence_rewritten_when_file_gone_or_hunk_adds_nothing(tmp_path):
    """A deleted file or a deletion-only hunk has no surviving added content to confirm."""
    assert discovery._content_presence("+x = 1\n", tmp_path, "src/gone.py") == ("rewritten", 1, 1)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "any.py").write_text("x = 1\n")
    assert discovery._content_presence("-x = 1\n", tmp_path, "src/any.py") == ("rewritten", 0, 0)


def test_head_rewinds_unreadable_path_history_reads_unknown(tmp_path, monkeypatch):
    """A failed `git log` in the basis lookup must not read as a clean DRIFT count."""
    home = tmp_path / "site"
    home.mkdir()
    sha = "b" * 40
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "src/unknown.py\n", "later": set(),
                         "verdicts": {(f"PATCH src/unknown.py", r): False
                                      for r in (True, False)}})
    real_run = discovery.subprocess.run

    def failing_log(cmd, **kwargs):
        if cmd[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(discovery.subprocess, "run", failing_log)
    result = discovery._head_rewinds(home)
    assert result["state"] == "unknown"
    assert result["sample"] == f"path history unreadable: {sha[:7]}"


def test_head_rewinds_bounds_dropped_commits_and_paths(tmp_path, monkeypatch):
    """The reflog window and per-commit path cap keep an unbounded rewind cheap."""
    home = tmp_path / "site"
    home.mkdir()
    shas = ["b" * 40, "c" * 40, "d" * 40, "e" * 40, "f" * 40]
    reflog = "".join(f"{sha} reset: moving to HEAD~1\n" for sha in shas)
    _rewind_git(monkeypatch, reflog=reflog, dropped=set(shas),
                applies={"paths": "".join(f"src/f{i}.py\n" for i in range(99)),
                         "verdicts": {(f"PATCH src/f{i}.py", True): False
                                      for i in range(99)} | {
                             (f"PATCH src/f{i}.py", False): False for i in range(99)}})
    result = discovery._head_rewinds(home)
    # Every dropped commit in the window is counted and adjudicated; the
    # per-commit path cap bounds each commit's work, and the sample names both
    # counts.
    assert result["dropped"] == len(shas)
    # A commit with paths past the cap is not fully classified: its unread
    # paths are counted as unexamined coverage, never as classified or lossless.
    unread = len(shas) * (99 - discovery._HEAD_REWIND_PATH_LIMIT)
    assert result["classified_commits"] == 0
    assert result["unexamined_paths"] == unread
    assert result["needs_review"] == len(shas) * discovery._HEAD_REWIND_PATH_LIMIT
    assert f"unexamined-paths={unread}" in result["sample"]
    assert "classified-commits=0" in result["sample"]
    assert "dropped-commits=5" in result["sample"]


def test_head_rewinds_unexamined_paths_without_drift_read_unknown(tmp_path, monkeypatch):
    """Lossless paths that fit the cap cannot verify a commit whose tail was never read."""
    home = tmp_path / "site"
    home.mkdir()
    sha = "b" * 40
    paths = [f"src/f{i}.py" for i in range(discovery._HEAD_REWIND_PATH_LIMIT + 1)]
    _rewind_git(monkeypatch, reflog=f"{sha} reset: moving to HEAD~1\n", dropped={sha},
                applies={"paths": "".join(f"{p}\n" for p in paths),
                         "verdicts": {(f"PATCH {p}", True): True for p in paths}
                                     | {(f"PATCH {p}", False): False for p in paths}})
    result = discovery._head_rewinds(home)
    assert result["state"] == "unknown"
    assert result["unexamined_paths"] == 1
    assert result["classified_commits"] == 0
    assert result["lossless"] == discovery._HEAD_REWIND_PATH_LIMIT
    assert "unexamined-paths=1" in result["sample"]

def _scan(home: Path, created: str, free_bytes: int) -> None:
    """Write a minimal scan file with one verified disk.free observation."""
    discovery = home / "discovery"
    discovery.mkdir(exist_ok=True)
    doc = {"created": created,
           "observations": [{"id": "sense.disk.free", "state": "verified",
                             "sample": free_bytes}]}
    (discovery / f"scan-{created.replace(':', '')}.json").write_text(
        json.dumps(doc), encoding="utf-8")


def test_disk_trend_no_corpus_reads_unknown(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._disk_trend(home)
    assert result["state"] == "unknown"
    assert result["sample"] == "discovery corpus unavailable"


def test_disk_trend_fewer_than_two_samples_reads_unknown(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    _scan(home, "2026-10-16T17:00:00Z", 70 * 2**30)
    result = discovery._disk_trend(home)
    assert result["state"] == "unknown"
    assert "fewer than 2" in result["sample"]


def test_disk_trend_short_span_reads_unknown(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    _scan(home, "2026-10-16T17:00:00Z", 70 * 2**30)
    _scan(home, "2026-10-16T17:05:00Z", 70 * 2**30)
    result = discovery._disk_trend(home)
    assert result["state"] == "unknown"
    assert "span" in result["sample"]


def test_disk_trend_stable_reads_verified(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    now = datetime(2026, 10, 16, 17, 0, 0, tzinfo=timezone.utc)
    for i in range(200):
        ts = (now - timedelta(minutes=10 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _scan(home, ts, 70 * 2**30)
    result = discovery._disk_trend(home, now=now)
    assert result["state"] == "verified"
    assert "min24=" in result["sample"]
    assert result["samples"] == 200


def test_disk_trend_fast_drain_reads_drift(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    now = datetime(2026, 10, 16, 17, 0, 0, tzinfo=timezone.utc)
    for i in range(200):
        ts = (now - timedelta(minutes=10 * (199 - i))).strftime("%Y-%m-%dT%H:%M:%SZ")
        free = int((300 - i * 100 / 144) * 2**30)
        _scan(home, ts, free)
    result = discovery._disk_trend(home, now=now)
    assert result["state"] == "drift"
    assert "drain" in result["sample"]


def test_disk_trend_low_minimum_reads_drift(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    now = datetime(2026, 10, 16, 17, 0, 0, tzinfo=timezone.utc)
    for i in range(200):
        ts = (now - timedelta(minutes=10 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _scan(home, ts, 15 * 2**30)
    result = discovery._disk_trend(home, now=now)
    assert result["state"] == "drift"
    assert "below" in result["sample"]


def _fake_git_show(checkout: Path):
    """A ``git show <sha>:<rel>`` handler serving the worktree file under ``checkout``."""
    def _run(cmd):
        rel = cmd[2].split(":", 1)[1]
        path = checkout / rel
        if not path.exists():
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: missing")
        return subprocess.CompletedProcess(cmd, 0, path.read_text(encoding="utf-8"), "")
    return _run


def test_served_staleness_happy_path(tmp_path, monkeypatch):
    """_served_staleness parses the emitter and checks git log -L per unit."""
    checkout = tmp_path / "checkout"
    emitter = checkout / "src" / "mishe_tauftauf" / "discovery.py"
    emitter.parent.mkdir(parents=True)
    emitter.write_text(
        'def _foo():\n'
        '    return {"id": "sense.test.foo", "state": "verified"}\n'
        'def sample():\n'
        '    pass\n'
    )
    (emitter.parent / "body_perception.py").write_text('SENSE_ID = "sense.test.body"\n')
    pin = "a" * 40
    head = "b" * 40

    class _FakeGit:
        def run(self, cmd, **kwargs):
            if cmd[:2] == ["git", "show"]:
                return _fake_git_show(checkout)(cmd)
            if cmd[:4] == ["git", "log", "--oneline", "-L"]:
                return subprocess.CompletedProcess(cmd, 0, "abc1234 fix\n", "")
            raise AssertionError(f"unexpected git command: {cmd}")
    monkeypatch.setattr(discovery, "subprocess", _FakeGit())
    stale, err = discovery._served_staleness(checkout, pin, head)
    assert err is None
    assert stale == ["sense.test.foo"]


def test_served_staleness_emitter_unreadable(tmp_path):
    """A missing emitter file reads as an error, not an empty result."""
    checkout = tmp_path / "checkout"
    stale, err = discovery._served_staleness(checkout, "a" * 40, "b" * 40)
    assert stale == []
    assert err is not None
    assert "emitter unreadable" in err


def test_served_staleness_emitter_unparseable(tmp_path, monkeypatch):
    """A syntax-error emitter reads as an error, not a crash."""
    checkout = tmp_path / "checkout"
    emitter = checkout / "src" / "mishe_tauftauf" / "discovery.py"
    emitter.parent.mkdir(parents=True)
    emitter.write_text("def broken(:\n")

    class _FakeGit:
        def run(self, cmd, **kwargs):
            if cmd[:2] == ["git", "show"]:
                return _fake_git_show(checkout)(cmd)
            raise AssertionError(f"unexpected git command: {cmd}")
    monkeypatch.setattr(discovery, "subprocess", _FakeGit())
    stale, err = discovery._served_staleness(checkout, "a" * 40, "b" * 40)
    assert stale == []
    assert err is not None
    assert "emitter unparseable" in err


def test_served_staleness_git_error_returns_none_commits(tmp_path, monkeypatch):
    """A git log -L failure returns None commits, which reads as not stale."""
    checkout = tmp_path / "checkout"
    emitter = checkout / "src" / "mishe_tauftauf" / "discovery.py"
    emitter.parent.mkdir(parents=True)
    emitter.write_text(
        'def _foo():\n'
        '    return {"id": "sense.test.foo", "state": "verified"}\n'
    )
    (emitter.parent / "body_perception.py").write_text('SENSE_ID = "sense.test.body"\n')

    class _FakeGit:
        def run(self, cmd, **kwargs):
            if cmd[:2] == ["git", "show"]:
                return _fake_git_show(checkout)(cmd)
            if cmd[:4] == ["git", "log", "--oneline", "-L"]:
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: bad revision")
            raise AssertionError(f"unexpected git command: {cmd}")
    monkeypatch.setattr(discovery, "subprocess", _FakeGit())
    stale, err = discovery._served_staleness(checkout, "a" * 40, "b" * 40)
    assert err is None
    assert stale == []


def test_served_staleness_no_stale_senses(tmp_path, monkeypatch):
    """When no emitter changed after the pin, the result is an empty list."""
    checkout = tmp_path / "checkout"
    emitter = checkout / "src" / "mishe_tauftauf" / "discovery.py"
    emitter.parent.mkdir(parents=True)
    emitter.write_text(
        'def _foo():\n'
        '    return {"id": "sense.test.foo", "state": "verified"}\n'
    )
    (emitter.parent / "body_perception.py").write_text('SENSE_ID = "sense.test.body"\n')

    class _FakeGit:
        def run(self, cmd, **kwargs):
            if cmd[:2] == ["git", "show"]:
                return _fake_git_show(checkout)(cmd)
            if cmd[:4] == ["git", "log", "--oneline", "-L"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(f"unexpected git command: {cmd}")
    monkeypatch.setattr(discovery, "subprocess", _FakeGit())
    stale, err = discovery._served_staleness(checkout, "a" * 40, "b" * 40)
    assert err is None
    assert stale == []


def _tape_entry(sequence: int, timestamp: str, source: str, body: str) -> bytes:
    header = f"v1 {sequence:020d} {timestamp} {source} ::\n"
    lines = body.splitlines(keepends=True)
    if not lines:
        lines = [body]
    framed = []
    for line in lines:
        if line.endswith("\n"):
            framed.append("    | " + line)
        else:
            framed.append("    | " + line + "\n")
    terminator = "    .\n" if body.endswith("\n") else "    .-\n"
    return (header + "".join(framed) + terminator).encode("utf-8")


def _write_outcome(home: Path, role: str) -> str:
    (home / "records").mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "kind": "wall-outcome",
               "payload": {"role": role, "kind": "accepted", "text": "outcome"}}
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    (home / "records" / f"{digest}.json").write_bytes(data)
    return digest


def _dm_now() -> datetime:
    return datetime(2026, 10, 6, 18, 0, 0, tzinfo=timezone.utc)


def test_dm_disposition_age_replied_outcome_and_open(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    witness_digest = _write_outcome(home, "witness")
    entries = [
        (1, "2026-10-06T12:30:00Z", "health", "[dm] to=genome\n# hi genome\n"),
        (2, "2026-10-06T12:35:00Z", "genome", "[dm] to=health\n# hi health\n"),
        (3, "2026-10-06T13:00:00Z", "health", "[dm] to=witness\n# hi witness\n"),
        (4, "2026-10-06T13:30:00Z", "witness",
         f"[record] records/{witness_digest}.json sha256={witness_digest}\n"),
        (5, "2026-10-06T14:00:00Z", "health", "[dm] to=docs\n# hi docs\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    assert result["dms"] == 4
    # health->genome is credited by the reply edge; health->witness by the wall join.
    # genome->health is open: health's later DMs go to witness and docs, not genome.
    assert result["reply_edge"] == 1
    assert result["seq_credit"] == 0
    assert result["credit_only"] == 0
    assert result["wall_only"] == 1
    assert result["dispositioned"] == 2
    assert result["open"] == 2
    assert result["stall"] == 2
    assert result["p50_minutes"] == 18
    assert result["p90_minutes"] == 28
    assert result["oldest_open"] == "health:5h25m"
    assert result["sample"] == ("dms=4 reply_edge=1 seq_credit=0 credit_only=0 "
                                "wall_only=1 dispositioned=2 open=2 stall=2 "
                                "p50=18m p90=28m broadcast_excluded=0 "
                                "nonmind_excluded=0 oldest_open=health:5h25m")


def test_dm_disposition_age_reply_to_third_party_does_not_disposition(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    entries = [
        (1, "2026-10-06T12:30:00Z", "health", "[dm] to=genome\n# hi genome\n"),
        (2, "2026-10-06T12:35:00Z", "genome", "[dm] to=witness\n# hi witness\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    assert result["dms"] == 2
    assert result["reply_edge"] == 0
    assert result["dispositioned"] == 0
    assert result["open"] == 2
    assert result["stall"] == 2
    assert result["oldest_open"] == "genome:5h30m"


def test_dm_disposition_age_missing_tape(tmp_path):
    result = discovery._coord_dm_disposition_age(tmp_path / "site", now=_dm_now())
    assert result["state"] == "unknown"
    assert result["sample"] == "chat tape unavailable"


def test_dm_disposition_age_missing_records(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    entries = [
        (1, "2026-10-06T12:30:00Z", "health", "[dm] to=genome\n# hi genome\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "unknown"
    assert result["sample"] == "outcome store unavailable"


def test_dm_disposition_age_empty_window(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    entries = [
        (1, "2026-10-06T10:00:00Z", "health", "[dm] to=genome\n# hi genome\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    assert result["dms"] == 0
    assert result["dispositioned"] == 0
    assert result["open"] == 0
    assert result["sample"] == ("dms=0 reply_edge=0 seq_credit=0 credit_only=0 "
                                "wall_only=0 dispositioned=0 open=0 stall=0 "
                                "broadcast_excluded=0 nonmind_excluded=0")

def test_dm_disposition_age_uses_earliest_outcome(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    # Two outcomes for the same role at different times; the disposition age
    # must use the earliest, regardless of record filename (digest) order.
    early = _write_outcome(home, "genome")
    late = _write_outcome(home, "genome")
    entries = [
        (1, "2026-10-06T12:00:00Z", "health", "[dm] to=genome\n# hi genome\n"),
        (2, "2026-10-06T12:30:00Z", "genome",
         f"[record] records/{early}.json sha256={early}\n"),
        (3, "2026-10-06T13:00:00Z", "genome",
         f"[record] records/{late}.json sha256={late}\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    assert result["dms"] == 1
    assert result["dispositioned"] == 1
    assert result["open"] == 0
    # The earliest outcome (12:30) dispositions the DM, not the later (13:00).
    assert result["p50_minutes"] == 30


def test_dm_disposition_age_seq_citation_dispositions(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    entries = [
        (1, "2026-10-06T12:30:00Z", "health", "[dm] to=genome\n# hi genome\n"),
        (2, "2026-10-06T13:00:00Z", "genome",
         "Wall outcome accepted by genome\n# genome — wake 2\n\nRead DM 1. Acked.\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    assert result["dms"] == 1
    # The outcome body cites the DM's seq token: credited without a reply.
    assert result["reply_edge"] == 0
    assert result["seq_credit"] == 1
    assert result["credit_only"] == 1
    assert result["wall_only"] == 0
    assert result["dispositioned"] == 1
    assert result["open"] == 0
    assert result["p50_minutes"] == 30


def test_dm_disposition_age_stall_gate_excludes_young_open(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    entries = [
        (1, "2026-10-06T17:45:00Z", "health", "[dm] to=genome\n# hi genome\n"),
        (2, "2026-10-06T17:00:00Z", "witness", "[dm] to=docs\n# hi docs\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    assert result["dms"] == 2
    assert result["open"] == 2
    # The 17:45 DM is 15m old: right-censored, open but not a stall.
    assert result["stall"] == 1
    assert result["oldest_open"] == "docs:1h00m"

def test_dm_disposition_age_excludes_broadcast_pseudo_targets(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    entries = [
        (1, "2026-10-06T12:00:00Z", "body-research", "[dm] to=chat\n# routine note\n"),
        (2, "2026-10-06T12:30:00Z", "body-research", "[dm] to=all\n# broadcast\n"),
        (3, "2026-10-06T13:00:00Z", "health", "[dm] to=genome\n# hi genome\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    # Only the health->genome DM is counted; chat and all are excluded.
    assert result["dms"] == 1
    assert result["open"] == 1
    assert result["stall"] == 1
    assert result["broadcast_excluded"] == 2
    assert result["oldest_open"] == "genome:5h00m"


def test_dm_disposition_age_excludes_targets_with_no_seated_mind(tmp_path):
    """A watcher, feed or the human shell can never disposition a DM.

    The plant held its own internal check RED at stall=8 while every
    mind-to-mind request in the window was answered: four of the eight were
    DMs addressed to ``operator``, three were health's own answers posted to
    watcher targets (``operator-view``, ``session-watch``, ``silence-watch``),
    and one went to ``ci``. Those routings have no reply edge and no wall
    outcome, so counting them as resolver waits pinned the gate for the whole
    six-hour window. They are counted in ``nonmind_excluded`` — visible, out
    of the stall estimate.
    """
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    entries = [
        (1, "2026-10-06T12:00:00Z", "health", "[dm] to=operator-view\n# recurrence\n"),
        (2, "2026-10-06T12:10:00Z", "health", "[dm] to=session-watch\n# cause\n"),
        (3, "2026-10-06T12:20:00Z", "witness", "[dm] to=ci\n# run stale\n"),
        (4, "2026-10-06T12:30:00Z", "docs", "[dm] to=operator\n# escalation\n"),
        (5, "2026-10-06T13:00:00Z", "health", "[dm] to=genome\n# hi genome\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    assert result["dms"] == 1
    assert result["open"] == 1
    assert result["stall"] == 1
    assert result["nonmind_excluded"] == 4
    assert result["broadcast_excluded"] == 0
    assert result["oldest_open"] == "genome:5h00m"
    assert "nonmind_excluded=4" in result["sample"]


def test_dm_disposition_age_counts_a_site_declared_seat(tmp_path):
    """A role the site actually declares counts as seated; an unnamed one does not.

    Seating is the site's own notion (charters/<slug>.md plus an executable
    minds/<slug>), so a declared resident's DMs are resolver waits while a DM
    to a role with no charter stays excluded.
    """
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True, exist_ok=True)
    (home / "charters").mkdir()
    (home / "charters" / "researcher.md").write_text("# researcher\n", encoding="utf-8")
    (home / "minds").mkdir()
    (home / "minds" / "researcher").write_text("#!/bin/sh\n", encoding="utf-8")
    entries = [
        (1, "2026-10-06T12:30:00Z", "health", "[dm] to=researcher\n# ask\n"),
        (2, "2026-10-06T12:35:00Z", "health", "[dm] to=stranger\n# ask\n"),
    ]
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src, body) for seq, ts, src, body in entries))
    result = discovery._coord_dm_disposition_age(home, now=_dm_now())
    assert result["state"] == "verified"
    assert result["dms"] == 1
    assert result["open"] == 1
    assert result["stall"] == 1
    assert result["nonmind_excluded"] == 1
    assert result["oldest_open"] == "researcher:5h30m"


def _settle_tape(home: Path, settlements: list[tuple[str, str, str]]) -> None:
    """Write one seed settle receipt per (timestamp, role, result)."""
    entries = []
    for seq, (ts, role, result) in enumerate(settlements, start=1):
        body = (f"seed yield {role} wake={seq}\n"
                f"Turn settled ({result}); wall and handoff at walls/{role}.md and "
                f"handoffs/{role}.md. This is a transport receipt, not proof of patch "
                f"acceptance.\n")
        entries.append(_tape_entry(seq, ts, "seed", body))
    (home / "chat.log").write_bytes(b"".join(entries))


def test_settle_result_mix_counts_and_drifts_on_blocked(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    _settle_tape(home, [
        ("2026-10-06T10:00:00Z", "a", "verified"),
        ("2026-10-06T10:10:00Z", "b", "changed"),
        ("2026-10-06T10:20:00Z", "c", "blocked"),
        ("2026-10-06T10:30:00Z", "d", "verified"),
        ("2026-10-06T10:40:00Z", "e", "changed"),
    ])
    result = discovery._seed_settle_result_mix(home)
    assert result["state"] == "drift"
    assert result["window"] == 5
    assert (result["verified"], result["changed"], result["blocked"],
            result["other"], result["incidents"]) == (2, 2, 1, 0, 1)
    assert result["since_last"] == 2
    assert result["sample"] == ("n=5 verified=2 changed=2 blocked=1 other=0 "
                                "incidents=1 since_last=2")


def test_settle_result_mix_clean_window_is_verified(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    _settle_tape(home, [
        ("2026-10-06T10:00:00Z", "a", "verified"),
        ("2026-10-06T10:10:00Z", "b", "changed"),
    ])
    result = discovery._seed_settle_result_mix(home)
    assert result["state"] == "verified"
    assert result["blocked"] == 0
    assert result["incidents"] == 0
    assert result["since_last"] is None
    assert result["sample"] == ("n=2 verified=1 changed=1 blocked=0 other=0 "
                                "incidents=0 since_last=none")


def test_settle_result_mix_all_changed_window_is_not_verified(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    _settle_tape(home, [
        ("2026-10-06T10:00:00Z", "a", "changed"),
        ("2026-10-06T10:10:00Z", "b", "changed"),
    ])
    result = discovery._seed_settle_result_mix(home)
    assert result["state"] == "unknown"
    assert result["verified"] == 0
    assert result["changed"] == 2
    assert result["blocked"] == 0


def test_settle_result_mix_all_verified_window_is_verified(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    _settle_tape(home, [
        ("2026-10-06T10:00:00Z", "a", "verified"),
        ("2026-10-06T10:10:00Z", "b", "verified"),
    ])
    result = discovery._seed_settle_result_mix(home)
    assert result["state"] == "verified"
    assert result["verified"] == 2


def test_settle_result_mix_empty_tape_is_not_verified(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "chat.log").write_bytes(b"")
    result = discovery._seed_settle_result_mix(home)
    assert result["state"] == "unknown"
    assert result["window"] == 0


def test_settle_result_mix_clusters_blocked_within_the_gap(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    _settle_tape(home, [
        ("2026-10-06T10:00:00Z", "a", "blocked"),
        ("2026-10-06T10:30:00Z", "b", "blocked"),
        ("2026-10-06T12:00:00Z", "c", "blocked"),
    ])
    result = discovery._seed_settle_result_mix(home)
    assert result["state"] == "drift"
    assert result["blocked"] == 3
    assert result["incidents"] == 2


def test_settle_result_mix_blocked_outside_window_does_not_drift(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)
    settlements = [(base.isoformat().replace("+00:00", "Z"), "a", "blocked")]
    settlements += [((base + timedelta(minutes=10 * (index + 1)))
                     .isoformat().replace("+00:00", "Z"), "b", "verified")
                    for index in range(100)]
    _settle_tape(home, settlements)
    result = discovery._seed_settle_result_mix(home)
    assert result["state"] == "verified"
    assert result["window"] == 100
    assert result["blocked"] == 0
    assert result["since_last"] == 100


def test_settle_result_mix_ignores_unpaired_and_unknown_results(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    entries = [
        _tape_entry(1, "2026-10-06T10:00:00Z", "seed",
                    "seed yield a wake=1\nTurn settled (verified); wall and handoff at "
                    "walls/a.md.\n"),
        _tape_entry(2, "2026-10-06T10:10:00Z", "seed", "seed yield b wake=2\n"),
        _tape_entry(3, "2026-10-06T10:20:00Z", "senses",
                    "seed yield c wake=3\nTurn settled (blocked); wall and handoff at "
                    "walls/c.md.\n"),
        _tape_entry(4, "2026-10-06T10:30:00Z", "seed",
                    "seed yield d wake=4\nTurn settled (unspecified); wall and handoff at "
                    "walls/d.md.\n"),
    ]
    (home / "chat.log").write_bytes(b"".join(entries))
    result = discovery._seed_settle_result_mix(home)
    # The unpaired yield and the non-seed source are not settlements; the
    # unnamed result lands in ``other`` rather than inventing a class.
    assert result["window"] == 2
    assert result["verified"] == 1
    assert result["other"] == 1
    assert result["blocked"] == 0
    assert result["state"] == "verified"


def test_settle_result_mix_missing_tape(tmp_path):
    result = discovery._seed_settle_result_mix(tmp_path / "site")
    assert result["state"] == "unknown"
    assert result["sample"] == "chat tape unavailable"


def _write_evidenced_outcome(home: Path, role: str, evidence: Path,
                             digest: str | None = None,
                             supersedes: list[str] | None = None) -> str:
    (home / "records").mkdir(parents=True, exist_ok=True)
    if digest is None:
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    payload = {"version": 1, "kind": "wall-outcome",
               "payload": {"role": role, "kind": "accepted", "text": "outcome",
                           "evidence": {"path": str(evidence),
                                        "sha256": digest}}}
    if supersedes:
        payload["payload"]["supersedes"] = list(supersedes)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    record_digest = hashlib.sha256(data).hexdigest()
    (home / "records" / f"{record_digest}.json").write_bytes(data)
    return record_digest


def _binding_tape(home: Path, citations: list[tuple[int, str, str, str]]) -> None:
    (home / "chat.log").write_bytes(
        b"".join(_tape_entry(seq, ts, src,
                            f"Wall outcome accepted by {src}\noutcome\n" + body)
                 for seq, ts, src, body in citations))


def test_evidence_binding_clean_store(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence_a = home / "artifacts" / "wake-a.md"
    evidence_a.write_text("write-once A\n")
    evidence_b = home / "artifacts" / "wake-b.md"
    evidence_b.write_text("write-once B\n")
    digest_a = _write_evidenced_outcome(home, "alice", evidence_a)
    digest_b = _write_evidenced_outcome(home, "bob", evidence_b)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest_a}.json sha256={digest_a}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{digest_b}.json sha256={digest_b}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "verified"
    assert result["bound"] == 2
    assert result["latest_roles"] == 2
    assert result["latest_bad"] == 0
    assert result["all_bad"] == 0



def test_evidence_binding_allows_scan_and_dm_paths_but_reports_edit(tmp_path):
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True)
    (home / "dms").mkdir()
    scan = home / "discovery" / "scan-test.json"
    dm = home / "dms" / "DM-test.md"
    scan.write_text('{"scan":1}\n')
    dm.write_text("immutable DM\n")
    scan_digest = _write_evidenced_outcome(home, "alice", scan)
    dm_digest = _write_evidenced_outcome(home, "bob", dm)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{scan_digest}.json sha256={scan_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{dm_digest}.json sha256={dm_digest}\n"),
    ])

    clean = discovery._ledger_evidence_binding(home)
    assert clean["state"] == "verified"
    assert clean["policy"] == "eligible-paths-v2"
    assert clean["all_bad"] == 0

    scan.write_text('{"scan":2}\n')
    drift = discovery._ledger_evidence_binding(home)
    assert drift["state"] == "drift"
    assert drift["all_bad"] == 1
    assert drift["latest_bad_roles"] == ["alice(B)"]
    assert any("scan-test.json(B)" in item for item in drift["violations"])
    assert not any("DM-test.md" in item for item in drift["violations"])
def test_notify_scan_labels_non_verified_readings_by_state(tmp_path):
    readings = [
        {"id": "sense.a", "kind": "read", "state": "verified", "sample": "ok"},
        {"id": "sense.b", "kind": "read", "state": "drift", "sample": "measured violation"},
        {"id": "sense.c", "kind": "read", "state": "unknown", "sample": "no source"},
        {"id": "sense.d", "kind": "read", "state": "unavailable", "sample": "absent source"},
        {"id": "command.x", "kind": "declaration", "state": "available",
         "sample": "declared"},
    ]
    discovery._notify_scan(tmp_path, {"created": "2026-10-10T12:00:00Z",
                                      "node": "test", "observations": readings},
                            None, tmp_path / "sample.json")
    body = Feed(tmp_path).entries()[-1].body
    assert "Verified readings: sense.a = ok." in body
    assert "Drift readings: sense.b — measured violation." in body
    assert "Unknown readings: sense.c — no source." in body
    assert "Unavailable readings: sense.d — absent source." in body
    # A measured violation is not printed on the line labelled unknown.
    unknown_line = next(line for line in body.splitlines()
                        if line.startswith("Unknown readings:"))
    assert "sense.b" not in unknown_line
    # The guidance line names every non-verified state shown, so a literal
    # reader cannot take a drift reading as needing no verification (witness
    # 64896).
    next_line = next(line for line in body.splitlines() if line.startswith("Next:"))
    for state in ("drift", "unknown", "unavailable"):
        assert state in next_line


def test_evidence_binding_flags_mutated_evidence(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence = home / "artifacts" / "wake-a.md"
    evidence.write_text("original bytes\n")
    digest = _write_evidenced_outcome(home, "alice", evidence)
    evidence.write_text("edited after record\n")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert result["all_bad"] == 1
    assert result["latest_bad"] == 1
    assert "wake-a.md(B)" in result["sample"]


def test_evidence_binding_flags_deleted_evidence(tmp_path):
    """A bound file that has been deleted is absent (C), not a broken digest (B).

    Observed live 2026-10-10: a bound per-outcome evidence file was written,
    its outcome recorded, then deleted by the recording mind as a "scaffold"
    before settling (mechanism verified from that mind's own omp session log,
    witness wake 63338; reproduced by research-methods wake 63341), so the
    sense reported drift. Deletion is a distinct branch from a later edit:
    the file is absent (C), not edited (B), and the existence check must
    keep flagging it rather than dropping the row.
    """
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence = home / "artifacts" / "wake-a.md"
    evidence.write_text("write-once bytes\n")
    digest = _write_evidenced_outcome(home, "alice", evidence)
    evidence.unlink()
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "drift"
    assert result["bound"] == 1
    assert result["all_bad"] == 1
    assert result["latest_bad"] == 1
    assert "wake-a.md(C)" in result["sample"]


def test_evidence_binding_flags_outside_artifacts(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence = home / "walls" / "alice.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("wall file\n")
    digest = _write_evidenced_outcome(home, "alice", evidence)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    # The row was bound before the allowlist amendment, so it is a policy
    # version: accounted for, nothing owed, and the verdict is settled.
    assert result["state"] == "settled"
    assert result["all_bad"] == 1
    assert result["policy_version_rows"] == 1
    assert "alice.md(A)" in result["sample"]


def test_evidence_binding_resolves_a_dot_dot_path_before_the_artifacts_test(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "walls").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence = home / "walls" / "discover.md"
    evidence.write_text("wall file\n")
    # Preserve content-addressed envelope integrity while testing the lexical
    # traversal: the evidence itself resolves outside artifacts.
    digest = _write_evidenced_outcome(
        home, "alice", home / "artifacts" / ".." / "walls" / "discover.md")
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest}.json sha256={digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    # A traversal path is never eligible, so the row is measured and permanent
    # rather than repairable debt: the verdict reads settled, and the row stays
    # published in the standing audit and in the sample.
    assert result["state"] == "settled"
    assert result["remediation_bands"]["permanent"] == 1
    assert "discover.md(A)" in result["sample"]


def test_evidence_binding_flags_reused_path(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    evidence = home / "artifacts" / "shared.md"
    evidence.write_text("shared evidence\n")
    digest_a = _write_evidenced_outcome(home, "alice", evidence)
    digest_b = _write_evidenced_outcome(home, "bob", evidence)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "alice",
         f"[record] records/{digest_a}.json sha256={digest_a}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{digest_b}.json sha256={digest_b}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    # A path bound by two outcomes cannot be unbound from an append-only store,
    # so the rows are measured and permanent rather than repairable debt: the
    # verdict reads settled, and both rows stay published.
    assert result["state"] == "settled"
    assert result["remediation_bands"]["permanent"] == 2
    # Both outcomes cite one path: each violates the never-reused requirement.
    assert result["all_bad"] == 2
    assert result["latest_bad"] == 2
    assert "shared.md(D)" in result["sample"]


def test_evidence_binding_latest_window_clean_while_all_has_history(tmp_path):
    home = tmp_path / "site"
    (home / "artifacts").mkdir(parents=True)
    (home / "records").mkdir(parents=True)
    stale = home / "artifacts" / "stale.md"
    stale.write_text("original\n")
    stale_digest = _write_evidenced_outcome(home, "bob", stale)
    stale.write_text("edited after record\n")
    fresh = home / "artifacts" / "fresh.md"
    fresh.write_text("write-once\n")
    fresh_digest = _write_evidenced_outcome(home, "bob", fresh)
    _binding_tape(home, [
        (1, "2026-10-06T17:00:00Z", "bob",
         f"[record] records/{stale_digest}.json sha256={stale_digest}\n"),
        (2, "2026-10-06T17:30:00Z", "bob",
         f"[record] records/{fresh_digest}.json sha256={fresh_digest}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    # The verdict follows the standing audit: bob's latest outcome is clean,
    # so the live window is empty, but the audit keeps the amended row and the
    # ledger is damaged, so the reading says drift.
    assert result["state"] == "drift"
    assert result["all_bad"] == 1
    assert result["latest_bad"] == 0
    assert result["latest_roles"] == 1


def test_evidence_binding_missing_tape(tmp_path):
    result = discovery._ledger_evidence_binding(tmp_path / "site")
    assert result["state"] == "unknown"


def test_evidence_binding_no_post_clause_outcomes(tmp_path):
    home = tmp_path / "site"
    home.mkdir(parents=True, exist_ok=True)
    (home / "records").mkdir(parents=True)
    _binding_tape(home, [
        (1, "2026-10-06T15:00:00Z", "alice",
         f"[record] records/{'a' * 64}.json sha256={'a' * 64}\n"),
    ])
    result = discovery._ledger_evidence_binding(home)
    assert result["state"] == "unknown"


@pytest.mark.parametrize("damage", [
    "missing", "corrupt", "kind-typo", "role-typo", "leading-blank",
    "replaced-header", "wrong-envelope", "role-mismatch", "missing-reference",
])
@pytest.mark.parametrize("changed", [False, True])
def test_evidence_binding_incomplete_publication_preserves_known_drift(tmp_path, damage, changed):
    from mishe_tauftauf import wall, records
    from tests.test_wall import setup_wall
    setup_wall(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    first = artifacts / "first.txt"
    first.write_text("first evidence\n")
    wall.outcome(tmp_path, "discover", "accepted", "First contribution.", first)
    second = artifacts / "second.txt"
    second.write_text("second evidence\n")
    heading = "Wall outcome accepted by health"
    data = {"role": "health", "kind": "accepted", "text": "Second contribution.",
            "evidence": {"path": str(second), "sha256": hashlib.sha256(second.read_bytes()).hexdigest()}}
    if damage == "kind-typo":
        heading = "Wall outcome acceptd by health"
    elif damage == "role-typo":
        heading += "!"
    elif damage == "leading-blank":
        heading = "\n" + heading
    elif damage == "replaced-header":
        heading = "Damaged publication header"
    elif damage == "role-mismatch":
        data["role"] = "discover"
    if damage == "missing-reference":
        event = Feed(tmp_path).append("health", heading + "\n" + data["text"])
    else:
        event = Feed(tmp_path).append_record("health", heading + "\n" + data["text"], data,
                                            kind="event" if damage == "wrong-envelope" else "wall-outcome")
    if damage in ("missing", "corrupt"):
        digest = records.REFERENCE_RE.fullmatch(event.body.splitlines()[-1]).group(1)
        path = tmp_path / "records" / (digest + ".json")
        if damage == "missing":
            path.unlink()
        else:
            path.chmod(0o600)
            path.write_text("{broken json")
    if changed:
        # Bound evidence is made read-only at the bind boundary, so a later
        # edit is a deliberate one: the writer restores write access first.
        first.chmod(0o600)
        first.write_text("controlled evidence change\n")
    observed = discovery._ledger_evidence_binding(tmp_path)
    assert observed["state"] == ("drift" if changed else "unknown")
    assert observed["all_bad"] == int(changed)
    assert observed["bound"] == 1
    assert [row["sequence"] for row in observed["incomplete"]] == [event.sequence]
    third = artifacts / "third.txt"
    third.write_text("independent next contribution\n")
    with pytest.raises(ValueError, match="cannot derive"):
        wall.outcome(tmp_path, "senses", "accepted", "Third contribution.", third)




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


def test_renderer_coverage_dedupes_drift_rows_sharing_a_root_and_modules() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"renderer-dedupe-pin-{os.getpid()}",
                         {"wall_view": "VALUE = 1\n"})
    renderer = _renderer_root(Path("/tmp") / f"renderer-dedupe-stale-{os.getpid()}",
                              {"wall_view": "VALUE = 2\n"})
    for role in ("senses", "docs", "discover"):
        _top_pain(home, role,
                  f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value={}):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    # Three roles render from one stale root with one module set: that is one
    # violation, so nothing distinct is left unshown and no ``+Nmore`` appears.
    assert reading["state"] == "drift"
    assert reading["sample"] == "renderers=3 drift=wall_view=wall_view"
    assert len(reading["identity"]["drift"]) == 3


def test_renderer_coverage_caps_the_drift_sample_and_counts_the_rest() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    modules = ("wall_view", "seed_culture_views", "feed", "wall")
    pin = _renderer_root(Path("/tmp") / f"renderer-cap-pin-{os.getpid()}",
                         {name: "VALUE = 1\n" for name in modules})
    renderer = _renderer_root(Path("/tmp") / f"renderer-cap-stale-{os.getpid()}",
                              {name: "VALUE = 2\n" for name in modules})
    for module, role in zip(modules, ("senses", "docs", "discover", "genome")):
        _top_pain(home, role,
                  f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.{module}\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value={}):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    # The pane truncates the sample, so the drift list leads it and only two
    # distinct violations are shown; ``+Nmore`` names the rest.
    assert reading["state"] == "drift"
    assert reading["sample"].startswith("renderers=4 drift=")
    assert reading["sample"].endswith(" +2more")
    assert "script_unknown" not in reading["sample"]


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

def _site_script(home: Path, name: str, source: str) -> None:
    (home / name).write_text(source, encoding="utf-8")


def test_site_script_chain_follows_one_level_of_site_scripts(tmp_path) -> None:
    from mishe_tauftauf import discovery

    (tmp_path / "plan_state.py").write_text(
        "from mishe_tauftauf.feed import Feed\n", encoding="utf-8")
    chain = discovery._site_script_chain(
        "from pathlib import Path\n"
        "from plan_state import main\n"
        "subprocess.run([sys.executable, str(site / 'auto_intake.py')])\n", tmp_path)
    # pathlib.py and auto_intake.py do not exist in the site home: only the
    # site script that does is followed, one level deep.
    assert chain == [("plan_state.py", "feed")]


def test_renderer_coverage_reports_indirect_package_from_a_site_script() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"scriptchain-pin-{os.getpid()}",
                          {"feed": "VALUE = 1\n", "wall_view": "VALUE = 1\n"})
    _top_pain(home, "senses",
              f"export PYTHONPATH={pin}/src\nexec python -m mishe_tauftauf.wall_view\n")
    _top_pain(home, "self-development",
              "import sys\nfrom pathlib import Path\n"
              "site = Path(__file__).resolve().parents[1]\n"
              "sys.path.insert(0, str(site))\n"
              "from plan_state import main\n"
              "raise SystemExit(main())\n")
    _site_script(home, "plan_state.py", "from mishe_tauftauf.feed import Feed\n")
    pane = {"self-development": (f"env PYTHONPATH={pin}/src python -m mishe_tauftauf pain watch self-development",
                                str(home), "")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
    assert reading["state"] == "verified"
    assert "uncovered=self-development" in reading["sample"]
    assert "indirect_package=self-development:mishe_tauftauf.feed" in reading["sample"]
    assert reading["identity"]["indirect_package"] == ["self-development:mishe_tauftauf.feed"]
    assert f"{pin}:mishe_tauftauf.feed:script-chain" in reading["identity"]["renderers"]


def test_renderer_coverage_indirect_package_clears_when_the_script_is_removed() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"scriptchain-gone-{os.getpid()}",
                          {"wall_view": "VALUE = 1\n"})
    _top_pain(home, "senses",
              f"export PYTHONPATH={pin}/src\nexec python -m mishe_tauftauf.wall_view\n")
    _top_pain(home, "self-development",
              "import sys\n"
              "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
              "from plan_state import main\n"
              "raise SystemExit(main())\n")
    pane = {"self-development": (f"env PYTHONPATH={pin}/src python -m mishe_tauftauf pain watch self-development",
                                str(home), "")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
    assert reading["state"] == "verified"
    assert "indirect_package" not in reading["sample"]
    assert reading["identity"]["indirect_package"] == []


def test_renderer_coverage_flags_a_script_closure_that_differs_from_the_pin() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"scriptchain-drift-pin-{os.getpid()}",
                          {"feed": "VALUE = 1\n", "wall_view": "VALUE = 1\n"})
    renderer = _renderer_root(Path("/tmp") / f"scriptchain-drift-live-{os.getpid()}",
                              {"feed": "VALUE = 2\n", "wall_view": "VALUE = 1\n"})
    _top_pain(home, "senses",
              f"export PYTHONPATH={pin}/src\nexec python -m mishe_tauftauf.wall_view\n")
    _top_pain(home, "core-suite",
              "import subprocess, sys\n"
              "from pathlib import Path\n"
              "site = Path(__file__).resolve().parents[1]\n"
              "subprocess.run([sys.executable, str(site / 'continuity_state.py')])\n")
    _site_script(home, "continuity_state.py", "from mishe_tauftauf.feed import Feed\n")
    pane = {"core-suite": (f"env PYTHONPATH={renderer}/src python -m mishe_tauftauf pain watch core-suite",
                           str(home), "")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    assert reading["state"] == "drift"
    assert "indirect_package=core-suite:mishe_tauftauf.feed" in reading["sample"]
    assert "drift=feed=feed" in reading["sample"]
    assert reading["identity"]["drift"] == [
        {"root": str(renderer), "entry": "feed", "modules": ["feed"]}]


def test_renderer_coverage_flags_a_script_resolving_to_a_foreign_root() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"scriptchain-foreign-pin-{os.getpid()}",
                          {"feed": "VALUE = 1\n", "wall_view": "VALUE = 1\n"})
    other = _renderer_root(Path("/tmp") / f"scriptchain-foreign-live-{os.getpid()}",
                           {"feed": "VALUE = 1\n", "wall_view": "VALUE = 1\n"})
    _top_pain(home, "senses",
              f"export PYTHONPATH={pin}/src\nexec python -m mishe_tauftauf.wall_view\n")
    _top_pain(home, "self-development",
              "from pathlib import Path\n"
              "from plan_state import main\n"
              "raise SystemExit(main())\n")
    _site_script(home, "plan_state.py", "from mishe_tauftauf.feed import Feed\n")
    pane = {"self-development": (f"env PYTHONPATH={other}/src python -m mishe_tauftauf pain watch self-development",
                                str(home), "")}
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value=pane):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(other)
    # Identical bytes, but the renderer executes a root the pin does not govern.
    assert reading["state"] == "drift"
    assert reading["identity"]["drift"] == [
        {"root": str(other), "entry": "feed", "modules": ["feed"]}]


def _pane_line(session: str, pid: str, home: Path, role: str) -> str:
    return (f"{session}|{pid}|env PYTHONPATH={home}/src python -m mishe_tauftauf "
            f"--home {home} pain watch {role} --interval 5|{home}")


def test_pane_info_takes_the_pane_the_reading_home_owns() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    other = tmp_site_with_services()
    panes = [
        _pane_line("other-site", "101", other, "senses"),
        _pane_line("this-site", "102", home, "senses"),
        _pane_line("this-site", "103", home, "docs"),
        _pane_line("unknown-site", "104", home, "witness"),
    ]
    try:
        with patch("mishe_tauftauf.discovery.subprocess.run",
                   return_value=_completed("\n".join(panes))), \
                patch("mishe_tauftauf.tmux.session_owner",
                      side_effect=lambda session: {"this-site": str(home.resolve()),
                                                   "other-site": str(other.resolve())}.get(session)):
            info = discovery._pane_info(home)
        # Both sites watch `senses`, so a last-match-wins map would hand this
        # site the other site's pane and, with it, its import root. The session
        # this home owns decides the role instead.
        assert info["senses"][2] == "102"
        assert info["docs"][2] == "103"
        # A lone watcher in a session no home has claimed cannot be another
        # site's pane, so it stays this site's watcher for the role.
        assert info["witness"][2] == "104"
    finally:
        shutil.rmtree(home)
        shutil.rmtree(other)


def test_pane_info_leaves_a_role_several_sessions_watch_unresolved() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    other = tmp_site_with_services()
    panes = [
        _pane_line("other-site", "101", other, "senses"),
        _pane_line("another-site", "102", other, "senses"),
    ]
    try:
        with patch("mishe_tauftauf.discovery.subprocess.run",
                   return_value=_completed("\n".join(panes))), \
                patch("mishe_tauftauf.tmux.session_owner",
                      side_effect=lambda session: str(other.resolve())):
            info = discovery._pane_info(home)
        # Neither session belongs to this home and the role has two watchers,
        # so no reading can tell which root this site's renderer imports:
        # unresolved rather than a root this site never ran.
        assert info == {}
    finally:
        shutil.rmtree(home)
        shutil.rmtree(other)


def test_renderer_coverage_resolves_each_site_renderer_from_its_own_pane() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    other = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"twosite-pin-{os.getpid()}",
                         {"wall_view": "VALUE = 1\n"})
    stale = _renderer_root(Path("/tmp") / f"twosite-stale-{os.getpid()}",
                           {"wall_view": "VALUE = 2\n"})
    for site in (home, other):
        _top_pain(site, "senses", "exec python -m mishe_tauftauf.wall_view\n")
    watchers = {
        "this-site": _pane_line("this-site", "102", pin, "senses"),
        "other-site": _pane_line("other-site", "101", stale, "senses"),
    }
    owners = {"this-site": str(home.resolve()),
              "other-site": str(other.resolve())}
    try:
        for lines in ([watchers["other-site"], watchers["this-site"]],
                      [watchers["this-site"], watchers["other-site"]]):
            readings = []
            for site in (home, other):
                with patch("mishe_tauftauf.discovery._pinned_root",
                           return_value=(str(pin), None)), \
                        patch("mishe_tauftauf.discovery.subprocess.run",
                              return_value=_completed("\n".join(lines))), \
                        patch("mishe_tauftauf.tmux.session_owner",
                              side_effect=owners.get):
                    readings.append(discovery._renderer_coverage(site))
            here, there = readings
            # The other site's renderer really runs a stale root, and that is
            # its own drift row, not this site's. This site reads verified
            # either way round, so a pane's position in the table cannot
            # decide which bytes the reading was taken against.
            assert here["state"] == "verified"
            assert here["sample"] == "renderers=1"
            assert there["state"] == "drift"
            assert there["identity"]["drift"] == [
                {"root": str(stale), "entry": "wall_view", "modules": ["wall_view"]}]
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(stale)
        shutil.rmtree(home)
        shutil.rmtree(other)


def test_renderer_coverage_script_chain_skipped_without_a_pane() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"scriptchain-nopane-{os.getpid()}",
                          {"feed": "VALUE = 1\n", "wall_view": "VALUE = 1\n"})
    _top_pain(home, "senses",
              f"export PYTHONPATH={pin}/src\nexec python -m mishe_tauftauf.wall_view\n")
    _top_pain(home, "self-development",
              "from plan_state import main\n"
              "raise SystemExit(main())\n")
    _site_script(home, "plan_state.py", "from mishe_tauftauf.feed import Feed\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value={}):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
    assert reading["state"] == "verified"
    assert "script_unknown=self-development" not in reading["sample"]

def test_renderer_coverage_reports_drift_even_when_a_sibling_root_is_unresolved() -> None:
    from mishe_tauftauf import discovery

    home = tmp_site_with_services()
    pin = _renderer_root(Path("/tmp") / f"renderer-precedence-pin-{os.getpid()}",
                         {"feed": "VALUE = 1\n", "wall_view": "VALUE = 1\n"})
    renderer = _renderer_root(Path("/tmp") / f"renderer-precedence-stale-{os.getpid()}",
                              {"feed": "VALUE = 1\n", "wall_view": "VALUE = 2\n"})
    _top_pain(home, "senses",
              f"export PYTHONPATH={renderer}/src\nexec python -m mishe_tauftauf.wall_view\n")
    _top_pain(home, "self-development",
              "from plan_state import main\n"
              "raise SystemExit(main())\n")
    _site_script(home, "plan_state.py", "from mishe_tauftauf.feed import Feed\n")
    try:
        with patch("mishe_tauftauf.discovery._pinned_root", return_value=(str(pin), None)), \
                patch("mishe_tauftauf.discovery._pane_info", return_value={}):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(renderer)
    assert reading["state"] == "drift"
    assert "script_unknown=self-development" not in reading["sample"]
    assert reading["identity"]["drift"] == [
        {"root": str(renderer), "entry": "wall_view", "modules": ["wall_view"]}]


def test_pane_info_reads_role_names_from_start_commands() -> None:
    from mishe_tauftauf import discovery
    stdout = (
        "mishe-tauftauf|4101|env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf --home /srv/site pain watch senses --interval 5|/srv/site\n"
        "mishe-tauftauf|4102|env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf --home /srv/site pain watch health --interval 5|/srv/site\n"
        "mishe-tauftauf|4103||/srv/site\n"
        "mishe-tauftauf|4104|env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf --home /srv/site pain watch senses --interval 5|/srv/site\n"
    )
    with patch("mishe_tauftauf.discovery.subprocess.run",
               return_value=_completed(stdout)), \
            patch("mishe_tauftauf.tmux.session_owner",
                  side_effect=lambda session: "/srv/site" if session == "mishe-tauftauf" else None):
        info = discovery._pane_info(Path("/srv/site"))
    # One site watches both roles, and the session it owns decides each one, so
    # a respawned duplicate pane is the live pane without hiding the role.
    assert info == {
        "senses": ("env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf --home /srv/site pain watch senses --interval 5", "/srv/site", "4104"),
        "health": ("env PYTHONPATH=/srv/pin/src python -m mishe_tauftauf --home /srv/site pain watch health --interval 5", "/srv/site", "4102"),
    }


def test_pane_info_returns_empty_when_tmux_fails() -> None:
    from mishe_tauftauf import discovery
    with patch("mishe_tauftauf.discovery.subprocess.run", return_value=_completed("", returncode=1)):
        assert discovery._pane_info(Path("/srv/site")) == {}


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


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, timeout=15)


def _init_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    assert _git(root, "init", "--initial-branch=main").returncode == 0
    _git(root, "config", "user.email", "test@example.com")
    git_config = _git(root, "config", "user.name", "test")
    assert git_config.returncode == 0
    (root / "file.txt").write_text("one\n", encoding="utf-8")
    # The site home is plant-local, as .mishe-* is in the real checkout.
    (root / ".gitignore").write_text("site/\n", encoding="utf-8")
    _git(root, "add", "file.txt", ".gitignore")
    assert _git(root, "commit", "-m", "one").returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _pushable_origin(root: Path, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    assert _git(root, "init", "--bare", str(origin)).returncode == 0
    _git(root, "remote", "add", "origin", str(origin))
    assert _git(root, "push", "origin", "main").returncode == 0


def _dangling_commit(root: Path) -> str:
    """A commit main cannot reach, on no branch."""
    assert _git(root, "checkout", "--detach", "main").returncode == 0
    (root / "side.txt").write_text("side\n", encoding="utf-8")
    _git(root, "add", "side.txt")
    assert _git(root, "commit", "-m", "side").returncode == 0
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    assert _git(root, "checkout", "main").returncode == 0
    return sha


def _inventory_home(root: Path) -> Path:
    home = root / "site"
    home.mkdir(parents=True, exist_ok=True)
    return home


def test_branch_inventory_verified_when_main_is_the_sole_branch(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _pushable_origin(root, tmp_path)
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    assert reading["state"] == "verified"
    assert reading["sample"] == ("local=1 extra=none remote=1 extra_remote=none "
                                 "worktrees=1 nonmain=0 dirty=0 absent=0 strays=none "
                                 "nonmain_names=none dirty_names=none "
                                 "absent_names=none")


def test_branch_inventory_names_an_absent_worktree_instead_of_reading_unknown(tmp_path):
    """A registered worktree whose directory is gone is named, not swallowed.

    The live reading sat UNKNOWN with "worktree status unreadable:
    /tmp/disc-head" for a peer experiment's worktree that no longer exists.
    One dead path made the whole inventory opaque: the branch regime, the
    dirty bytes and the strays were all hidden behind it. The absence is a
    fact about the inventory and belongs in the reading, while a worktree
    that is present and still unreadable stays UNKNOWN.
    """
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _pushable_origin(root, tmp_path)
    gone = tmp_path / "gone-worktree"
    added = _git(root, "worktree", "add", "--detach", str(gone), "main")
    assert added.returncode == 0
    shutil.rmtree(gone)

    reading = discovery._repo_branch_inventory(_inventory_home(root))
    assert reading["state"] != "unknown"
    assert reading["absent"] == [str(gone)]
    assert "absent=1" in reading["sample"]
    assert str(gone) in reading["sample"]


def test_branch_inventory_drift_on_an_extra_local_branch(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _git(root, "branch", "revert-check")
    _pushable_origin(root, tmp_path)
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    assert reading["state"] == "drift"
    assert reading["extra"] == ["revert-check"]
    assert "extra=revert-check" in reading["sample"]


def test_branch_inventory_drift_on_an_extra_remote_ref(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _pushable_origin(root, tmp_path)
    assert _git(root, "push", "origin", "main:refs/heads/feature-x").returncode == 0
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    assert reading["state"] == "drift"
    assert reading["extra_remote"] == ["feature-x"]
    assert "extra_remote=feature-x" in reading["sample"]


def test_branch_inventory_drift_on_a_release_worktree_at_a_non_main_commit(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    side = _dangling_commit(root)
    _pushable_origin(root, tmp_path)
    release = root / "site" / "releases" / side[:7]
    release.parent.mkdir(parents=True, exist_ok=True)
    assert _git(root, "worktree", "add", "--detach", str(release), side).returncode == 0
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    # Releases are pinned from pushed main SHAs: a non-main commit there is a
    # delivery anomaly.
    assert reading["state"] == "drift"
    assert reading["nonmain"] == 1
    assert reading["nonmain_names"] == [f"releases/{side[:7]}"]
    assert reading["drift"] == [f"releases/{side[:7]}"]


def test_branch_inventory_reports_a_sanctioned_nonmain_worktree(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    side = _dangling_commit(root)
    _pushable_origin(root, tmp_path)
    sanctioned = root / "site" / "worktrees" / "cgroup-current"
    sanctioned.parent.mkdir(parents=True, exist_ok=True)
    assert _git(root, "worktree", "add", "--detach", str(sanctioned), side).returncode == 0
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    # The preservation area holds unique commits by design: reported, not drift.
    assert reading["state"] == "verified"
    assert reading["nonmain"] == 1
    assert reading["nonmain_names"] == ["worktrees/cgroup-current"]
    assert reading["drift"] == []


def _release(home: Path, sha: str, *, worktree: bool = True, age_days: float = 0.0) -> Path:
    """Create one ``releases/<sha>`` entry; a git worktree carries a ``.git`` file."""
    path = home / "releases" / sha
    path.mkdir(parents=True, exist_ok=True)
    if worktree:
        (path / ".git").write_text("gitdir: /nonexistent\n", encoding="utf-8")
    stamp = datetime.now(timezone.utc).timestamp() - age_days * 86400.0
    os.utime(path, (stamp, stamp))
    return path


def _bounded_bytes(monkeypatch) -> None:
    monkeypatch.setattr(discovery, "_release_tree_bytes", lambda root: 1024)


def test_release_retention_absent_releases_reads_unknown(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    _bounded_bytes(monkeypatch)
    monkeypatch.setattr(discovery, "_referenced_release_names", lambda: set())
    reading = discovery._repo_release_retention(home)
    assert reading["state"] == "unknown"
    assert reading["sample"] == "releases directory absent"


def test_release_retention_bounded_keeps_read_verified(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    live = "a" * 40
    _release(home, live, age_days=30)
    for i in range(4):
        _release(home, f"{i:040x}", age_days=30 - i)
    monkeypatch.setattr(discovery, "_referenced_release_names", lambda: {live})
    _bounded_bytes(monkeypatch)
    reading = discovery._repo_release_retention(home)
    assert reading["state"] == "verified"
    assert reading["worktrees"] == 5
    assert reading["live"] == 1
    assert reading["reclaimable"] == 4
    assert reading["kept"] == 4
    assert reading["over"] == 0


def test_release_retention_over_keep_reads_drift(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    live = "b" * 40
    _release(home, live, age_days=30)
    for i in range(20):
        _release(home, f"{i + 1:040x}", age_days=30 - i)
    monkeypatch.setattr(discovery, "_referenced_release_names", lambda: {live})
    _bounded_bytes(monkeypatch)
    reading = discovery._repo_release_retention(home)
    assert reading["state"] == "drift"
    assert reading["reclaimable"] == 20
    assert reading["over"] == 20 - discovery._RELEASE_RETENTION_KEEP
    assert "beyond keep" in reading["sample"]


def test_release_retention_byte_ceiling_reads_drift(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    live = "c" * 40
    _release(home, live, age_days=30)
    for i in range(2):
        _release(home, f"{i + 1:040x}", age_days=30 - i)
    monkeypatch.setattr(discovery, "_referenced_release_names", lambda: {live})
    monkeypatch.setattr(discovery, "_release_tree_bytes", lambda root: 3 * 2**30)
    reading = discovery._repo_release_retention(home)
    assert reading["state"] == "drift"
    assert reading["bytes"] == 3 * 2**30
    assert "GiB exceeds" in reading["sample"]


def test_release_retention_legacy_copy_is_not_a_worktree(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    live = "d" * 40
    _release(home, live, age_days=30)
    legacy = home / "releases" / "0a95df6f"
    legacy.mkdir(parents=True, exist_ok=True)
    os.utime(legacy, (datetime.now(timezone.utc).timestamp() - 30 * 86400.0,) * 2)
    monkeypatch.setattr(discovery, "_referenced_release_names", lambda: {live})
    _bounded_bytes(monkeypatch)
    reading = discovery._repo_release_retention(home)
    assert reading["legacy"] == 1
    assert reading["worktrees"] == 1
    assert reading["reclaimable"] == 0


def test_release_retention_reads_a_configured_unit_file_reference(tmp_path):
    from mishe_tauftauf import discovery

    unit_root = tmp_path / "units"
    unit_root.mkdir()
    sha = "e" * 40
    (unit_root / "mishe-x.service").write_text(
        f"Environment=PYTHONPATH=/home/u/.mishe-tauftauf/releases/{sha}/src\n",
        encoding="utf-8")
    assert sha in discovery._referenced_release_names(unit_root)


def test_release_retention_reports_oldest_age_and_recent_creations(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    live = "f" * 40
    _release(home, live, age_days=30)
    _release(home, f"{1:040x}", age_days=12)
    _release(home, f"{2:040x}", age_days=1)
    monkeypatch.setattr(discovery, "_referenced_release_names", lambda: {live})
    _bounded_bytes(monkeypatch)
    reading = discovery._repo_release_retention(home)
    assert reading["oldest_days"] > 11
    assert reading["created_recent"] == 1
    assert "created7d=1" in reading["sample"]


def test_branch_inventory_drift_on_an_external_nonmain_worktree(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    side = _dangling_commit(root)
    _pushable_origin(root, tmp_path)
    external = root / "external" / "draft"
    external.parent.mkdir(parents=True, exist_ok=True)
    assert _git(root, "worktree", "add", "--detach", str(external), side).returncode == 0
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    # Unique commits belong on main or in the preservation area.
    assert reading["state"] == "drift"
    assert reading["drift"] == [str(external)]


def test_branch_inventory_reports_dirty_worktree_bytes(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _pushable_origin(root, tmp_path)
    draft = root / "site" / "worktrees" / "draft"
    draft.parent.mkdir(parents=True, exist_ok=True)
    assert _git(root, "worktree", "add", "--detach", str(draft)).returncode == 0
    (draft / "draft.txt").write_text("uncommitted\n", encoding="utf-8")
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    assert reading["state"] == "verified"
    assert reading["dirty"] == 1
    assert reading["dirty_names"] == ["worktrees/draft"]


def test_branch_inventory_names_untracked_strays_in_the_shared_checkout(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _pushable_origin(root, tmp_path)
    (root / "notes").mkdir()
    (root / "notes" / "senses-1-wall.md").write_text("scratch\n", encoding="utf-8")
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    # A local note written against a repo-root-relative path is not ignored, so
    # a broad `git add` would commit it. `dirty` alone names only the worktree;
    # the reader must see which bytes, or the hazard stays anonymous.
    assert reading["strays"] == ["notes/"]
    assert "strays=notes/" in reading["sample"]
    # The pane truncates the sample at 120 chars, so the hazard must land before
    # the long nonmain/dirty name lists or the operator never sees it.
    assert reading["sample"].index("strays=") < 120
    assert reading["dirty"] == 1
    assert reading["state"] == "verified"


def test_branch_inventory_strays_exclude_ignored_and_tracked_entries(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _pushable_origin(root, tmp_path)
    (root / "site" / "notes").mkdir(parents=True)
    (root / "site" / "notes" / "kept.md").write_text("kept\n", encoding="utf-8")
    (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    assert _git(root, "commit", "-m", "tracked").returncode == 0
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    # The ignored site home and a committed file are not strays; the field must
    # not flag the site's own notes as checkout debris.
    assert reading["strays"] == []
    assert "strays=none" in reading["sample"]


def test_branch_inventory_stray_names_are_not_c_quoted(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _pushable_origin(root, tmp_path)
    (root / "my notes").mkdir()
    (root / "my notes" / "x.md").write_text("scratch\n", encoding="utf-8")
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    # git C-quotes a path with a space unless `-z` is passed, so without the
    # flag the reader would see an escaped string instead of the real path.
    assert reading["strays"] == ["my notes/"]
    assert '"my notes/"' not in reading["sample"]


def test_branch_inventory_unknown_when_not_a_git_repo(tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    reading = discovery._repo_branch_inventory(home)
    assert reading["state"] == "unknown"
    assert reading["sample"] == "not a git repo"


def test_branch_inventory_unknown_when_the_remote_is_unreadable(tmp_path):
    from mishe_tauftauf import discovery

    root = tmp_path / "checkout"
    _init_repo(root)
    _git(root, "remote", "add", "origin", str(tmp_path / "missing-origin.git"))
    reading = discovery._repo_branch_inventory(_inventory_home(root))
    assert reading["state"] == "unknown"
    assert reading["sample"] == "remote heads unreadable"


def _ghost_tree(root: Path, name: str, files: dict[str, str], mtime: float) -> None:
    ghost = root / name
    ghost.mkdir(parents=True, exist_ok=True)
    for relative, text in files.items():
        path = ghost / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (mtime, mtime))


def _ordered_checkout_scan(checkout: Path, ordered: list[SimpleNamespace]):
    """A ``scandir`` factory yielding ``ordered`` entries at the checkout root.

    A bound on inspected names is only meaningfully tested when the order of
    the checkout's own entries is fixed: the real directory yields them in
    filesystem order, so whether a reserved root sits before or behind an
    ordinary prefix is left to chance. Everything outside the checkout scan
    keeps the real directory, so only one reading path is shaped.
    """
    seen: list[str] = []
    real_scandir = os.scandir

    class OrderedScan:
        def __init__(self, entries):
            self._entries = iter(entries)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            item = next(self._entries)
            seen.append(item.name)
            return item

    def ordered_scandir(path):
        if os.fspath(path) == str(checkout):
            return OrderedScan(ordered)
        return real_scandir(path)

    return ordered_scandir, seen


def test_ghost_site_homes_verified_when_only_the_site_home_exists(tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / ".mishe-plant"
    home.mkdir(parents=True)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "verified"
    assert reading["sample"] == ("ghost-homes=0 files=0 holding=none nested=0 "
                                 "newest=none")
    assert reading["ghosts"] == []


def test_ghost_site_homes_drift_names_a_reserved_home_that_is_not_this_site(tmp_path):
    from mishe_tauftauf import discovery

    # The live shape: the site home is itself reserved-named, so the reader must
    # resolve paths rather than skip every `.mishe-*` entry by name.
    home = tmp_path / ".mishe-tauftauf"
    home.mkdir(parents=True)
    _ghost_tree(tmp_path, ".mishe-tauftauft", {"walls/senses.md": "ghost wall\n"}, 1_000.0)
    reading = discovery._ghost_site_homes(home, now=1_000.0 + 7_200)
    assert reading["state"] == "drift"
    assert reading["ghosts"] == [".mishe-tauftauft"]
    assert reading["holding"] == [".mishe-tauftauft"]
    assert reading["files"] == {".mishe-tauftauft": 1}
    assert reading["total"] == 1
    assert reading["nested"] == []
    assert reading["newest"][".mishe-tauftauft"]["age_s"] == 7_200
    assert "ghost-homes=1 files=1 holding=.mishe-tauftauft no-process=.mishe-tauftauft nested=0" in reading["sample"]
    assert "newest=.mishe-tauftauft age=2h0m" in reading["sample"]
    assert reading["files_at_least"] is False


def test_ghost_site_homes_no_process_excludes_a_ghost_a_running_process_names(monkeypatch, tmp_path):
    """A ghost a running process names drops out of the ``no-process`` list.

    The reference scan is process-only, so its miss is labelled ``no-process``,
    never ``stale``: a configured-but-not-running unit stays UNKNOWN. A hit must
    not be reported under that miss label.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / ".mishe-tauftauf"
    home.mkdir(parents=True)
    _ghost_tree(tmp_path, ".mishe-tauftauft", {"walls/senses.md": "ghost wall\n"}, 1_000.0)

    monkeypatch.setattr(discovery, "_home_referenced_by_process",
                        lambda path: Path(path).name == ".mishe-tauftauft")
    reading = discovery._ghost_site_homes(home)
    assert reading["ghosts"] == [".mishe-tauftauft"]
    assert reading["no_process"] == []
    assert "no-process=" not in reading["sample"]


def test_ghost_site_homes_counts_nested_ghost_directories(tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    _ghost_tree(tmp_path, ".mishe-tauftauft", {
        "walls/discover.md": "snapshot\n",
        ".mishe-tauftauf/research/note.md": "nested\n",
    }, 2_000.0)
    reading = discovery._ghost_site_homes(home, now=2_000.0 + 60)
    assert reading["state"] == "drift"
    assert reading["total"] == 2
    assert reading["nested"] == [".mishe-tauftauft/.mishe-tauftauf"]
    assert "nested=1" in reading["sample"]


def test_ghost_site_homes_reports_empty_ghost_directories_without_files(tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    (tmp_path / ".mishe-tauftauftau").mkdir()
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "drift"
    assert reading["ghosts"] == [".mishe-tauftauftau"]
    assert reading["empty"] == [".mishe-tauftauftau"]
    assert reading["holding"] == []
    assert reading["total"] == 0
    assert "holding=none empty=.mishe-tauftauftau" in reading["sample"]


def test_ghost_site_homes_dates_an_empty_ghost_root_by_its_own_mtime(tmp_path):
    """An empty ghost has no file to age, so the root's own time dates the stray.

    A reserved-root directory holding nothing is still a live typo: with no
    file to age, the reading had no temporal validity for the empty class, so a
    fresh stray could not be told from an ancient leftover.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    ghost = tmp_path / ".mishe-tauftaft"
    ghost.mkdir()
    os.utime(ghost, (1_000.0, 1_000.0))
    reading = discovery._ghost_site_homes(home, now=1_000.0 + 3_600)
    assert reading["state"] == "drift"
    assert reading["empty"] == [".mishe-tauftaft"]
    assert reading["total"] == 0
    assert reading["newest"][".mishe-tauftaft"]["age_s"] == 3_600
    assert "empty=.mishe-tauftaft" in reading["sample"]
    assert "newest=.mishe-tauftaft age=1h0m" in reading["sample"]


def test_ghost_site_homes_caps_the_enumeration_to_a_lower_bound(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_FILE_LIMIT", 2)
    _ghost_tree(tmp_path, ".mishe-tauftauft", {
        "a.md": "1\n", "b.md": "2\n", "c.md": "3\n"}, 3_000.0)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "drift"
    assert reading["files_at_least"] is True
    assert reading["files"] == {".mishe-tauftauft": 2}
    assert "files>=2" in reading["sample"]


def test_ghost_site_homes_caps_tree_count_and_marks_the_lower_bound(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_ROOT_LIMIT", 2)
    for index in range(6):
        _ghost_tree(tmp_path, f".mishe-ghost{index}", {"walls/a.md": "x\n"}, 1_000.0)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "drift"
    assert len(reading["ghosts"]) == 2  # identity holds at most the examined trees
    assert reading["total"] == 2  # no later tree was walked
    assert reading["roots_at_least"] is True
    assert reading["work_truncated"] is False
    assert "ghost-homes=2+" in reading["sample"]
    assert "files=2" in reading["sample"]


def test_ghost_site_homes_bounds_work_on_a_directory_only_tree(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_WORK_LIMIT", 8)
    tree = tmp_path / ".mishe-deep"
    # A tree of directories and no files: unbounded names, zero counted files.
    cursor = tree
    for level in range(30):
        cursor = cursor / f"level{level:02d}"
        cursor.mkdir(parents=True)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "drift"
    assert reading["work_truncated"] is True
    assert reading["roots_at_least"] is True
    assert reading["total"] == 0
    assert "names-truncated" in reading["sample"]
    assert "files>=0" in reading["sample"]  # a partial count still never reads clean
    assert "empty=.mishe-deep" in reading["sample"]  # the walked tree stays listed


def test_ghost_site_homes_caps_nested_identity_and_marks_the_lower_bound(tmp_path, monkeypatch):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_IDENTITY_LIMIT", 2)
    tree = tmp_path / ".mishe-tauftauft"
    tree.mkdir(parents=True)
    for index in range(5):
        (tree / f".mishe-lane{index}").mkdir()
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "drift"
    assert len(reading["nested"]) == 2  # identity is capped, not full
    assert reading["nested_truncated"] is True
    assert "nested=2+" in reading["sample"]
    assert reading["files_at_least"] is False
    assert "files=0" in reading["sample"]


def test_ghost_site_homes_stays_exact_under_every_cap(tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    _ghost_tree(tmp_path, ".mishe-tauftauft", {
        "walls/a.md": "1\n", ".mishe-other/b.md": "2\n"}, 4_000.0)
    reading = discovery._ghost_site_homes(home, now=4_000.0 + 60)
    assert reading["roots_at_least"] is False
    assert reading["nested_truncated"] is False
    assert reading["work_truncated"] is False
    assert reading["sample"].endswith("newest=.mishe-tauftauft age=1m0s")
    assert reading["nested"] == [".mishe-tauftauft/.mishe-other"]


def test_ghost_site_homes_unknown_when_a_ghost_tree_cannot_be_walked(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    (tmp_path / ".mishe-tauftauft").mkdir()

    real_scandir = os.scandir

    def refusing_scan(path):
        if Path(path).name == ".mishe-tauftauft":
            raise PermissionError(13, "Permission denied", "")
        return real_scandir(path)

    monkeypatch.setattr(discovery.os, "scandir", refusing_scan)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "unknown"
    assert "ghost home unreadable: .mishe-tauftauft" in reading["sample"]
    assert "count" not in reading


def test_ghost_site_homes_names_a_reserved_root_symlink_without_traversing_it(tmp_path):
    """A reserved root symlink is checkout-scope evidence, never a traversal.

    ``Path.is_dir()`` follows symlinks, so a reserved root pointing at a
    directory outside the checkout used to be walked: its names, file counts
    and newest-file ages were reported under a checkout name. The root name
    itself is in scope, so it is recorded as a ghost and its file count is
    left unmeasured rather than read as a clean zero.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / ".mishe-plant"
    home.mkdir(parents=True)
    # An external target reachable only by crossing the symlink: nothing under
    # it may be counted, named or dated by a checkout reading.
    outside = tmp_path.parent / f"ghost-outside-{tmp_path.name}"
    outside.mkdir()
    try:
        (outside / "external-file.md").write_text("external\n", encoding="utf-8")
        (outside / "external-dir").mkdir()
        os.symlink(outside, tmp_path / ".mishe-tauftauft")
        reading = discovery._ghost_site_homes(home)
    finally:
        shutil.rmtree(outside, ignore_errors=True)

    assert reading["state"] == "drift"
    assert reading["ghosts"] == [".mishe-tauftauft"]
    assert reading["symlinked"] == [".mishe-tauftauft"]
    assert reading["symlinked_truncated"] is False
    assert reading["files"] == {} and reading["total"] == 0
    assert reading["holding"] == [] and reading["empty"] == []
    assert reading["newest"] == {}
    assert "external-file" not in reading["sample"]
    assert "external-dir" not in reading["sample"]
    # One unmeasured root makes the file count a lower bound, not an exact 0.
    assert reading["files_at_least"] is True
    assert ("ghost-homes=1 files>=0 holding=none no-process=.mishe-tauftauft nested=0 symlink=.mishe-tauftauft"
            in reading["sample"])


def test_ghost_site_homes_counts_one_tree_once_through_two_names(tmp_path):
    """An in-checkout alias to another ghost tree is not a second tree.

    Following the alias would walk the same tree twice and report its files
    under both names, so the aliased root is recorded and never walked.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    _ghost_tree(tmp_path, ".mishe-tauftauft", {"walls/senses.md": "ghost\n"}, 6_000.0)
    os.symlink(tmp_path / ".mishe-tauftauft", tmp_path / ".mishe-tauftauftau")
    reading = discovery._ghost_site_homes(home, now=6_000.0 + 60)
    assert reading["state"] == "drift"
    assert reading["ghosts"] == [".mishe-tauftauft", ".mishe-tauftauftau"]
    assert reading["symlinked"] == [".mishe-tauftauftau"]
    assert reading["files"] == {".mishe-tauftauft": 1}
    assert reading["total"] == 1
    assert reading["files_at_least"] is True  # the alias's own count is unmeasured


def test_ghost_site_homes_skips_a_reserved_root_symlink_to_this_site_home(tmp_path):
    """An alias to this site's own home is this home, not a ghost.

    The reserved name resolves to the site home, so the reading keeps its
    clean zero instead of naming its own home as a second site directory.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / ".mishe-tauftauf"
    home.mkdir(parents=True)
    (home / "walls").mkdir()
    os.symlink(home, tmp_path / ".mishe-tauftauft")
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "verified"
    assert reading["ghosts"] == []
    assert reading["symlinked"] == []
    assert reading["sample"] == ("ghost-homes=0 files=0 holding=none nested=0 "
                                "newest=none")


def test_ghost_site_homes_caps_reserved_root_symlink_identity(tmp_path, monkeypatch):
    """Symlink names are retained under the shared identity budget.

    Every other identity class counts past its cap and marks the lower bound;
    symlinked roots must not lose that marker just because they are not walked.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    outside = tmp_path.parent / f"ghost-outside-{tmp_path.name}"
    outside.mkdir()
    try:
        monkeypatch.setattr(discovery, "GHOST_IDENTITY_LIMIT", 2)
        for index in range(5):
            os.symlink(outside, tmp_path / f".mishe-ghost{index}")
        reading = discovery._ghost_site_homes(home)
    finally:
        shutil.rmtree(outside, ignore_errors=True)

    assert reading["state"] == "drift"
    assert len(reading["symlinked"]) == 2
    assert reading["symlinked_truncated"] is True
    assert "symlink=.mishe-ghost0,.mishe-ghost1+" in reading["sample"]
    assert reading["files"] == {}


def test_ghost_site_homes_refuses_to_walk_a_symlinked_nested_directory(tmp_path):
    """Nested directory symlinks stay outside the file count and the age.

    A symlinked directory inside a walked tree resolves outside the checkout.
    Descending it was already refused; it also is no longer counted as a file
    or stat'ed, so an external object's mtime cannot become this reading's
    newest-file age.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    _ghost_tree(tmp_path, ".mishe-tauftauft", {"walls/keep.md": "ghost\n"}, 7_000.0)
    outside = tmp_path.parent / f"ghost-outside-{tmp_path.name}"
    outside.mkdir()
    try:
        (outside / "external-file.md").write_text("external\n", encoding="utf-8")
        # A future external mtime would read as a negative age if it leaked.
        os.utime(outside, (9_000.0, 9_000.0))
        os.symlink(outside, tmp_path / ".mishe-tauftauft" / "linked-out")
        reading = discovery._ghost_site_homes(home, now=7_000.0 + 60)
    finally:
        shutil.rmtree(outside, ignore_errors=True)

    assert reading["state"] == "drift"
    assert reading["files"] == {".mishe-tauftauft": 1}
    assert reading["total"] == 1
    assert reading["newest"][".mishe-tauftauft"]["age_s"] == 60
    assert reading["symlinked"] == []
    assert "external-file" not in reading["sample"]
    assert reading["files_at_least"] is False


def test_ghost_site_homes_carries_one_name_budget_across_every_tree(tmp_path, monkeypatch):
    """Aggregate name work is one scan-wide budget, not one per ghost tree.

    Per-tree budgets let a checkout with many modest ghost trees spend
    limit * trees names while each tree stays far under its own bound, so the
    scan-wide reading advertises a bound it never enforces.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_WORK_LIMIT", 10)
    for index in range(6):
        _ghost_tree(tmp_path, f".mishe-lane{index}", {
            f"d{level:02d}/keep.md": "x\n" for level in range(3)}, 1_000.0)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "drift"
    assert reading["work_truncated"] is True
    assert reading["roots_at_least"] is True
    assert "names-truncated" in reading["sample"]
    # No tree is walked to completion once the shared budget runs out, so the
    # file count is a lower bound rather than an exact total.
    assert reading["total"] < 6 * 6
    assert reading["files_at_least"] is True


def test_ghost_site_homes_stops_enumerating_the_checkout_at_the_root_cap(tmp_path, monkeypatch):
    """Checkout enumeration stops at the root cap instead of reading every entry.

    Reading every directory entry before applying the root cap leaves the
    scan unbounded in the checkout's own size; the bound must stop the
    enumeration itself and mark the count as a lower bound. The cap counts
    reserved roots: ordinary entries around them are covered by the separate
    total-name budget, so this bound must not be asserted over every name the
    checkout happens to yield.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_ROOT_LIMIT", 2)
    for index in range(6):
        _ghost_tree(tmp_path, f".mishe-ghost{index}", {"walls/a.md": "x\n"}, 1_000.0)
    # Fixed order: an ordinary name, three reserved roots, an ordinary name,
    # then the remaining reserved roots. The root cap counts reserved roots
    # only, so the scan stops on the first reserved root past the cap no
    # matter how many ordinary entries surround the reserved run.
    ordered = [SimpleNamespace(name="ordinary0", path=str(tmp_path / "ordinary0"))]
    ordered.extend(
        SimpleNamespace(name=f".mishe-ghost{index}",
                        path=str(tmp_path / f".mishe-ghost{index}"))
        for index in range(3)
    )
    ordered.append(SimpleNamespace(name="ordinary1", path=str(tmp_path / "ordinary1")))
    ordered.extend(
        SimpleNamespace(name=f".mishe-ghost{index}",
                        path=str(tmp_path / f".mishe-ghost{index}"))
        for index in range(3, 6)
    )
    ordered_scandir, seen = _ordered_checkout_scan(tmp_path, ordered)
    monkeypatch.setattr(discovery.os, "scandir", ordered_scandir)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "drift"
    assert reading["ghosts"] == [".mishe-ghost0", ".mishe-ghost1"]
    assert reading["roots_at_least"] is True
    assert "ghost-homes=2+" in reading["sample"]
    # Enumeration stopped once the cap was exceeded instead of reading every
    # directory entry: exactly one reserved root past the cap was inspected,
    # and the entries behind the stop point were never reached.
    reserved_seen = [name for name in seen if name.startswith(".mishe-")]
    assert len(reserved_seen) == discovery.GHOST_ROOT_LIMIT + 1
    assert "ordinary1" not in seen
    assert ".mishe-ghost3" not in seen


def test_ghost_site_homes_unknown_when_the_checkout_cannot_be_enumerated(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)

    class RefusingScan:
        def __enter__(self):
            raise PermissionError(13, "Permission denied", "")

        def __exit__(self, *exc):
            return False

    def refusing_scandir(path):
        return RefusingScan()

    monkeypatch.setattr(discovery.os, "scandir", refusing_scandir)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "unknown"
    assert "checkout root unreadable" in reading["sample"]
    assert "count" not in reading


def test_ghost_site_homes_caps_total_checkout_names_inspected(tmp_path, monkeypatch):
    """Total checkout-root names inspected are bounded, reserved or not.

    Charging only reserved names leaves the scan unbounded in the checkout's
    own size: arbitrarily many ordinary entries before the reserved ones are
    still inspected, and the reading never marks the truncation. The budget
    must cover every inspected name and stop the enumeration itself.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_SCAN_LIMIT", 8)
    for index in range(8):
        (tmp_path / f"ordinary{index}").write_text("x\n", encoding="utf-8")
    for index in range(10):
        _ghost_tree(tmp_path, f".mishe-ghost{index}", {"walls/a.md": "x\n"}, 1_000.0)
    seen: list[str] = []
    real_scandir = os.scandir

    class CountingScan:
        def __init__(self, entries, is_checkout):
            self._entries = iter(entries)
            self._is_checkout = is_checkout

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            item = next(self._entries)
            if self._is_checkout:
                seen.append(item.name)
            return item

    def counting_scandir(path):
        return CountingScan(real_scandir(path), os.fspath(path) == str(tmp_path))

    monkeypatch.setattr(discovery.os, "scandir", counting_scandir)
    reading = discovery._ghost_site_homes(home)
    # The scan stopped one name past the budget instead of walking every
    # checkout-root entry, whatever their order and reservation class.
    assert len(seen) == discovery.GHOST_SCAN_LIMIT + 1
    # Nine of the nineteen checkout-root entries are ordinary, so eight
    # inspected names reach a ghost before the budget runs out: presence is
    # established and the retained count stays an explicit lower bound.
    assert reading["state"] == "drift"
    assert reading["roots_at_least"] is True
    assert reading["checkout_truncated"] is True
    assert "checkout-truncated" in reading["sample"]


def test_ghost_site_homes_unknown_when_truncation_leaves_absence_unestablished(tmp_path, monkeypatch):
    """A truncated enumeration that examined no ghost tree reads UNKNOWN.

    Reading a clean zero from a partially inspected checkout is exactly the
    false-negative class this detector exists to make visible, so absence is
    claimed only from an enumeration that finished within its budget.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_SCAN_LIMIT", 8)
    for index in range(20):
        (tmp_path / f"ordinary{index}").write_text("x\n", encoding="utf-8")
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "unknown"
    assert "truncated" in reading["sample"]
    assert "ghost absence not established" in reading["sample"]
    assert "count" not in reading


def test_ghost_site_homes_stops_the_checkout_scan_at_the_budget_before_any_reserved_root(
        tmp_path, monkeypatch):
    """The budget bounds inspected names, not reserved roots the scan hopes to find.

    Charging only reserved roots makes the bound depend on where a ghost
    name happens to sit: an ordinary prefix longer than the budget exhausts
    the scan before any reserved entry is reached, so the claimed bound holds
    only for checkouts whose ghost roots come first. The enumeration itself
    has to stop at the budget and leave absence unestablished.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_SCAN_LIMIT", 8)
    ghost = tmp_path / ".mishe-tauftauft"
    ghost.mkdir(parents=True)
    # The ghost tree sits behind every ordinary name, so a budget charged per
    # reserved root would walk the whole prefix and still claim its bound.
    ordered = [SimpleNamespace(name=f"ordinary{index:03d}",
                               path=str(tmp_path / f"ordinary{index:03d}"))
               for index in range(600)]
    ordered.append(SimpleNamespace(name=".mishe-tauftauft", path=str(ghost)))
    ordered_scandir, seen = _ordered_checkout_scan(tmp_path, ordered)
    monkeypatch.setattr(discovery.os, "scandir", ordered_scandir)
    reading = discovery._ghost_site_homes(home)
    # The inspection stopped one name past the budget: the reserved root
    # behind the prefix was never inspected, so no ghost is reported.
    assert len(seen) == discovery.GHOST_SCAN_LIMIT + 1
    assert all(name.startswith("ordinary") for name in seen)
    assert reading["state"] == "unknown"
    assert "checkout enumeration truncated" in reading["sample"]
    assert "ghost absence not established" in reading["sample"]
    assert "count" not in reading


def test_ghost_site_homes_reaches_a_reserved_root_behind_an_ordinary_prefix(
        tmp_path, monkeypatch):
    """A bounded scan that does reach a ghost reads drift, not a clean count.

    The same budget, the same stop point, a different order: with the reserved
    root inside the inspected window the scan walks it and reports the count
    as an explicit lower bound, so the reading neither hides the ghost nor
    claims a complete count it never made.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_SCAN_LIMIT", 8)
    ghost = tmp_path / ".mishe-tauftauft"
    _ghost_tree(tmp_path, ".mishe-tauftauft", {"walls/mind.md": "ghost wall\n"}, 1_000.0)
    ordered = [SimpleNamespace(name=f"ordinary{index}",
                               path=str(tmp_path / f"ordinary{index}"))
               for index in range(4)]
    ordered.append(SimpleNamespace(name=".mishe-tauftauft", path=str(ghost)))
    ordered.extend(SimpleNamespace(name=f"later{index}",
                                   path=str(tmp_path / f"later{index}"))
                   for index in range(600))
    ordered_scandir, seen = _ordered_checkout_scan(tmp_path, ordered)
    monkeypatch.setattr(discovery.os, "scandir", ordered_scandir)
    reading = discovery._ghost_site_homes(home, now=1_000.0 + 60)
    # Four ordinary names, the ghost root and four more ordinary names: the
    # ninth inspected name is where the scan stops, past the ghost.
    assert len(seen) == discovery.GHOST_SCAN_LIMIT + 1
    assert seen[-1] == "later3"
    assert reading["state"] == "drift"
    assert reading["ghosts"] == [".mishe-tauftauft"]
    assert reading["files"] == {".mishe-tauftauft": 1}
    assert reading["roots_at_least"] is True
    assert reading["checkout_truncated"] is True
    assert "ghost-homes=1+" in reading["sample"]
    assert "checkout-truncated" in reading["sample"]
    assert "newest=.mishe-tauftauft age=1m0s" in reading["sample"]


def test_ghost_site_homes_charges_the_work_budget_per_yielded_entry(tmp_path, monkeypatch):
    """The tree budget is charged per yielded entry, not per materialized list.

    A walk that builds a directory's full name list before charging the budget
    lets one high-fanout directory exceed the advertised work bound during
    materialization; the scan must stop requesting entries once the shared
    name budget runs out, so total entries inspected stays within it.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    monkeypatch.setattr(discovery, "GHOST_WORK_LIMIT", 10)
    ghost = tmp_path / ".mishe-ghost"
    (ghost / "many").mkdir(parents=True)
    for index in range(600):
        (ghost / "many" / f"entry{index:04d}").mkdir()
    seen: list[str] = []
    real_scandir = os.scandir

    class CountingScan:
        def __init__(self, entries):
            self._entries = iter(entries)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            item = next(self._entries)
            seen.append(item.name)
            return item

    def counting_scandir(path):
        return CountingScan(real_scandir(path))

    monkeypatch.setattr(discovery.os, "scandir", counting_scandir)
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "drift"
    assert reading["work_truncated"] is True
    assert reading["files_at_least"] is True
    assert "names-truncated" in reading["sample"]
    # Every entry inspected anywhere in the scan charged the shared budget, so
    # the enumeration itself — not a later truncation of a materialized list —
    # is what stopped it: of the 600 names in the high-fanout directory, only
    # the budget plus the entry that revealed the truncation were yielded.
    assert sum(1 for name in seen if name.startswith("entry")) <= discovery.GHOST_WORK_LIMIT + 1


def test_ghost_site_homes_ignores_non_reserved_entries(tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / ".gitignore").write_text(".mishe-*\n", encoding="utf-8")
    (tmp_path / ".mesh").mkdir()
    (tmp_path / "loose.md").write_text("note\n", encoding="utf-8")
    reading = discovery._ghost_site_homes(home)
    assert reading["state"] == "verified"
    assert reading["ghosts"] == []


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
                patch("mishe_tauftauf.proc_table.read_pane_pythonpath", return_value=[f"{env_root}/src"]):
            reading = discovery._renderer_coverage(home)
    finally:
        shutil.rmtree(pin)
        shutil.rmtree(env_root)
        shutil.rmtree(cmd_root)
    assert reading["state"] == "verified"
    assert reading["identity"]["drift"] == []


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
            patch.object(discovery, "_unit_declared_paths", return_value=None), \
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

def test_drift_across_sites_keeps_its_reason_inside_the_pane_clip(
        tmp_path: Path) -> None:
    # The pane shortens a long sample at its tail, so a reading whose reason
    # trails the informational host-scope list renders as an UNKNOWN the reader
    # cannot explain: observed live 2026-10-16T11:05Z, the served row read
    # "host-scope=mishe-cleaner-recovered.service,...-watch.service…" with the
    # `foreign=` coupling clipped off.
    pinned_sha: list[str] = []
    other_pin = tmp_path / "releases" / "other"
    _release_tree(other_pin, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    host_units = [f"mishe-host-tool-{index}.service" for index in range(6)]
    foreign_unit = "mishe-seed-session-b0f90360ee89420ca40c3e7bfc16fd06.service"
    checkout = tmp_path.resolve()
    with patch.object(discovery, "_active_site_units",
                      return_value=(host_units + [foreign_unit], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={foreign_unit: str(checkout / "src")}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.object(discovery, "_unit_declared_paths",
                         return_value=["/usr/bin/python3", "/home/host/tool"]), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert row["identity"]["host_scope"] == sorted(host_units)
    assert row["identity"]["foreign"] == {foreign_unit: str(checkout / "src")}
    _, line = seed_culture_views._observation_reading(row, "recent")
    # The unit alone: the same field also carries the tmp_path-derived checkout
    # name, which a longer generated basetemp could push past the clip budget.
    assert f"foreign={foreign_unit}@" in line

def test_drift_across_sites_names_a_standalone_script_a_host_tool(
        tmp_path: Path) -> None:
    # A unit that runs a stdlib-only script from a site-home path imports no
    # site package, so it cannot drift against a pin through imports: the
    # unit is a host tool, not an uninspectable site consumer.
    own_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    _release_tree(own_pin, own_sha)
    home = tmp_path / "plant"
    _pin(home, own_pin, own_sha[0], "mishe-self")
    _linked_registry(home, [{"home": str(tmp_path / "other"),
                             "session": "mishe-other", "sha": own_sha[0]}])
    script = home / "bin" / "standalone"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env python3\nimport json\n", encoding="utf-8")
    unit = "mishe-ifm-shim.service"
    execstart = f"{{ path={script} ; argv[]={script} }}\n"
    completed = subprocess.CompletedProcess(["systemctl"], 0, execstart, "")
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_unit_declared_paths",
                         return_value=[str(script)]), \
            patch.object(discovery.subprocess, "run", return_value=completed), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "verified"
    assert row["identity"]["host_scope"] == [unit]
    assert row["identity"]["uninspectable"] == []


def test_drift_across_sites_keeps_a_site_importing_script_uninspectable(
        tmp_path: Path) -> None:
    # A unit whose executed script imports the site package may run code the
    # pin contradicts; with no PYTHONPATH root the sensor cannot judge it,
    # so the unit stays uninspectable rather than being cleared as a host
    # tool.
    own_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    _release_tree(own_pin, own_sha)
    home = tmp_path / "plant"
    _pin(home, own_pin, own_sha[0], "mishe-self")
    _linked_registry(home, [{"home": str(tmp_path / "other"),
                             "session": "mishe-other", "sha": own_sha[0]}])
    script = home / "bin" / "consumer.py"
    script.parent.mkdir(parents=True)
    script.write_text("from mishe_tauftauf import discovery\n", encoding="utf-8")
    unit = "mishe-ifm-shim.service"
    execstart = f"{{ path={script} ; argv[]={script} }}\n"
    completed = subprocess.CompletedProcess(["systemctl"], 0, execstart, "")
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_unit_declared_paths",
                         return_value=[str(script)]), \
            patch.object(discovery.subprocess, "run", return_value=completed), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert row["identity"]["uninspectable"] == [unit]
    assert row["identity"]["host_scope"] == []


def test_foreign_checkout_consumers_skips_unit_with_description_naming_root(
        tmp_path: Path) -> None:
    # A plant-level service whose description names the checkout root is local,
    # not foreign, even when its script text references the checkout.
    checkout = tmp_path.resolve()
    local_unit = "mishe-cleaner-recovered.service"
    foreign_unit = "mishe-unlisted.service"
    with patch.object(discovery, "_unit_description",
                      side_effect=lambda u: f"{checkout.name} report-only cleaner coordinator"
                      if u == local_unit else ""), \
            patch.object(discovery, "_unit_names_checkout", return_value=True), \
            patch.object(discovery, "_script_spawns_site_package", return_value=False):
        consumers = discovery._foreign_checkout_consumers(
            [local_unit, foreign_unit], checkout)
    assert local_unit not in consumers
    assert consumers[foreign_unit] == str(checkout)

def test_foreign_checkout_consumers_reports_a_wrapper_that_spawns_the_package(
        tmp_path: Path) -> None:
    # A wrapper that spawns `-m <package>` carries the coupling itself, so the
    # unit is a foreign consumer even when systemd's description names the
    # checkout root: observed live 2026-10-16T20:5xZ on mesh-host, where the
    # cleaner unit's description names the checkout and its wrapper spawns
    # `python3 -m mishe_tauftauf` children every 15 s, so the reading no
    # longer flaps between foreign and a false host-scope/verified.
    checkout = tmp_path.resolve()
    unit = "mishe-cleaner-recovered.service"
    with patch.object(discovery, "_unit_description",
                      return_value=f"{checkout.name} report-only cleaner coordinator"), \
            patch.object(discovery, "_unit_names_checkout", return_value=False), \
            patch.object(discovery, "_script_spawns_site_package", return_value=True):
        consumers = discovery._foreign_checkout_consumers([unit], checkout)
    assert consumers == {unit: str(checkout)}


def test_script_spawns_site_package_reads_a_wrapper_argv_list(tmp_path: Path) -> None:
    # The wrapper's own form: subprocess.run([sys.executable, "-m",
    # "mishe_tauftauf", ...]) declares the spawn in an argv list.
    script = tmp_path / "wrapper.py"
    script.write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-m', 'mishe_tauftauf', '--home', 'x'], env={})\n",
        encoding="utf-8")
    unit = "mishe-wrapper.service"
    execstart = f"{{ path={script} ; argv[]={script} follow }}\n"
    completed = subprocess.CompletedProcess(["systemctl"], 0, execstart, "")
    with patch.object(discovery.subprocess, "run", return_value=completed):
        assert discovery._script_spawns_site_package(unit, "mishe_tauftauf") is True


def test_script_spawns_site_package_reads_a_shell_wrapper_command(tmp_path: Path) -> None:
    # A non-Python wrapper declares the same spawn as command text.
    script = tmp_path / "wrapper.sh"
    script.write_text("#!/bin/sh\nexec python3 -m mishe_tauftauf --home /x\n",
                      encoding="utf-8")
    unit = "mishe-wrapper.service"
    execstart = f"{{ path={script} ; argv[]={script} follow }}\n"
    completed = subprocess.CompletedProcess(["systemctl"], 0, execstart, "")
    with patch.object(discovery.subprocess, "run", return_value=completed):
        assert discovery._script_spawns_site_package(unit, "mishe_tauftauf") is True


def test_script_spawns_site_package_false_for_a_stdlib_only_script(
        tmp_path: Path) -> None:
    script = tmp_path / "standalone.py"
    script.write_text("import json\nprint(json.dumps({}))\n", encoding="utf-8")
    unit = "mishe-wrapper.service"
    execstart = f"{{ path={script} ; argv[]={script} follow }}\n"
    completed = subprocess.CompletedProcess(["systemctl"], 0, execstart, "")
    with patch.object(discovery.subprocess, "run", return_value=completed):
        assert discovery._script_spawns_site_package(unit, "mishe_tauftauf") is False


def test_script_spawns_site_package_none_when_no_script_is_readable(
        tmp_path: Path) -> None:
    unit = "mishe-missing.service"
    execstart = "{ path=/nonexistent/wrapper.py ; argv[]=/nonexistent/wrapper.py }\n"
    completed = subprocess.CompletedProcess(["systemctl"], 0, execstart, "")
    with patch.object(discovery.subprocess, "run", return_value=completed):
        assert discovery._script_spawns_site_package(unit, "mishe_tauftauf") is None


def test_drift_across_sites_names_a_wrapper_spawning_the_package(
        tmp_path: Path) -> None:
    # A wrapper unit whose script spawns `-m <package>` is a foreign consumer
    # even with no live child at scan time: the static classifier and the live
    # import-root path then agree, so the sample cannot flip to a false
    # host-scope/verified on the 15 s child cycle (measured live 63 s, 12/12
    # samples unknown+foreign across live-root and no-root instants).
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
    script = tmp_path.parent / "wrapper.py"
    script.write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-m', 'mishe_tauftauf', '--home', 'x'], env={})\n",
        encoding="utf-8")
    execstart = f"{{ path={script} ; argv[]={script} follow }}\n"
    completed = subprocess.CompletedProcess(["systemctl"], 0, execstart, "")
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery.subprocess, "run", return_value=completed), \
            patch.dict(os.environ, {"MESHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert f"foreign={unit}@{checkout.name}" in row["sample"]
    assert row["identity"]["foreign"] == {unit: str(checkout)}
    assert row["identity"]["host_scope"] == []
    assert row["identity"]["unattributed"] == []


def test_drift_across_sites_keeps_a_mapped_wrapper_spawning_uninspectable(
        tmp_path: Path) -> None:
    # A site-mapped unit with no live root whose wrapper spawns the package is
    # structurally coupled, so it must not be cleared as a host tool: the
    # root it spawns with cannot be read statically, and verified would be a
    # false verdict.
    own_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    _release_tree(own_pin, own_sha)
    home = tmp_path / "plant"
    _pin(home, own_pin, own_sha[0], "mishe-self")
    _linked_registry(home, [{"home": str(tmp_path / "other"),
                             "session": "mishe-other", "sha": own_sha[0]}])
    script = home / "bin" / "wrapper.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-m', 'mishe_tauftauf', '--home', 'x'], env={})\n",
        encoding="utf-8")
    unit = "mishe-ifm-shim.service"
    execstart = f"{{ path={script} ; argv[]={script} 127.0.0.1:8377 }}\n"
    completed = subprocess.CompletedProcess(["systemctl"], 0, execstart, "")
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_is_tmux_session_holder", return_value=False), \
            patch.object(discovery, "_script_imports_site_package", return_value=False), \
            patch.object(discovery, "_script_spawns_site_package", return_value=True), \
            patch.object(discovery.subprocess, "run", return_value=completed), \
            patch.object(discovery, "_plant_session", return_value="mishe-self"):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert row["identity"]["uninspectable"] == [unit]
    assert row["identity"]["host_scope"] == []


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


def test_drift_across_sites_names_an_unattributed_unit_no_site_names(
        tmp_path: Path) -> None:
    # A host tool beside the sites settles the reading's question: nothing it
    # imports and nothing its declaration names lies inside a site, so it is
    # named host-scope instead of leaving the reading UNKNOWN.
    pinned_sha: list[str] = []
    other_pin = tmp_path / "releases" / "other"
    _release_tree(other_pin, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-cleaner-recovered.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.object(discovery, "_unit_declared_paths",
                         return_value=["/usr/bin/python3",
                                       "/home/host/scripts/mesh-cleaner-run",
                                       "/home/host/work"]), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "verified"
    assert f"host-scope={unit}" in row["sample"]
    assert row["identity"]["host_scope"] == [unit]
    assert row["identity"]["unattributed"] == []


def test_drift_across_sites_keeps_a_site_coupled_unit_unattributed(
        tmp_path: Path) -> None:
    # A declared path inside a site's own home is a coupling this sensor cannot
    # wave away, so the unit stays an anonymous gap rather than host scope.
    pinned_sha: list[str] = []
    other_pin = tmp_path / "releases" / "other"
    _release_tree(other_pin, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-unlisted.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.object(discovery, "_unit_declared_paths",
                         return_value=["/usr/bin/python3",
                                       str(other / "bin" / "tool")]), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert "host-scope" not in row["sample"]
    assert row["identity"]["unattributed"] == [unit]
    assert row["identity"]["host_scope"] == []


def test_drift_across_sites_keeps_an_unreadable_unit_unattributed(
        tmp_path: Path) -> None:
    # A unit whose declaration cannot be read is unjudgeable, which is not the
    # same as a unit declaring no site path; the gap stays visible.
    pinned_sha: list[str] = []
    other_pin = tmp_path / "releases" / "other"
    _release_tree(other_pin, pinned_sha)
    other = tmp_path / "site-other"
    _pin(other, other_pin, pinned_sha[0], "mishe-other")
    home = tmp_path / "plant"
    _linked_registry(home, [{"home": str(other), "session": "mishe-other",
                             "sha": pinned_sha[0]}])
    unit = "mishe-unlisted.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.object(discovery, "_unit_declared_paths", return_value=None), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "unknown"
    assert row["identity"]["unattributed"] == [unit]
    assert row["identity"]["host_scope"] == []

def test_drift_across_sites_maps_same_site_service_to_scanning_site(
        tmp_path: Path) -> None:
    # A same-site service (e.g. mishe-ifm-shim.service) starts with
    # SITE_UNIT_PREFIX but doesn't match any session prefix. It should be
    # mapped to the scanning site's session and compared against its pin,
    # not classified as foreign.
    own_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    _release_tree(own_pin, own_sha)
    home = tmp_path / "plant"
    _pin(home, own_pin, own_sha[0], "mishe-self")
    _linked_registry(home, [{"home": str(tmp_path / "other"),
                             "session": "mishe-other", "sha": own_sha[0]}])
    unit = "mishe-ifm-shim.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots",
                         return_value={unit: str(own_pin)}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.object(discovery, "_unit_declared_paths",
                         return_value=[str(home / ".mishe-tauftauf" / "bin" / "ifm-shim")]), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "verified"
    assert row["identity"]["foreign"] == {}
    assert row["identity"]["stale"] == {}
    assert row["identity"]["unattributed"] == []


def test_drift_across_sites_mapped_unit_no_import_root_no_site_path_is_host_scope(
        tmp_path: Path) -> None:
    # A mapped unit with no import root that doesn't declare any site path
    # is a host tool beside the sites, not an uninspectable gap.
    own_sha: list[str] = []
    own_pin = tmp_path / "releases" / "own"
    _release_tree(own_pin, own_sha)
    home = tmp_path / "plant"
    _pin(home, own_pin, own_sha[0], "mishe-self")
    _linked_registry(home, [{"home": str(tmp_path / "other"),
                             "session": "mishe-other", "sha": own_sha[0]}])
    unit = "mishe-self-host-tool.service"
    with patch.object(discovery, "_active_site_units", return_value=([unit], None)), \
            patch.object(discovery, "_unit_import_roots", return_value={}), \
            patch.object(discovery, "_foreign_checkout_consumers", return_value={}), \
            patch.object(discovery, "_unit_declared_paths",
                         return_value=["/usr/bin/python3", "/home/host/tool"]), \
            patch.dict(os.environ, {"MISHE_SEED_SESSION": "mishe-self"}):
        row = discovery._runtime_drift_across_sites(home)
    assert row["state"] == "verified"
    assert f"host-scope={unit}" in row["sample"]
    assert row["identity"]["host_scope"] == [unit]
    assert row["identity"]["uninspectable"] == []


def test_unit_declared_paths_reads_the_effective_unit_text() -> None:
    # systemd renders ExecStart, WorkingDirectory and Environment in one show;
    # every absolute path is collected, including the colon-separated entries
    # an Environment value names.
    text = ("ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
            "/home/host/scripts/tool follow ; ignore_errors=no }\n"
            "WorkingDirectory=/home/host/work\n"
            "Environment=PATH=/usr/bin:/bin MESH_HOME=/home/host/state\n")
    with patch.object(discovery.subprocess, "run",
                      return_value=SimpleNamespace(returncode=0, stdout=text)):
        paths = discovery._unit_declared_paths("mishe-cleaner-recovered.service")
    assert paths == ["/usr/bin/python3", "/home/host/scripts/tool",
                     "/home/host/work", "/usr/bin", "/bin",
                     "/home/host/state"]


def test_unit_declared_paths_is_none_when_the_unit_cannot_be_read() -> None:
    with patch.object(discovery.subprocess, "run",
                      return_value=SimpleNamespace(returncode=1, stdout="")):
        assert discovery._unit_declared_paths("mishe-unlisted.service") is None
    with patch.object(discovery.subprocess, "run", side_effect=OSError()):
        assert discovery._unit_declared_paths("mishe-unlisted.service") is None


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


def _record_commit(subject="inference-research wake 20213: record c9575b6 CI success"):
    return {"subject": subject,
            "files": ["research/inference-continuity/progress.md"]}


def _impl_commit():
    return {"subject": "body-research wake 20016: cover receiver framing "
                       "with behavioral self-tests",
            "files": ["research/portable-body/conformance-check-20261010.py"]}


def test_treadmill_positive_control_is_record_only_chain():
    commits = [_record_commit() for _ in range(7)]
    fold = discovery._treadmill_fold(commits)
    assert fold == {"stagnation": 7, "change": 0}


def test_treadmill_negative_control_impl_breaks_stagnation():
    commits = [_impl_commit()] + [_record_commit() for _ in range(4)]
    fold = discovery._treadmill_fold(commits)
    assert fold == {"stagnation": 0, "change": 1}


def test_treadmill_short_run_below_threshold():
    fold = discovery._treadmill_fold([_record_commit() for _ in range(3)])
    assert fold == {"stagnation": 3, "change": 0}


def test_treadmill_impl_inside_window_counts_change():
    commits = [_record_commit() for _ in range(4)] + [_impl_commit()]
    fold = discovery._treadmill_fold(commits)
    assert fold == {"stagnation": 4, "change": 1}


def test_treadmill_source_change_class():
    assert discovery._treadmill_class(
        ["src/mishe_tauftauf/discovery.py"], "senses wake: add sense") == "source-change"


def test_treadmill_other_change_class():
    assert discovery._treadmill_class(
        ["research/notes.md"], "some notes") == "other-change"


def test_treadmill_non_record_single_record_file_is_other_change():
    assert discovery._treadmill_class(
        ["research/inference-continuity/progress.md"],
        "inference-research wake 1: notes") == "other-change"


def test_treadmill_ci_concluded_single_record_file_is_record_only():
    assert discovery._treadmill_class(
        ["research/inference-continuity/progress.md"],
        "inference-research wake 33421: CI 38375665350 concluded success") == "record-only"


def test_treadmill_ci_concluded_failure_single_record_file_is_record_only():
    assert discovery._treadmill_class(
        ["research/inference-continuity/progress.md"],
        "inference-research wake 23088: CI afdcce8 concluded failure (operator-window class)") == "record-only"


def test_treadmill_ci_without_concluded_is_other_change():
    assert discovery._treadmill_class(
        ["research/inference-continuity/progress.md"],
        "inference-research wake 1: CI watch pending") == "other-change"


def _docs_refresh_commit(subject="docs wake 46638: refresh Redmi consumer status"):
    return {"subject": subject,
            "files": ["docs/mesh.md"]}


def test_docs_treadmill_positive_control_is_docs_only_chain():
    commits = [_docs_refresh_commit() for _ in range(7)]
    fold = discovery._docs_treadmill_fold(commits)
    assert fold == {"stagnation": 7, "change": 0}


def test_docs_treadmill_negative_control_impl_breaks_stagnation():
    commits = [_docs_refresh_commit() for _ in range(4)] + [_impl_commit()]
    fold = discovery._docs_treadmill_fold(commits)
    assert fold == {"stagnation": 4, "change": 1}


def test_docs_treadmill_short_run_below_threshold():
    fold = discovery._docs_treadmill_fold([_docs_refresh_commit() for _ in range(3)])
    assert fold == {"stagnation": 3, "change": 0}


def test_docs_treadmill_mixed_docs_src_commit_is_implementation():
    assert discovery._docs_treadmill_class(
        ["docs/mesh.md", "src/mishe_tauftauf/seed.py"]) == "implementation"


def test_docs_treadmill_research_only_commit_is_other():
    assert discovery._docs_treadmill_class(
        ["research/notes.md"]) == "other"


def test_docs_treadmill_empty_file_list_is_other():
    assert discovery._docs_treadmill_class([]) == "other"


def test_docs_treadmill_rollup_unknown_when_not_a_checkout(tmp_path):
    home = tmp_path / "site"
    (home / "handoffs").mkdir(parents=True)
    reading = discovery._mind_docs_treadmill(home)
    assert reading["id"] == "sense.mind.docs-treadmill"
    assert reading["state"] == "unknown"


def test_docs_treadmill_rollup_positive_and_clean(monkeypatch, tmp_path):
    """The docs lane's rollup wiring names a firing role and a clean one.

    The pure fold is covered above; this covers the part it cannot: the
    rollup must turn a >=5 docs-only leading run with zero change into
    ``watch-docs-treadmill`` and leave a role with an implementation commit
    in the run ``clean``. Without it, a wiring defect could emit a
    ``verified`` rollup that routes nothing while the fold tests still pass.
    """
    commits = {"docs": [_docs_refresh_commit() for _ in range(6)],
               "health": [_impl_commit()]}

    class Head:
        returncode = 0
        stdout = "c3c6194\n"

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Head())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60: list(commits.get(role, [])))
    result = discovery._mind_docs_treadmill(tmp_path)
    assert result["id"] == "sense.mind.docs-treadmill"
    assert result["state"] == "verified"
    assert result["verdict"] == "watch-docs-treadmill"
    assert result["roles"]["docs"]["verdict"] == "watch-docs-treadmill"
    assert result["roles"]["docs"]["stagnation"] == 6
    assert result["roles"]["health"]["verdict"] == "clean"
    assert result["validity"]["min_wakes"] == 5

def _wake_entry(sequence, role, observation):
    from mishe_tauftauf.feed import FeedEntry
    return FeedEntry(
        sequence=sequence,
        timestamp=f"2026-10-15T13:{sequence:02d}:00Z",
        source="seed",
        body=f"seed wake {role} observation={observation} event={sequence}")


class _MockFeed:
    def __init__(self, entries):
        self._entries = entries
    def entries(self):
        return self._entries


def test_artifact_recurrence_positive_same_observation_no_commits(monkeypatch, tmp_path):
    """Three wakes sharing one observation with no new commits fires."""
    entries = [_wake_entry(i, "docs", 43288) for i in range(1, 6)]
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60, since=None: [])
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert result["id"] == "sense.mind.artifact-recurrence"
    assert result["state"] == "verified"
    assert result["verdict"] == "watch-artifact-recurrence"
    assert result["roles"]["docs"]["verdict"] == "watch-artifact-recurrence"
    assert result["roles"]["docs"]["resolver"] == "docs"
    assert result["resolver"] == "docs"
    assert result["roles"]["docs"]["detail"] == (
        "last 3 wakes share observation 43288 with no new commits since their interval")


def test_artifact_recurrence_negative_control_new_commits(monkeypatch, tmp_path):
    """Same observation but new commits means legitimate progress — clean."""
    entries = [_wake_entry(i, "docs", 43288) for i in range(1, 6)]
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60, since=None: [{"subject": "docs wake 1", "files": ["docs/mesh.md"]}])
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert result["verdict"] == "clean"
    assert result["roles"]["docs"]["verdict"] == "clean"
    assert result["roles"]["docs"]["resolver"] is None
    assert result["resolver"] is None


def test_artifact_recurrence_negative_control_different_observations(monkeypatch, tmp_path):
    """Different observations mean the frontier moved — clean."""
    entries = [_wake_entry(i, "docs", 43288 + i) for i in range(1, 6)]
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60, since=None: [])
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert result["verdict"] == "clean"
    assert result["roles"]["docs"]["verdict"] == "clean"


def test_artifact_recurrence_short_run_below_threshold(monkeypatch, tmp_path):
    """Fewer than 3 wakes is not enough evidence — clean."""
    entries = [_wake_entry(i, "docs", 43288) for i in range(1, 3)]
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60, since=None: [])
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert result["verdict"] == "clean"
    assert result["roles"]["docs"]["verdict"] == "clean"


def test_artifact_recurrence_unknown_when_not_a_checkout(tmp_path):
    """Not a git checkout stays UNKNOWN."""
    home = tmp_path / "site"
    (home / "handoffs").mkdir(parents=True)
    result = discovery._mind_artifact_recurrence(home)
    assert result["id"] == "sense.mind.artifact-recurrence"
    assert result["state"] == "unknown"
    assert result["sample"] == "not a git checkout"


def test_artifact_recurrence_unknown_when_feed_unreadable(monkeypatch, tmp_path):
    """Unreadable feed stays UNKNOWN."""
    class BadFeed:
        def entries(self):
            raise OSError("no feed")
    monkeypatch.setattr(discovery, "Feed", lambda home: BadFeed())
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert result["state"] == "unknown"
    assert result["sample"] == "feed unreadable"


def test_artifact_recurrence_rollup_wiring(monkeypatch, tmp_path):
    """The rollup names a firing role and leaves a progressing role clean."""
    entries = ([_wake_entry(i, "docs", 43288) for i in range(1, 6)]
               + [_wake_entry(i, "health", 100 + i) for i in range(10, 15)])
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60, since=None: [])
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert result["state"] == "verified"
    assert result["verdict"] == "watch-artifact-recurrence"
    assert result["roles"]["docs"]["verdict"] == "watch-artifact-recurrence"
    assert result["roles"]["health"]["verdict"] == "clean"
    assert result["validity"]["min_wakes"] == 3

def test_artifact_recurrence_tail_slice_mixed_history(monkeypatch, tmp_path):
    """A head-distinct/tail-same history fires; head-only slicing misses it.

    The three oldest wakes carry distinct observations and the three newest
    share one. Slicing the tape's head reads "observations vary" and misses
    the recurrence; slicing its tail reads the run. The unique count and
    the detail are tail-based for the same reason.
    """
    entries = ([_wake_entry(i, "docs", 1000 + i) for i in range(1, 4)]
               + [_wake_entry(i, "docs", 43288) for i in range(4, 7)])
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60, since=None: [])
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert result["verdict"] == "watch-artifact-recurrence"
    assert result["roles"]["docs"]["verdict"] == "watch-artifact-recurrence"
    assert result["roles"]["docs"]["detail"] == (
        "last 3 wakes share observation 43288 with no new commits since their interval")
    # The last five wakes are 1002,1003,43288,43288,43288 -> three unique; a
    # head-based count over 1001,1002,1003,43288,43288 would read four.
    assert result["roles"]["docs"]["unique_observations"] == 3
    assert result["resolver"] == "docs"
    assert result["roles"]["docs"]["resolver"] == "docs"


def test_artifact_recurrence_commit_inside_interval_clears(monkeypatch, tmp_path):
    """A commit inside the recent wakes' interval is progress — clean."""
    entries = [_wake_entry(i, "docs", 43288) for i in range(1, 6)]
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    seen = {}

    def fake_commits(checkout, role, limit=60, since=None):
        seen["since"] = since
        return ([{"subject": "docs wake 1", "files": ["docs/mesh.md"]}]
                if since is not None else [])

    monkeypatch.setattr(discovery, "_treadmill_commits", fake_commits)
    result = discovery._mind_artifact_recurrence(tmp_path)
    # The interval starts at the oldest of the last three wakes (13:03Z).
    assert seen["since"] == datetime(2026, 10, 15, 13, 3, tzinfo=timezone.utc)
    assert result["verdict"] == "clean"
    assert result["roles"]["docs"]["verdict"] == "clean"


def test_artifact_recurrence_commit_before_interval_still_fires(monkeypatch, tmp_path):
    """A commit older than the recent wakes' interval is stale, not progress.

    This is the failure the unbounded window hid: the whole 60-commit window
    held a commit, so a same-observation tail read clean even though the mind
    made no commit inside the interval it keeps re-waking in.
    """
    entries = [_wake_entry(i, "docs", 43288) for i in range(1, 6)]
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    seen = {}

    def fake_commits(checkout, role, limit=60, since=None):
        seen["since"] = since
        return ([] if since is not None
                else [{"subject": "docs wake 1", "files": ["docs/mesh.md"]}])

    monkeypatch.setattr(discovery, "_treadmill_commits", fake_commits)
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert seen["since"] is not None
    assert result["verdict"] == "watch-artifact-recurrence"
    assert result["resolver"] == "docs"


def _unstamped_wake_entry(sequence, role, observation):
    """A wake entry whose timestamp is absent — an unboundable interval."""
    from mishe_tauftauf.feed import FeedEntry
    return FeedEntry(
        sequence=sequence,
        timestamp="",
        source="seed",
        body=f"seed wake {role} observation={observation} event={sequence}")


def test_artifact_recurrence_unreadable_commits_is_unknown(monkeypatch, tmp_path):
    """A None commit read is inconclusive: the role stays with verdict unknown.

    Path A (research-methods DM 57036): dropping the role on a None read left
    the top-level verdict clean, so an unreadable git stream read as a clean
    bill for the role that was recurring.
    """
    entries = [_wake_entry(i, "docs", 43288) for i in range(1, 6)]
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60, since=None: None)
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert result["verdict"] == "unknown"
    assert result["resolver"] == "docs"
    assert result["roles"]["docs"]["verdict"] == "unknown"
    assert result["roles"]["docs"]["detail"] == "commit stream unreadable"
    assert result["roles"]["docs"]["resolver"] == "docs"
    assert "unreadable=1" in result["sample"]


def test_artifact_recurrence_unboundable_interval_is_unknown(monkeypatch, tmp_path):
    """All-None wake stamps cannot bound the interval: unknown, not clean.

    Path B (research-methods DM 57036): falling back to a since=None read made
    the whole window the interval, so a commit predating every wake cleared the
    role. The detector must not make that read at all.
    """
    entries = [_unstamped_wake_entry(i, "docs", 43288) for i in range(1, 6)]
    monkeypatch.setattr(discovery, "Feed", lambda home: _MockFeed(entries))
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "abc123\n"})())
    calls = []

    def fake_commits(checkout, role, limit=60, since=None):
        calls.append(since)
        return [{"subject": "stale", "files": ["docs/mesh.md"]}]

    monkeypatch.setattr(discovery, "_treadmill_commits", fake_commits)
    result = discovery._mind_artifact_recurrence(tmp_path)
    assert calls == []
    assert result["verdict"] == "unknown"
    assert result["roles"]["docs"]["verdict"] == "unknown"
    assert result["roles"]["docs"]["detail"] == "wake interval unboundable: no wake stamp"
    assert result["roles"]["docs"]["resolver"] == "docs"
    assert result["resolver"] == "docs"
    assert "unboundable=1" in result["sample"]


def test_treadmill_unreadable_stream_keeps_role_in_the_rollup(monkeypatch, tmp_path):
    """The main treadmill lane keeps an unreadable role visible as unknown."""
    class Head:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Head())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60: (None if role == "docs" else []))
    result = discovery._mind_treadmill(tmp_path)
    assert result["roles"]["docs"]["verdict"] == "unknown"
    assert result["roles"]["docs"]["detail"] == "commit stream unreadable"
    assert result["verdict"] == "unknown"
    assert "unreadable=1" in result["sample"]


def test_docs_treadmill_unreadable_stream_keeps_role_in_the_rollup(monkeypatch, tmp_path):
    """The docs lane keeps an unreadable role visible as unknown."""
    class Head:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Head())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60: (None if role == "docs" else []))
    result = discovery._mind_docs_treadmill(tmp_path)
    assert result["roles"]["docs"]["verdict"] == "unknown"
    assert result["roles"]["docs"]["detail"] == "commit stream unreadable"
    assert result["verdict"] == "unknown"
    assert "unreadable=1" in result["sample"]


def _treadmill_home_with_handoff(tmp_path, text="wake 1 notes"):
    home = tmp_path / "site"
    (home / "handoffs").mkdir(parents=True, exist_ok=True)
    (home / "handoffs" / "senses.md").write_text(text, encoding="utf-8")
    return home


def test_treadmill_handoff_digest_is_stable_and_absent_is_none(tmp_path):
    home = _treadmill_home_with_handoff(tmp_path)
    assert discovery._treadmill_handoff_digest(home, "senses") == \
        discovery._treadmill_handoff_digest(home, "senses")
    assert discovery._treadmill_handoff_digest(home, "genome") is None


def test_treadmill_handoff_stagnant_when_bytes_identical(monkeypatch, tmp_path):
    import json

    home = _treadmill_home_with_handoff(tmp_path, "same words")
    digest = discovery._treadmill_handoff_digest(home, "senses")
    (home / "discovery").mkdir(exist_ok=True)
    previous = {"observations": [{"id": "sense.mind.treadmill", "kind": "read",
                                  "state": "verified", "sample": "treadmill=clean",
                                  "roles": {"senses": {"handoff": digest}}}]}
    (home / "discovery" / "latest.json").write_text(json.dumps(previous), encoding="utf-8")

    class Head:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Head())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60: [])
    result = discovery._mind_treadmill(home)
    assert result["roles"]["senses"]["handoff_moved"] == "stagnant"
    assert "handoff_stagnant=senses" in result["sample"]


def test_treadmill_handoff_changed_clears_nothing_but_names_movement(monkeypatch, tmp_path):
    import json

    home = _treadmill_home_with_handoff(tmp_path, "new words this wake")
    (home / "discovery").mkdir(exist_ok=True)
    previous = {"observations": [{"id": "sense.mind.treadmill", "kind": "read",
                                  "state": "verified", "sample": "treadmill=clean",
                                  "roles": {"senses": {"handoff": "deadbeef0000"}}}]}
    (home / "discovery" / "latest.json").write_text(json.dumps(previous), encoding="utf-8")

    def record_commit():
        return {"subject": "inference-research wake 1: CI 1 concluded success",
                "files": ["research/inference-continuity/progress.md"]}

    class Head:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Head())
    monkeypatch.setattr(
        discovery, "_treadmill_commits",
        lambda checkout, role, limit=60:
        ([record_commit() for _ in range(6)] if role == "inference-research" else []))
    result = discovery._mind_treadmill(home)
    assert result["roles"]["inference-research"]["verdict"] == "watch-treadmill"
    assert result["roles"]["senses"]["handoff_moved"] == "changed"
    assert "handoff_stagnant" not in result["sample"]


def test_treadmill_handoff_unknown_without_previous_receipt(monkeypatch, tmp_path):
    home = _treadmill_home_with_handoff(tmp_path)

    class Head:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Head())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60: [])
    result = discovery._mind_treadmill(home)
    assert result["roles"]["senses"]["handoff_moved"] is None

def test_treadmill_sample_renders_pending_and_idle(monkeypatch, tmp_path):
    home = tmp_path / "site"
    home.mkdir()

    class Head:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Head())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60: [])
    monkeypatch.setattr(discovery, "_mind_role_coverage",
                        lambda home, role, now, entries: {
                            "wall_updated": None, "last_yield": None,
                            "idle_s": 7892, "pending": 61469})
    result = discovery._mind_treadmill(home)
    assert "pending=61469" in result["sample"]
    assert "idle=7892" in result["sample"]



def test_treadmill_git_log_entries_groups_subjects_and_files():
    from mishe_tauftauf import proc_table
    raw = ("SUBJECT:inference-research wake 1: record abc CI success\n"
           "\n"
           "research/inference-continuity/progress.md\n"
           "\n"
           "SUBJECT:body-research wake 2: framing\n"
           "\n"
           "research/portable-body/conformance-check-20261010.py\n")
    assert proc_table.git_log_entries(raw) == [
        {"subject": "inference-research wake 1: record abc CI success",
         "files": ["research/inference-continuity/progress.md"]},
        {"subject": "body-research wake 2: framing",
         "files": ["research/portable-body/conformance-check-20261010.py"]},
    ]


def test_treadmill_git_failure_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery.subprocess, "run",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("no git")))
    assert discovery._treadmill_commits(tmp_path, "senses") is None


def test_treadmill_mind_reading_names_pairs_and_head(monkeypatch, tmp_path):
    commits = {"inference-research": [_record_commit() for _ in range(6)],
               "body-research": [_impl_commit()]}

    class Head:
        returncode = 0
        stdout = "c3c6194\n"

    monkeypatch.setattr(discovery.subprocess, "run",
                        lambda *a, **kw: Head())
    monkeypatch.setattr(discovery, "_treadmill_commits",
                        lambda checkout, role, limit=60: list(commits.get(role, [])))
    result = discovery._mind_treadmill(tmp_path)
    assert result["id"] == "sense.mind.treadmill"
    assert result["state"] == "verified"
    assert result["verdict"] == "watch-treadmill"
    assert result["roles"]["inference-research"]["verdict"] == "watch-treadmill"
    assert result["roles"]["body-research"]["verdict"] == "clean"
    assert result["validity"]["min_wakes"] == 5


def _stability_home(tmp_path, session="mishe-self-development-current"):
    home = tmp_path / "site"
    home.mkdir(exist_ok=True)
    (home / ".seed-raised").write_text(f"{session} $abc\n", encoding="utf-8")
    return home


def _stability_run(session_names=b"", server_pids=b"", pgrep_out=b"",
                   name_rc=0, pid_rc=0, pgrep_rc=0):
    """Route the sense's subprocess calls by their tmux/pgrep argv.

    The sense reads the session names and the socket's own server pids from two
    ``list-sessions`` calls, then the host-wide server list from pgrep; a test
    names each by the argv it carries so the owner read can be told apart.
    """
    class Result:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout

    def run(argv, **kwargs):
        text = " ".join(str(part) for part in argv)
        if "list-sessions" in text:
            if "#{pid}" in text:
                return Result(pid_rc, server_pids)
            return Result(name_rc, session_names)
        return Result(pgrep_rc, pgrep_out)

    return run


def test_session_stability_no_live_sessions_keeps_only_the_missing_session(
        monkeypatch, tmp_path):
    """No live session means no server of this plant's to shield.

    With the socket's session list empty, a foreign unshielded server must not
    be named; the recorded session is what drifts, not the foreign server.
    """
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run",
                        _stability_run(b"", b"", b"999\n", name_rc=1))
    monkeypatch.setattr(discovery.proc_table, "is_tmux_server", lambda pid: True)
    monkeypatch.setattr(discovery.proc_table, "read_oom_score_adj", lambda pid: "0")
    result = discovery._host_session_stability(_stability_home(tmp_path))
    assert result["state"] == "drift"
    assert result["live"] == []
    assert result["unshielded"] == []
    assert "session-missing=mishe-self-development-current" in result["sample"]


def test_session_stability_verified_when_live_and_shielded(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run",
                        _stability_run(b"mishe-self-development-current\nandroid-body\n",
                                       b"111\n", b"111\n"))
    monkeypatch.setattr(discovery.proc_table, "is_tmux_server", lambda pid: True)
    monkeypatch.setattr(discovery.proc_table, "read_oom_score_adj", lambda pid: "-900")
    result = discovery._host_session_stability(_stability_home(tmp_path))
    assert result["id"] == "sense.host.session-stability"
    assert result["state"] == "verified"
    assert result["unshielded"] == []


def test_session_stability_ignores_a_foreign_unshielded_server(monkeypatch, tmp_path):
    """A test's disposable server on its own socket is not this plant's to shield.

    The plant's server (111) is shielded; a foreign server (999) is not. Only
    the plant's own socket's servers may read as a shield lapse, so the foreign
    one must not turn the reading drift.
    """
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run",
                        _stability_run(b"mishe-self-development-current\n",
                                       b"111\n", b"111\n999\n"))
    monkeypatch.setattr(discovery.proc_table, "is_tmux_server", lambda pid: True)
    monkeypatch.setattr(discovery.proc_table, "read_oom_score_adj",
                        lambda pid: "-900" if pid == "111" else "0")
    result = discovery._host_session_stability(_stability_home(tmp_path))
    assert result["state"] == "verified"
    assert result["unshielded"] == []


def test_session_stability_keeps_foreign_servers_when_the_pid_list_is_unreadable(
        monkeypatch, tmp_path):
    """An unreadable owner list must stay visible, not read as stability.

    The socket's own server pids cannot be read, so the sense cannot tell a
    plant server from a foreign one; it falls back to every server so a broken
    read still surfaces the unshielded one.
    """
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run",
                        _stability_run(b"mishe-self-development-current\n", b"",
                                       b"999\n", pid_rc=1))
    monkeypatch.setattr(discovery.proc_table, "is_tmux_server", lambda pid: True)
    monkeypatch.setattr(discovery.proc_table, "read_oom_score_adj", lambda pid: "0")
    result = discovery._host_session_stability(_stability_home(tmp_path))
    assert result["state"] == "drift"
    assert result["unshielded"] == ["999"]


def test_session_stability_drift_when_session_missing(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run",
                        _stability_run(b"android-body\n", b"111\n", b"",
                                       pgrep_rc=1))
    result = discovery._host_session_stability(_stability_home(tmp_path))
    assert result["state"] == "drift"
    assert result["session"] == "mishe-self-development-current"
    assert "session-missing=mishe-self-development-current" in result["sample"]


def test_session_stability_unreadable_ownership_is_unknown_not_drift(monkeypatch, tmp_path):
    """A failed owned-session enumeration must not read the session as missing.

    The ambient listing omits the recorded session, but this home's ownership
    enumeration fails: the session may live on another socket. An empty owned
    set would call it missing; the reading must stay unknown instead.
    """
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run",
                        _stability_run(b"android-body\n", b"111\n", b"",
                                       pgrep_rc=1))

    def unreadable(home):
        raise OSError("socket enumeration failed")

    monkeypatch.setattr(discovery.tmux, "home_sessions", unreadable)
    result = discovery._host_session_stability(_stability_home(tmp_path))
    assert result["state"] == "unknown"
    assert "session-ownership=unreadable" in result["sample"]


def test_session_stability_drift_when_server_unshielded(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run",
                        _stability_run(b"mishe-self-development-current\n",
                                       b"222\n", b"222\n"))
    monkeypatch.setattr(discovery.proc_table, "is_tmux_server", lambda pid: True)
    monkeypatch.setattr(discovery.proc_table, "read_oom_score_adj", lambda pid: "200")
    result = discovery._host_session_stability(_stability_home(tmp_path))
    assert result["state"] == "drift"
    assert result["unshielded"] == ["222"]


def test_session_stability_tmux_failure_is_unknown(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("no tmux")))
    result = discovery._host_session_stability(_stability_home(tmp_path))
    assert result["state"] == "unknown"


def _restart_journal_output(*stamps):
    import json
    lines = []
    for index, stamp in enumerate(stamps):
        lines.append(json.dumps({
            "__CURSOR": f"cursor-{index}", "__REALTIME_TIMESTAMP": str(stamp),
            "_BOOT_ID": "boot-restart", "SYSLOG_IDENTIFIER": "systemd",
            "MESSAGE": ("Started mishe-self-development-current-session.scope"
                        " - Mishe resident session mishe-self-development-current.")}))
    return ("\n".join(lines) + "\n").encode() if lines else b""


def _restart_wiring(monkeypatch, stamps):
    from mishe_tauftauf import discovery
    output = _restart_journal_output(*stamps)

    class Journal:
        returncode = 0
        stdout = output
    monkeypatch.setattr(discovery, "_journal_command", lambda cmd, timeout=10: output)
    monkeypatch.setattr(discovery, "_journal_boot", lambda: "boot-restart")
    return discovery


def test_session_restarts_drift_on_churn(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    from mishe_tauftauf import discovery
    now = datetime.now(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    to_us = lambda value: int((value - epoch).total_seconds() * 1000000)
    stamps = [to_us(now - timedelta(minutes=50)),
              to_us(now - timedelta(minutes=30)),
              to_us(now - timedelta(minutes=10))]
    _restart_wiring(monkeypatch, stamps)
    result = discovery._host_session_restarts(_stability_home(tmp_path))
    assert result["id"] == "sense.host.session-restarts"
    assert result["state"] == "drift"
    assert result["verdict"] == "churn"
    assert "births=3" in result["sample"]


def test_session_restarts_verified_when_steady(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    from mishe_tauftauf import discovery
    now = datetime.now(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    to_us = lambda value: int((value - epoch).total_seconds() * 1000000)
    stamps = [to_us(now - timedelta(minutes=50))]
    _restart_wiring(monkeypatch, stamps)
    result = discovery._host_session_restarts(_stability_home(tmp_path))
    assert result["state"] == "verified"
    assert result["verdict"] == "steady"


def test_session_restarts_unknown_on_acquisition_failure(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery
    monkeypatch.setattr(discovery, "_journal_command", lambda cmd, timeout=10: None)
    monkeypatch.setattr(discovery, "_journal_boot", lambda: "boot-restart")
    result = discovery._host_session_restarts(_stability_home(tmp_path))
    assert result["state"] == "unknown"
    assert result["reason"] == "incomplete or failed acquisition"


def test_session_restarts_verified_on_empty_acquisition(monkeypatch, tmp_path):
    # Healthy session, zero rebirths: journalctl --grep exits 1 with empty
    # stdout, which _journal_command now surfaces as b"" (not None).
    from mishe_tauftauf import discovery
    monkeypatch.setattr(discovery, "_journal_command", lambda cmd, timeout=10: b"")
    monkeypatch.setattr(discovery, "_journal_boot", lambda: "boot-restart")
    result = discovery._host_session_restarts(_stability_home(tmp_path))
    assert result["state"] == "verified"
    assert result["verdict"] == "steady"
    assert result["reason"] == "no-births-in-window"
    assert "births=0" in result["sample"]


def test_authorship_fog_clean_tree_is_verified(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    class Status:
        returncode = 0
        stdout = b""
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Status())
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._coord_authorship_fog(home)
    assert result["id"] == "sense.coord.authorship-fog"
    assert result["state"] == "verified"
    assert result["fog"] == []


def test_authorship_fog_unclaimed_path_is_drift(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    class Status:
        returncode = 0
        stdout = b" M src/mishe_tauftauf/novel.py\n"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Status())
    home = tmp_path / "site"
    (home / "walls").mkdir(parents=True)
    (home / "walls" / "health.md").write_text("nothing relevant here\n", encoding="utf-8")
    (home / "chat.log").write_text("v1 1 2026-10-12T00:00:00Z seed ::\n .\n", encoding="utf-8")
    result = discovery._coord_authorship_fog(home)
    assert result["state"] == "drift"
    assert any("novel.py" in p for p in result["fog"])


def test_authorship_fog_claimed_path_is_verified(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    class Status:
        returncode = 0
        stdout = b" M src/mishe_tauftauf/novel.py\n"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Status())
    home = tmp_path / "site"
    (home / "walls").mkdir(parents=True)
    (home / "walls" / "health.md").write_text("landing novel.py after review\n", encoding="utf-8")
    result = discovery._coord_authorship_fog(home)
    assert result["state"] == "verified"
    assert result["fog"] == []

def test_authorship_fog_own_publication_does_not_claim(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    class Status:
        returncode = 0
        stdout = b" M src/mishe_tauftauf/novel.py\n"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Status())
    home = tmp_path / "site"
    (home / "walls").mkdir(parents=True)
    (home / "walls" / "health.md").write_text("nothing relevant here\n", encoding="utf-8")
    # The sense's own publication carries the fog paths; it must not count as a claim.
    (home / "chat.log").write_text(
        "sense.coord.authorship-fog: dirty=1 fog=1 ?? src/mishe_tauftauf/novel.py\n",
        encoding="utf-8")
    result = discovery._coord_authorship_fog(home)
    assert result["state"] == "drift"
    assert any("novel.py" in p for p in result["fog"])


def test_gate_latency_no_tape_is_unknown(tmp_path):
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    result = discovery._coord_gate_latency(home)
    assert result["state"] == "unknown"


def test_malformed_tape_reads_unknown_in_the_readings_that_read_it(tmp_path):
    """A tape that faults reads UNKNOWN, in every reading that reads it.

    The tape is read through the streaming reader now, and the reader raises
    where the whole-tape read raised. A reading that let that fault escape
    would take the scan down with it, and a reading that swallowed it would
    report a shorter tape as a whole one: both are worse than saying the tape
    is unreadable. The gate-latency reading reads the tape through the
    disposition reading, so it is covered by the same fault.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    (home / "chat.log").write_bytes(
        b"v1 00000000000000000001 2026-10-10T15:51:56.625536Z seed ::\n"
        b"    | a\n    .\n"
        b"v1 00000000000000000003 2026-10-10T15:51:57.625536Z seed ::\n"
        b"    | b\n    .\n")
    for reading in (discovery._coord_dm_disposition_age,
                    discovery._seed_settle_result_mix,
                    discovery._coord_gate_latency):
        result = reading(home)
        assert result["state"] == "unknown", reading.__name__
        assert "unreadable" in result["sample"], reading.__name__


def test_gate_latency_carries_the_disposition_readings_own_reason(tmp_path):
    """The gate's UNKNOWN names why the tape could not be read.

    The gate-latency reading does not parse the tape itself — the disposition
    reading it calls already parses the whole tape — so the reason the tape
    failed has to travel with the reading instead of being dropped on the way.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    home.mkdir()
    (home / "chat.log").write_bytes(b"not a header\n")
    result = discovery._coord_gate_latency(home)
    assert result["state"] == "unknown"
    assert "chat tape unreadable" in result["sample"]


def _landing_home(tmp_path, suite: bool):
    """A site home whose checkout is ``tmp_path``, suite file optional.

    ``_repo_landing_block`` derives the checkout as the site home's parent, so
    the focused suite it gates on lives at ``tmp_path/tests/test_proc_table.py``.
    """
    if suite:
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / "tests" / "test_proc_table.py").write_text("", encoding="utf-8")
    home = tmp_path / "site"
    home.mkdir()
    return home


def test_landing_block_clean_gate_verified(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_coord_authorship_fog",
                        lambda home: {"fog": [], "state": "verified"})
    class Gate:
        returncode = 0
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Gate())
    home = _landing_home(tmp_path, suite=True)
    result = discovery._repo_landing_block(home)
    assert result["id"] == "sense.repo.landing-block"
    assert result["state"] == "verified"
    assert result["blocks"] == []
    assert result["gate"] is True


def test_landing_block_absent_suite_is_not_gated(monkeypatch, tmp_path):
    """A checkout without the kernel focused suite must not read as a failure.

    A linked site's repo carries no ``tests/test_proc_table.py``, so the
    kernel's gate command fails there on the missing interpreter (``env``
    exits 127). That absence is not a landing block: the sense must stay
    verified and name the gate absent rather than report a failing test that
    does not exist in that repo.
    """
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_coord_authorship_fog",
                        lambda home: {"fog": [], "state": "verified"})

    class Env:
        returncode = 127
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Env())
    home = _landing_home(tmp_path, suite=False)
    result = discovery._repo_landing_block(home)
    assert result["state"] == "verified"
    assert result["blocks"] == []
    assert result["gate_applicable"] is False
    assert "gate=absent" in result["sample"]


def test_landing_block_failing_suite_is_a_block(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_coord_authorship_fog",
                        lambda home: {"fog": [], "state": "verified"})

    class Gate:
        returncode = 1
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Gate())
    home = _landing_home(tmp_path, suite=True)
    result = discovery._repo_landing_block(home)
    assert result["state"] == "drift"
    assert result["blocks"] == ["gate=fail"]
    assert result["gate"] is False


def test_landing_block_unlaunchable_runner_is_unknown(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_coord_authorship_fog",
                        lambda home: {"fog": [], "state": "verified"})

    def run(*a, **kw):
        raise OSError("no runner")
    monkeypatch.setattr(discovery.subprocess, "run", run)
    home = _landing_home(tmp_path, suite=True)
    result = discovery._repo_landing_block(home)
    assert result["state"] == "drift"
    assert result["blocks"] == ["gate=unknown"]


def test_loop_stall_verified_when_nothing_treadmills(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_mind_treadmill",
                        lambda home: {"state": "verified", "roles": {"docs": {"verdict": "clean"}}})
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._mind_loop_stall(home)
    assert result["id"] == "sense.mind.loop-stall"
    assert result["state"] == "verified"
    assert result["stalled"] == []


def test_loop_stall_drift_when_treadmill_plus_mature_gate(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_mind_treadmill",
                        lambda home: {"state": "verified",
                                      "roles": {"inference-research": {"verdict": "watch-treadmill"}}})
    monkeypatch.setattr(discovery, "_coord_dm_disposition_age",
                        lambda home, now=None: {"state": "verified", "stall": 2,
                                               "oldest_open": "witness:7h"})
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._mind_loop_stall(home)
    assert result["state"] == "drift"
    assert result["stalled"] == ["inference-research@witness:7h"]


def test_loop_stall_verified_when_gates_young(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_mind_treadmill",
                        lambda home: {"state": "verified",
                                      "roles": {"inference-research": {"verdict": "watch-treadmill"}}})
    monkeypatch.setattr(discovery, "_coord_dm_disposition_age",
                        lambda home, now=None: {"state": "verified", "stall": 0,
                                               "oldest_open": ""})
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._mind_loop_stall(home)
    assert result["state"] == "verified"


def test_loop_stall_unknown_when_treadmill_unreadable(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_mind_treadmill",
                        lambda home: {"state": "unknown"})
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._mind_loop_stall(home)
    assert result["state"] == "unknown"


def test_loop_stall_drift_when_docs_lane_plus_mature_gate(monkeypatch, tmp_path):
    """The docs-book lane routes through the same gate as the record lane.

    A docs-churning role with a mature open gate is the demonstrated loop
    shape; without this wiring the lane fires with no consumer and the stall
    never reaches the dashboard's cause list.
    """
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_mind_treadmill",
                        lambda home: {"state": "verified", "roles": {}})
    monkeypatch.setattr(discovery, "_mind_docs_treadmill",
                        lambda home: {"state": "verified",
                                      "roles": {"docs": {"verdict": "watch-docs-treadmill"}}})
    monkeypatch.setattr(discovery, "_coord_dm_disposition_age",
                        lambda home, now=None: {"state": "verified", "stall": 1,
                                               "oldest_open": "genome:7h"})
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._mind_loop_stall(home)
    assert result["state"] == "drift"
    assert result["stalled"] == ["docs@genome:7h"]


def test_loop_stall_verified_when_docs_lane_gate_young(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_mind_treadmill",
                        lambda home: {"state": "verified", "roles": {}})
    monkeypatch.setattr(discovery, "_mind_docs_treadmill",
                        lambda home: {"state": "verified",
                                      "roles": {"docs": {"verdict": "watch-docs-treadmill"}}})
    monkeypatch.setattr(discovery, "_coord_dm_disposition_age",
                        lambda home, now=None: {"state": "verified", "stall": 0,
                                               "oldest_open": ""})
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._mind_loop_stall(home)
    assert result["state"] == "verified"
    assert result["stalled"] == []


def test_loop_stall_ignores_unknown_docs_lane(monkeypatch, tmp_path):
    """An unreadable docs lane is not a clean bill and not a stall input.

    A role that cannot be read stays out of ``treadmilling``; the record lane
    still decides, and a broken docs lane alone never reads clean.
    """
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery, "_mind_treadmill",
                        lambda home: {"state": "verified", "roles": {}})
    monkeypatch.setattr(discovery, "_mind_docs_treadmill",
                        lambda home: {"state": "unknown", "roles": {}})
    monkeypatch.setattr(discovery, "_coord_dm_disposition_age",
                        lambda home, now=None: {"state": "verified", "stall": 1,
                                               "oldest_open": "genome:7h"})
    home = tmp_path / "site"
    home.mkdir()
    result = discovery._mind_loop_stall(home)
    assert result["state"] == "verified"
    assert result["stalled"] == []


def _keeper_display(pid):
    class Display:
        returncode = 0
        stdout = f"{pid}\n".encode()
    return Display


def test_server_keeper_verified_when_session_owns_server(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: _keeper_display(4242))
    monkeypatch.setattr(discovery.proc_table, "read_cgroup_unit",
                        lambda pid: "mishe-self-development-current-session-9f3a2b1c4d5e6f708192a3b4c5d6e7f8.service")
    result = discovery._host_server_keeper(_stability_home(tmp_path))
    assert result["id"] == "sense.host.server-keeper"
    assert result["state"] == "verified"
    assert result["keeper"] == "mishe-self-development-current-session-9f3a2b1c4d5e6f708192a3b4c5d6e7f8.service"


def test_server_keeper_verified_string_scope_raise(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: _keeper_display(7))
    monkeypatch.setattr(discovery.proc_table, "read_cgroup_unit",
                        lambda pid: "mishe-self-development-current-session.scope")
    result = discovery._host_server_keeper(_stability_home(tmp_path))
    assert result["state"] == "verified"


def test_server_keeper_drift_when_finished_test_holds_server(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: _keeper_display(2053654))
    monkeypatch.setattr(discovery.proc_table, "read_cgroup_unit",
                        lambda pid: "mishe-tauftauf-test-2052154-2e8e4ec20b-session-69098cb50e994a759167cd47c622babc.service")
    result = discovery._host_server_keeper(_stability_home(tmp_path))
    assert result["state"] == "drift"
    assert result["server_pid"] == "2053654"
    assert "foreign-keeper=mishe-tauftauf-test-2052154" in result["sample"]


def test_server_keeper_drift_when_foreign_scope_holds_server(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: _keeper_display(8123))
    monkeypatch.setattr(discovery.proc_table, "read_cgroup_unit",
                        lambda pid: "android-body-session.scope")
    result = discovery._host_server_keeper(_stability_home(tmp_path))
    assert result["state"] == "drift"
    assert result["keeper"] == "android-body-session.scope"


def test_server_keeper_unknown_when_session_not_attributable(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    class Missing:
        returncode = 1
        stdout = b""
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Missing())
    result = discovery._host_server_keeper(_stability_home(tmp_path))
    assert result["state"] == "unknown"


def test_server_keeper_unknown_when_pid_unreadable(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    class Junk:
        returncode = 0
        stdout = b"not-a-pid\n"
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: Junk())
    result = discovery._host_server_keeper(_stability_home(tmp_path))
    assert result["state"] == "unknown"


def test_server_keeper_unknown_when_cgroup_unreadable(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **kw: _keeper_display(31))
    monkeypatch.setattr(discovery.proc_table, "read_cgroup_unit", lambda pid: "")
    result = discovery._host_server_keeper(_stability_home(tmp_path))
    assert result["state"] == "unknown"


def test_server_keeper_unknown_when_session_receipt_missing(monkeypatch, tmp_path):
    from mishe_tauftauf import discovery

    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    result = discovery._host_server_keeper(tmp_path / "empty")
    assert result["state"] == "unknown"


def test_notify_scan_ignores_read_at_only_drift_changes(tmp_path):
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    rid = "sense.ledger.evidence-binding"
    sample_one = "all_bad=1 permanent_loss=0 latest_bad=0 latest_bad_roles=none latest_bad_last=none read_at=2026-10-15T08:00:00Z bound=1 latest_roles=1"
    sample_two = "all_bad=1 permanent_loss=0 latest_bad=0 latest_bad_roles=none latest_bad_last=none read_at=2026-10-15T08:01:00Z bound=1 latest_roles=1"
    readings_one = [{"id": rid, "kind": "read", "state": "drift", "sample": sample_one}]
    readings_two = [{"id": rid, "kind": "read", "state": "drift", "sample": sample_two}]
    snapshot_one = {"created": "2026-10-10T12:00:00Z", "node": "test", "observations": readings_one}
    snapshot_two = {"created": "2026-10-10T12:01:00Z", "node": "test", "observations": readings_two}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after == before

def test_notify_scan_marks_absent_artifact_instead_of_claiming_it(tmp_path):
    """A notice must not assert a `Full sample:` path that does not exist.

    A diagnostic caller can pass a synthetic snapshot with a nonexistent
    artifact; the notice is still appended to the tape. Naming the path as a
    full sample asserts evidence that never existed (wake 44217: five phantom
    `discover` notices on the canonical tape, later retired by a correction).
    The absent marker keeps the pointer without the claim.
    """
    from mishe_tauftauf import discovery

    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    missing = home / "discovery" / "probe-sample.json"
    snapshot = {"created": "2026-10-10T12:00:00Z", "node": "probe",
                "observations": [{"id": "sense.a", "kind": "read", "state": "drift",
                                  "sample": "measured violation"}]}
    discovery._notify_scan(home, snapshot, None, missing)
    body = Feed(home).entries()[-1].body
    assert "Full sample: absent" in body
    assert f"Full sample: {missing.resolve()}." not in body


def test_notify_scan_notifies_on_evidence_binding_cause_change(tmp_path):
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    rid = "sense.ledger.evidence-binding"
    sample_one = "all_bad=1 permanent_loss=0 latest_bad=0 latest_bad_roles=none latest_bad_last=none read_at=2026-10-15T08:00:00Z bound=1 latest_roles=1"
    sample_two = "all_bad=2 permanent_loss=0 latest_bad=0 latest_bad_roles=none latest_bad_last=none read_at=2026-10-15T08:00:00Z bound=1 latest_roles=1"
    snapshot_one = {"created": "2026-10-10T12:00:00Z", "node": "test", "observations": [{"id": rid, "kind": "read", "state": "drift", "sample": sample_one}]}
    snapshot_two = {"created": "2026-10-10T12:01:00Z", "node": "test", "observations": [{"id": rid, "kind": "read", "state": "drift", "sample": sample_two}]}
    from mishe_tauftauf.discovery import _notify_scan
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after > before

def test_notify_scan_ignores_gate_latency_age_only_changes(tmp_path):
    """A humanized-age advance alone is not a material gate-latency change.

    The live sample embeds `oldest=<role>:<age>` where the age advances every
    scan; identity uses the structured stall count and oldest role name
    (DM 44455: gate-latency was named in 3/3 consecutive notices with only
    the age differing).
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    rid = "sense.coord.gate-latency"
    reading_one = {"id": rid, "kind": "read", "state": "drift",
                   "sample": "stall=8 oldest=discover:1h02m",
                   "stall": 8, "oldest_open": "discover:1h02m"}
    reading_two = {"id": rid, "kind": "read", "state": "drift",
                   "sample": "stall=8 oldest=discover:1h03m",
                   "stall": 8, "oldest_open": "discover:1h03m"}
    snapshot_one = {"created": "2026-10-10T12:00:00Z", "node": "test",
                    "observations": [reading_one]}
    snapshot_two = {"created": "2026-10-10T12:01:00Z", "node": "test",
                    "observations": [reading_two]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after == before


def test_notify_scan_notifies_on_gate_latency_stall_change(tmp_path):
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    rid = "sense.coord.gate-latency"
    reading_one = {"id": rid, "kind": "read", "state": "drift",
                   "sample": "stall=8 oldest=discover:1h02m",
                   "stall": 8, "oldest_open": "discover:1h02m"}
    reading_two = {"id": rid, "kind": "read", "state": "drift",
                   "sample": "stall=9 oldest=discover:1h02m",
                   "stall": 9, "oldest_open": "discover:1h02m"}
    snapshot_one = {"created": "2026-10-10T12:00:00Z", "node": "test",
                    "observations": [reading_one]}
    snapshot_two = {"created": "2026-10-10T12:01:00Z", "node": "test",
                    "observations": [reading_two]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after > before


def test_notify_scan_notifies_on_gate_latency_oldest_role_change(tmp_path):
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    rid = "sense.coord.gate-latency"
    reading_one = {"id": rid, "kind": "read", "state": "drift",
                   "sample": "stall=8 oldest=discover:1h02m",
                   "stall": 8, "oldest_open": "discover:1h02m"}
    reading_two = {"id": rid, "kind": "read", "state": "drift",
                   "sample": "stall=8 oldest=health:05m",
                   "stall": 8, "oldest_open": "health:05m"}
    snapshot_one = {"created": "2026-10-10T12:00:00Z", "node": "test",
                    "observations": [reading_one]}
    snapshot_two = {"created": "2026-10-10T12:01:00Z", "node": "test",
                    "observations": [reading_two]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after > before

def _ghost_reading(age_s, ghosts=(".mishe-tauftauft",), files=None, total=2):
    """A `sense.site.ghost-homes` reading in the producer's structured shape."""
    files = {".mishe-tauftauft": 2} if files is None else files
    named = ",".join(ghosts)
    return {"id": "sense.site.ghost-homes", "kind": "read", "state": "drift",
            "sample": f"ghost-homes={len(ghosts)} files={total} holding={named} "
                      f"no-process={named} nested=0 newest={ghosts[0]} age={age_s}s",
            "ghosts": sorted(ghosts), "files": files, "total": total,
            "holding": sorted(ghosts), "no_process": sorted(ghosts),
            "empty": [], "nested": [], "symlinked": [],
            "roots_at_least": False, "files_at_least": False,
            "newest": {ghosts[0]: {"stamp": "2026-10-16T02:39:57+00:00",
                                   "age_s": age_s}}}


def test_notify_scan_ignores_ghost_home_age_only_changes(tmp_path):
    """A humanized-age advance alone is not a material ghost-home change.

    Two consecutive real scans 84 s apart (wake 59621, 13:35:01Z and
    13:36:25Z) differed only in `age=10h54m` -> `age=10h56m` and still
    re-announced `Material changes: sense.site.ghost-homes.`; identity drops
    the age view so the drift stays visible in every scan's drift line
    without re-notifying an unchanged condition.
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    snapshot_one = {"created": "2026-10-16T13:35:01Z", "node": "test",
                    "observations": [_ghost_reading(39240)]}
    snapshot_two = {"created": "2026-10-16T13:36:25Z", "node": "test",
                    "observations": [_ghost_reading(39360)]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after == before


def test_notify_scan_notifies_on_ghost_home_set_change(tmp_path):
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    one = _ghost_reading(39240)
    two = _ghost_reading(39240, ghosts=(".mishe-tauffauf", ".mishe-tauftauft"),
                         files={".mishe-tauffauf": 1, ".mishe-tauftauft": 2},
                         total=3)
    snapshot_one = {"created": "2026-10-16T13:35:01Z", "node": "test",
                    "observations": [one]}
    snapshot_two = {"created": "2026-10-16T13:36:25Z", "node": "test",
                    "observations": [two]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after > before


def test_notify_scan_notifies_on_ghost_home_unavailable_reason_change(tmp_path):
    """An unavailable ghost reading keeps sample-text identity.

    A reading without the structured fields (checkout enumeration truncated,
    or a ghost tree unreadable) must still re-notify when its reason changes,
    so the age-only identity cannot swallow a real availability change.
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"

    def unavailable(sample):
        return {"id": "sense.site.ghost-homes", "kind": "read",
                "state": "unknown", "sample": sample}

    snapshot_one = {"created": "2026-10-16T13:35:01Z", "node": "test",
                    "observations": [unavailable(
                        "ghost home unreadable: .mishe-tauftauft: permission denied")]}
    snapshot_two = {"created": "2026-10-16T13:36:25Z", "node": "test",
                    "observations": [unavailable(
                        "checkout enumeration truncated at 4000 names: /home/x; "
                        "ghost absence not established")]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after > before

def _dv_binding_reading(sample, records, bool_dv, unbound, state="drift"):
    """A `sense.ledger.dv-binding` reading in the producer's structured shape."""
    return {"id": "sense.ledger.dv-binding", "kind": "read", "state": state,
            "sample": sample, "records": records, "bool_dv": bool_dv,
            "unbound": unbound}


def test_notify_scan_ignores_dv_binding_coverage_tier_changes(tmp_path):
    """A coverage-tier advance alone is not a material dv-binding change.

    `records=N bool_dv=M` count the whole ledger and advance on every write
    while the flagged condition — the unbound phase set — is unchanged
    (DM 59781: real scans re-notified on records 101->102 and bool_dv 56->57,
    both with `unbound=applied` unchanged); identity slices the tier out.
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    snapshot_one = {"created": "2026-10-16T13:25:00Z", "node": "test",
                    "observations": [_dv_binding_reading(
                        "unbound=applied records=101 bool_dv=56", 101, 56, 1)]}
    snapshot_two = {"created": "2026-10-16T13:35:00Z", "node": "test",
                    "observations": [_dv_binding_reading(
                        "unbound=applied records=102 bool_dv=57", 102, 57, 1)]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after == before


def test_notify_scan_notifies_on_dv_binding_phase_set_change(tmp_path):
    """A new unbound phase — and the drift->verified flip — is material.

    The flip back to a clean bill must notify once and stay quiet while only
    the coverage tiers advance.
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    one = _dv_binding_reading("unbound=applied records=102 bool_dv=57",
                              102, 57, 1)
    two = _dv_binding_reading("unbound=applied,health-stale-pend-detection "
                              "records=103 bool_dv=58", 103, 58, 2)
    snapshot_one = {"created": "2026-10-16T13:25:00Z", "node": "test",
                    "observations": [one]}
    snapshot_two = {"created": "2026-10-16T13:35:00Z", "node": "test",
                    "observations": [two]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after > before
    three = _dv_binding_reading("unbound=0 records=104 bool_dv=59", 104, 59, 0,
                                state="verified")
    snapshot_three = {"created": "2026-10-16T13:45:00Z", "node": "test",
                      "observations": [three]}
    discovery._notify_scan(home, snapshot_three, snapshot_two, path)
    flipped = len(Feed(home).entries())
    assert flipped > after
    four = _dv_binding_reading("unbound=0 records=105 bool_dv=60", 105, 60, 0,
                               state="verified")
    snapshot_four = {"created": "2026-10-16T13:55:00Z", "node": "test",
                     "observations": [four]}
    discovery._notify_scan(home, snapshot_four, snapshot_three, path)
    assert len(Feed(home).entries()) == flipped


def test_notify_scan_notifies_on_dv_binding_unavailable_reason_change(tmp_path):
    """Unavailable dv-binding readings keep their reason in identity.

    The unknown sample carries the coverage tier ahead of `unreadable=<file>`;
    slicing must not collapse an unreadable store, an empty store and a
    missing patch store into one identity, and a tier advance with the same
    unreadable file must stay quiet.
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"

    def unavailable(sample, records=0, bool_dv=0):
        return _dv_binding_reading(sample, records, bool_dv, 0, state="unknown")

    snapshot_one = {"created": "2026-10-16T13:25:00Z", "node": "test",
                    "observations": [unavailable("records=0 bool_dv=0")]}
    snapshot_two = {"created": "2026-10-16T13:35:00Z", "node": "test",
                    "observations": [unavailable(
                        "records=102 bool_dv=57 unreadable=ledger.json.bak",
                        102, 57)]}
    snapshot_three = {"created": "2026-10-16T13:45:00Z", "node": "test",
                      "observations": [unavailable(
                          "records=103 bool_dv=58 "
                          "unreadable=ledger.json.bak,other.json",
                          103, 58)]}
    snapshot_four = {"created": "2026-10-16T13:55:00Z", "node": "test",
                     "observations": [unavailable("patch store unavailable")]}
    snapshot_five = {"created": "2026-10-16T14:05:00Z", "node": "test",
                     "observations": [unavailable("patch store unavailable")]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    previous = snapshot_one
    for snapshot in (snapshot_two, snapshot_three, snapshot_four):
        discovery._notify_scan(home, snapshot, previous, path)
        previous = snapshot
    after = len(Feed(home).entries())
    assert after == before + 3
    discovery._notify_scan(home, snapshot_five, previous, path)
    assert len(Feed(home).entries()) == after

def test_notify_scan_notifies_on_dv_binding_fourth_unbound_phase(tmp_path):
    """A 4th unbound phase is material even though the display cap hides it.

    The sample displays `unbound[:3]` (discovery.py:1965-1967), so a 4th
    phase does not appear in the sample text; the structured `unbound` count
    carries the change into identity (wake 60035: witness residual E).
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    # 3 unbound phases: sample shows all 3
    one = _dv_binding_reading(
        "unbound=applied,health-stale-pend-detection,other "
        "records=102 bool_dv=57", 102, 57, 3)
    # 4 unbound phases: sample still shows first 3, structured count is 4
    two = _dv_binding_reading(
        "unbound=applied,health-stale-pend-detection,other "
        "records=103 bool_dv=58", 103, 58, 4)
    snapshot_one = {"created": "2026-10-16T13:25:00Z", "node": "test",
                    "observations": [one]}
    snapshot_two = {"created": "2026-10-16T13:35:00Z", "node": "test",
                    "observations": [two]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after > before

def _delivery_invariant_reading(sample, records, bool_dv, violations=None,
                                state="unknown"):
    """A `sense.ledger.delivery-invariant` reading in the producer's shape."""
    return {"id": "sense.ledger.delivery-invariant", "kind": "read",
            "state": state, "sample": sample, "records": records,
            "bool_dv": bool_dv, "violations": violations or {}, "kind": "read"}


def test_notify_scan_ignores_delivery_invariant_unknown_tier_changes(tmp_path):
    """A coverage-tier advance in the UNKNOWN branch is not material.

    The UNKNOWN sample carries `records=N bool_dv=M` as partial reads of an
    unreadable store; they advance on every ledger write while the flagged
    condition — the violation set — is unchanged (wake 60035: witness DM
    59875). Identity slices the tier out.
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    one = _delivery_invariant_reading(
        "records=102 bool_dv=57 unreadable=ledger.json.bak", 102, 57)
    two = _delivery_invariant_reading(
        "records=103 bool_dv=58 unreadable=ledger.json.bak", 103, 58)
    snapshot_one = {"created": "2026-10-16T13:25:00Z", "node": "test",
                    "observations": [one]}
    snapshot_two = {"created": "2026-10-16T13:35:00Z", "node": "test",
                    "observations": [two]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after == before


def test_notify_scan_notifies_on_delivery_invariant_unknown_violation_change(
        tmp_path):
    """A violation-set change in the UNKNOWN branch is material.

    The violation set is the flagged condition; a new violation notifies
    even while the coverage tier is sliced out of identity.
    """
    home = tmp_path / "site"
    (home / "discovery").mkdir(parents=True, exist_ok=True)
    path = home / "discovery" / "sample.json"
    one = _delivery_invariant_reading(
        "records=102 bool_dv=57 unreadable=ledger.json.bak", 102, 57)
    two = _delivery_invariant_reading(
        "records=102 bool_dv=57 violations=health-stale-pend-detection:1 "
        "unreadable=ledger.json.bak", 102, 57,
        violations={"health-stale-pend-detection": 1})
    snapshot_one = {"created": "2026-10-16T13:25:00Z", "node": "test",
                    "observations": [one]}
    snapshot_two = {"created": "2026-10-16T13:35:00Z", "node": "test",
                    "observations": [two]}
    discovery._notify_scan(home, snapshot_one, None, path)
    before = len(Feed(home).entries())
    discovery._notify_scan(home, snapshot_two, snapshot_one, path)
    after = len(Feed(home).entries())
    assert after > before


def test_rewrite_basis_labels_chain_parent_reachable_and_root_commits():
    a, b, c, d = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
    reachable = {"p" * 40}
    parents = {a: "p" * 40, b: c, c: None, d: "p" * 40}
    assert discovery._rewrite_basis(parents, reachable) == {
        "chain-unreachable": 1, "parent-reachable": 2, "root": 1}


def test_commit_parents_missing_dropped_sha_is_none_not_root():
    a, b = "a" * 40, "b" * 40
    raw = f"{a} {'p' * 40}\n"
    assert proc_table.git_commit_parents(raw, [a, b]) is None
    assert proc_table.git_commit_parents(raw, [a]) == {a: "p" * 40}
    assert proc_table.git_commit_parents(f"{a}\n", [a]) == {a: None}


def test_tmux_windows_prefers_owned_session_over_receipt(tmp_path, monkeypatch):
    """Receipt names the default-socket (life-support) session; the owned pin session wins."""
    home = tmp_path / "site"
    home.mkdir()
    (home / ".seed-raised").write_text("life-support $1\n", encoding="utf-8")
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    monkeypatch.setattr(discovery.tmux, "home_sessions",
                        lambda _home: [("pin-session", "/sock/pin")])
    monkeypatch.setattr(discovery.tmux, "owned_socket", lambda _h, _s: "/sock/pin")
    windows = {(None, "life-support"): "genome\nhealth\n",
               ("/sock/pin", "pin-session"): "senses\nwitness\n"}
    seen = []

    def run(argv, **_kw):
        socket = argv[2] if argv[1] == "-S" else None
        seen.append(argv)
        out = windows.get((socket, argv[argv.index("-t") + 1]))
        return subprocess.CompletedProcess(argv, 0 if out else 1, out or "", "")

    monkeypatch.setattr(discovery.subprocess, "run", run)
    assert discovery._tmux_window_names(home) == ["senses", "witness"]
    monkeypatch.setenv("MISHE_SEED_SESSION", "life-support")
    monkeypatch.setattr(discovery.tmux, "owned_socket", lambda _h, _s: None)
    assert discovery._tmux_window_names(home) == ["genome", "health"]
    monkeypatch.delenv("MISHE_SEED_SESSION")
    monkeypatch.setattr(discovery.tmux, "home_sessions", lambda _home: [])
    assert discovery._tmux_window_names(home) == ["genome", "health"]
    (home / ".seed-raised").unlink()
    assert discovery._tmux_window_names(home) == []


def test_plant_session_prefers_owned_over_receipt(tmp_path, monkeypatch):
    """Two sessions on two sockets: receipt names the default-socket one; owned pin wins, env still wins."""
    home = tmp_path / "site"
    home.mkdir()
    (home / ".seed-raised").write_text("life-support $1\n", encoding="utf-8")
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    monkeypatch.setattr(discovery.tmux, "home_sessions", lambda _h: [("pin-session", "/sock/pin")])
    assert discovery._plant_session(home) == "pin-session"
    monkeypatch.setenv("MISHE_SEED_SESSION", "override")
    assert discovery._plant_session(home) == "override"
    monkeypatch.delenv("MISHE_SEED_SESSION")
    monkeypatch.setattr(discovery.tmux, "home_sessions", lambda _h: [])
    assert discovery._plant_session(home) == "life-support"
