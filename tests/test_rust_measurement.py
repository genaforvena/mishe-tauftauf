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


def test_duty_measures_release_source_not_editable_checkout(tmp_path):
    from mishe_tauftauf import duties

    checkout = tmp_path / "checkout"
    release = tmp_path / "release"
    home = tmp_path / "site"
    home.mkdir()
    for tree, count in ((checkout, 7), (release, 3)):
        package = tree / "src" / "mishe_tauftauf"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "feed.py").write_text(
            "class Feed:\n"
            "    def __init__(self, home): pass\n"
            f"    def role_entries(self, role, _force_full=False): return [None] * {count}\n"
        )
    (release / "duties.json").write_text((REPO / "duties.json").read_text())
    duty = next(d for d in duties.declared(checkout, home, source=release)
                if d["name"] == "rust-measurement")
    # The checker itself is the real one; only its target source trees differ.
    command = duty["command"]
    command[1] = str(CHECK)
    proc = subprocess.run(command + ["--roles", "senses", "--max-load", "100000",
                                     "--no-artifact"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "3 entries" in proc.stdout
