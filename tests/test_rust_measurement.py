"""The rust-measurement duty: a deterministic fold reading, not a hand-written GREEN.

The verdict is GREEN only when every role was measured under both triggers, RED
when a trigger is hit, and UNKNOWN when the host guard or the fold prevents a
reading. These tests drive the real checker against an empty site home so they
stay fast and do not load the live tape.
"""
import json
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "checks" / "rust-measurement.py"
REPO = CHECK.parents[1]


def run(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(REPO),
         "--roles", "senses", "--no-artifact", *extra],
        capture_output=True, text=True, timeout=120)


def test_missing_home_is_unknown(tmp_path):
    proc = run(tmp_path / "absent")
    assert proc.returncode == 3
    assert "UNKNOWN" in proc.stderr


def test_busy_host_is_unknown_not_green(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    proc = run(home, "--max-load", "-1")
    assert proc.returncode == 3
    assert "skipped, host busy" in proc.stderr


def test_under_triggers_is_green(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    proc = run(home, "--cpu-trigger", "1000", "--rss-trigger-kb", "10000000",
               "--max-load", "100000")
    assert proc.returncode == 0, proc.stderr
    assert "GREEN" in proc.stdout and "senses" in proc.stdout


def test_trigger_hit_is_red(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    proc = run(home, "--cpu-trigger", "-1", "--max-load", "100000")
    assert proc.returncode == 2
    assert "RED" in proc.stderr and "senses cpu" in proc.stderr


def test_artifact_records_the_reading(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(REPO),
         "--roles", "senses", "--cpu-trigger", "1000", "--rss-trigger-kb", "10000000",
         "--max-load", "100000"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    data = json.loads((home / "artifacts" / "rust-plan-20261107"
                       / "rust-measurement-latest.json").read_text())
    assert data["readings"][0]["role"] == "senses"
    assert data["over"] == []


def test_duty_declares_host_busy_exit_as_unknown():
    # The checker exits 3 when the host guard skips it. Without declaring 3 as unknown,
    # the producer treats it as a crash, the unit fails, and the failed-service signal goes RED.
    duties = json.loads((Path(__file__).resolve().parents[1] / "duties.json").read_text())["duties"]
    duty = next(d for d in duties if d["name"] == "rust-measurement")
    assert duty.get("unknown_codes") == [3]
