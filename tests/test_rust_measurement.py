"""The rust-measurement duty: a deterministic fold reading, not a hand-written GREEN.

The verdict is GREEN only when every role was measured under both triggers, RED
when a trigger is hit, and UNKNOWN when the host guard or the fold prevents a
reading. These tests drive the real checker against an empty site home so they
stay fast and do not load the live tape.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "checks" / "rust-measurement.py"
REPO = CHECK.parents[1]


def run(home: Path, *extra: str) -> subprocess.CompletedProcess:
    # Generous pressure bars: these tests drive the verdict and child-fold paths.
    # The host guard's own bars are exercised by test_busy_host_is_unknown_not_green
    # and test_pressure_guard_is_unknown_not_green.
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(REPO),
         "--roles", "senses", "--no-artifact",
         "--max-cpu-pressure", "100000", "--max-mem-pressure", "100000", *extra],
        capture_output=True, text=True, timeout=120)


def check_module():
    spec = importlib.util.spec_from_file_location("rust_measurement_check", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_pressure_guard_is_unknown_not_green(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    proc = run(home, "--max-load", "100000", "--max-cpu-pressure", "-1")
    assert proc.returncode == 3
    assert "cpu pressure" in proc.stderr


def test_default_cpu_pressure_bar_admits_the_measured_busy_host():
    # A live pin run skipped at `some avg10` 32.85 with load1 1.17; forced at 39.56
    # the fold still measured 4.161 CPU-s, 17% under the 5.0 trigger. A bar at or
    # below that pressure turns ordinary host activity into UNKNOWN without
    # protecting the verdict (plan 10.45).
    assert check_module().DEFAULT_MAX_CPU_PRESSURE > 40.0


def test_pressure_some_avg10_reads_the_psi_layout(tmp_path):
    mod = check_module()
    psi = tmp_path / "cpu.pressure"
    psi.write_text("some avg10=3.50 avg60=1.25 avg300=0.40 total=1234\n"
                   "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    assert mod.pressure_some_avg10(psi) == 3.5
    assert mod.pressure_some_avg10(tmp_path / "absent") is None


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
         "--max-load", "100000", "--max-cpu-pressure", "100000",
         "--max-mem-pressure", "100000"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    data = json.loads((home / "artifacts" / "rust-plan-20261107"
                       / "rust-measurement-latest.json").read_text())
    assert data["readings"][0]["role"] == "senses"
    assert data["over"] == []
    # The reading is only auditable if the guard facts it was taken under are kept.
    assert data["guard"]["online_cpus"] >= 1
    assert "cpu_pressure_some_avg10" in data["guard"]


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
                                     "--max-cpu-pressure", "100000",
                                     "--max-mem-pressure", "100000",
                                     "--no-artifact"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "3 entries" in proc.stdout


def test_history_appends_a_completed_sample_with_its_guard(tmp_path):
    # 10.45's 60 bar rests on one measured point; the history is where the next
    # calibration gets a curve, so a sample must keep both the reading and the
    # pressure it was taken under.
    home = tmp_path / "site"
    home.mkdir()
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(REPO),
         "--roles", "senses", "--cpu-trigger", "1000", "--rss-trigger-kb", "10000000",
         "--max-load", "100000", "--max-cpu-pressure", "100000",
         "--max-mem-pressure", "100000"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    lines = (home / "artifacts" / "rust-plan-20261107"
             / "rust-measurement-history.jsonl").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["verdict"] == "green" and record["readings"][0]["role"] == "senses"
    assert "cpu_pressure_some_avg10" in record["guard"]


def test_history_records_a_guard_skip_with_the_facts_it_saw(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(REPO),
         "--roles", "senses", "--max-load", "-1"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 3 and "skipped, host busy" in proc.stderr
    record = json.loads((home / "artifacts" / "rust-plan-20261107"
                         / "rust-measurement-history.jsonl").read_text().splitlines()[0])
    assert record["verdict"] == "skipped" and "loadavg" in record["skip"]
    assert record["guard"]["load1"] >= 0


def test_history_is_bounded_to_the_newest_lines(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    mod = check_module()
    path = home / mod.ARTIFACT_DIR / "rust-measurement-history.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join('{"n":%d}\n' % i for i in range(5)))
    mod.append_history(home, {"n": 5}, max_lines=3)
    assert path.read_text().splitlines() == ['{"n":3}', '{"n":4}', '{"n":5}']
