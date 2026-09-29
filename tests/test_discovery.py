from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from mishe_tauftauf.discovery import latest, scan
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
