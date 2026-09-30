from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from mishe_tauftauf.discovery import _cpu_busy, latest, scan
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
    report = Feed(home).entries()[-1].body
    assert report.startswith("[discovery] Read-only scan at ")
    assert "Verified readings:" in report
    assert "Unknown readings:" in report
    assert f"Full sample: {artifact.resolve()}." in report
    assert "Next: senses should" in report
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
    assert latest(home) == second
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
        "sample": "testchip=42.5C", "kind": "read",
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
    with patch("mishe_tauftauf.discovery._thermal_slots", return_value=list(synthetic)):
        observed = {row["id"]: row for row in discovery.sample(tmp_path)["observations"]}
    reading = observed["sense.thermal.hwmon"]
    assert reading["state"] == "verified"
    assert reading["sample"] == "testchip=42.5C"
    # A malformed sensor must not break the read or appear in it.
    assert "not-a-number" not in str(reading["sample"])


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


def test_overlay_rate_uses_tx_rx_deltas_without_operstate_gate(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    stats = tmp_path / "tailscale0" / "statistics"
    stats.mkdir(parents=True)
    tx, rx = stats / "tx_bytes", stats / "rx_bytes"
    tx.write_text("1000\n")
    rx.write_text("2000\n")
    reads = {tx: iter(("1000\n", "1500\n")), rx: iter(("2000\n", "2300\n"))}
    with patch("mishe_tauftauf.discovery._read",
               side_effect=lambda path, limit=65536: next(reads[path])):
        with patch("mishe_tauftauf.discovery.time.sleep"):
            assert discovery._overlay_egress_rate(tmp_path, interval=0.1) == {
                "state": "verified",
                "sample": "short-window=0.1s egress=5000 B/s ingress=3000 B/s"}


def test_overlay_rate_reports_unknown_for_missing_or_decreased_counter(tmp_path: Path) -> None:
    from mishe_tauftauf import discovery

    stats = tmp_path / "tailscale0" / "statistics"
    stats.mkdir(parents=True)
    tx, rx = stats / "tx_bytes", stats / "rx_bytes"
    tx.write_text("1000\n")
    rx.write_text("2000\n")
    reads = {tx: iter(("1000\n", "900\n")), rx: iter(("2000\n", "2100\n"))}
    with patch("mishe_tauftauf.discovery._read",
               side_effect=lambda path, limit=65536: next(reads[path])):
        with patch("mishe_tauftauf.discovery.time.sleep"):
            result = discovery._overlay_egress_rate(tmp_path)
    assert result == {"state": "unknown", "sample": "tailscale0 tx_bytes counter decreased"}
