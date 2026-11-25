"""The rust-measurement duty: a deterministic fold reading, not a hand-written GREEN.

The verdict is GREEN only when every role was measured under both triggers and
the installed bytes match the release source; RED when a trigger or binary drift
is hit, and UNKNOWN when the host guard, the fold, or unreadable installed-byte
provenance prevents a reading. These tests drive the real checker against an
empty site home so they stay fast and do not load the live tape.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.records_binary import pin_records_binary
from tests.tape_binary import pin_tape_binary

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
    _install_all(home, REPO)
    proc = run(home, "--cpu-trigger", "1000", "--rss-trigger-kb", "10000000",
               "--max-load", "100000")
    assert proc.returncode == 0, proc.stderr
    assert "GREEN" in proc.stdout and "senses" in proc.stdout
    # The summary must carry both halves of the fold, not just the reader.
    assert "reader" in proc.stdout and "children" in proc.stdout
    assert "= " in proc.stdout and "CPU-s" in proc.stdout


def test_trigger_hit_is_red(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    _install_all(home, REPO)
    proc = run(home, "--cpu-trigger", "-1", "--max-load", "100000")
    assert proc.returncode == 2
    assert "RED" in proc.stderr and "senses reader cpu" in proc.stderr


def test_install_records_missing_is_unknown_not_green(tmp_path):
    # A CPU reading of a path whose installed bytes nobody verified cannot be GREEN
    # on its own; the verdict is the site's state, not just the fold number.
    home = tmp_path / "site"
    (home / "bin").mkdir(parents=True)
    proc = run(home, "--cpu-trigger", "1000", "--rss-trigger-kb", "10000000",
               "--max-load", "100000")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "UNKNOWN" in proc.stderr and "with no install record" in proc.stderr
    assert "GREEN" not in proc.stdout


def test_unresolvable_install_commit_is_unknown_not_green(tmp_path):
    # A record whose commit git cannot resolve leaves those bytes unverified: the
    # checker must not fall back to the CPU verdict and call it GREEN.
    home = tmp_path / "site"
    home.mkdir()
    _install_all(home, REPO)
    record_path = home / "bin" / "mishe-tape.install.json"
    record = json.loads(record_path.read_text())
    record["source_commit"] = "0" * 40
    record_path.write_text(json.dumps(record))
    proc = run(home, "--cpu-trigger", "1000", "--rss-trigger-kb", "10000000",
               "--max-load", "100000")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "UNKNOWN" in proc.stderr and "mishe-tape" in proc.stderr
    assert "GREEN" not in proc.stdout


def test_artifact_records_the_reading(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    _install_all(home, REPO)
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
    # A fold reading that claims to bound the fold must name all of its cost.
    reading = data["readings"][0]
    assert reading["children_cpu_s"] >= 0
    assert reading["fold_cpu_s"] >= reading["cpu_s"]
    assert reading["record_decisions"] is None
    assert reading["checkpoint_rebuilt"] is None
    assert data["triggers"]["cpu_s"] == 1000.0
    assert data["over"] == []
    # The reading is only auditable if the guard facts it was taken under are kept.
    assert data["guard"]["online_cpus"] >= 1
    assert "cpu_pressure_some_avg10" in data["guard"]


def test_fold_reports_its_own_peak_not_the_launchers(tmp_path):
    # Plan 10.57: across fork+exec Linux carries the launcher's resident set into
    # the child's ru_maxrss. A caller that had loaded the tape in-process (a wake
    # engine at ~350 MB) made the same fold read 3x larger and would have tripped
    # the RSS bar on a fold that never grew. The reading must be the child's own
    # VmHWM, so a 300 MB launcher cannot move it.
    mod = check_module()
    home = tmp_path / "site"
    home.mkdir()
    ballast = bytearray(300 * 1024 * 1024)
    for offset in range(0, len(ballast), 4096):  # touch every page: real RSS, not reserved
        ballast[offset] = 1
    launcher_kb = _vmhwm_kb()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.run(
        [sys.executable, "-c", mod.CHILD, str(home), "senses"],
        capture_output=True, text=True, timeout=120, env=env)
    del ballast
    assert proc.returncode == 0, proc.stderr
    assert launcher_kb > 250_000, "ballast did not become resident; the test would be vacuous"
    reading = json.loads(proc.stdout.strip().splitlines()[-1])
    assert reading["maxrss_kb"] < 200_000, reading


def _vmhwm_kb() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    raise AssertionError("/proc/self/status has no VmHWM")


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
            f"    def role_entries(self, role, _force_full=False, _fold_diagnostics=None): return [None] * {count}\n"
        )
    (release / "duties.json").write_text((REPO / "duties.json").read_text())
    # The duty compares the installed bytes with the release commit, so the release
    # needs a commit and the site a matching install record set.
    subprocess.run(["git", "-C", str(release), "init", "-q"], check=True)
    _commit(release, "release")
    _install_all(home, release)
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
    _install_all(home, REPO)
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


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
                    "-c", "user.name=t", "commit", "-qm", message], check=True)
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _rust_release(tmp_path: Path) -> Path:
    """A release worktree: one crate, and the package the fold child imports."""
    repo = tmp_path / "release"
    (repo / "rust" / "mishe-tape" / "src").mkdir(parents=True)
    (repo / "rust" / "mishe-tape" / "Cargo.toml").write_text("[package]\nname='mishe-tape'\n")
    (repo / "rust" / "mishe-tape" / "src" / "main.rs").write_text("fn main() {}\n")
    package = repo / "src" / "mishe_tauftauf"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "feed.py").write_text(
        "class Feed:\n"
        "    def __init__(self, home): pass\n"
        "    def role_entries(self, role, _force_full=False, _fold_diagnostics=None): return [None] * 3\n")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    _commit(repo, "release with the installed bytes")
    return repo


def _install(home: Path, repo: Path) -> Path:
    """An install record whose sha256 matches the bytes and whose commit is HEAD."""
    import hashlib

    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    body = bin_dir / "mishe-tape"
    body.write_bytes(b"frame the tape\n")
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    (bin_dir / "mishe-tape.install.json").write_text(json.dumps({
        "binary": "mishe-tape",
        "sha256": hashlib.sha256(body.read_bytes()).hexdigest(),
        "source_commit": commit,
    }))
    return body


def _install_all(home: Path, repo: Path) -> None:
    """A record for every crate the checker knows, all built from repo's HEAD.

    The checker will not report GREEN unless it could verify the installed bytes,
    so a test that drives the CPU verdict needs a complete install set.
    """
    import hashlib

    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    for binary in check_module().CRATES:
        body = bin_dir / binary
        body.write_bytes(b"built bytes for " + binary.encode() + b"\n")
        (bin_dir / f"{binary}.install.json").write_text(json.dumps({
            "binary": binary,
            "sha256": hashlib.sha256(body.read_bytes()).hexdigest(),
            "source_commit": commit,
        }))


def test_binary_provenance_is_clean_when_the_crates_match_the_installed_bytes(tmp_path):
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    facts = mod.binary_provenance(home, repo)
    assert facts["checked"] == len(mod.CRATES)
    assert facts["drift"] == [] and facts["broken"] == []
    assert facts["unknown"] is None


def test_binary_provenance_flags_crate_source_newer_than_the_installed_bytes(tmp_path):
    # The plan's 10.46 gap: $SITE/bin is not refreshed by a pin move, so a release
    # can carry crate source newer than the bytes the consumer execs.
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    (repo / "rust" / "mishe-tape" / "src" / "main.rs").write_text("fn main() { }\n")
    _commit(repo, "newer crate source")
    facts = mod.binary_provenance(home, repo)
    assert facts["checked"] == len(mod.CRATES)
    assert len(facts["drift"]) == 1 and "mishe-tape" in facts["drift"][0]
    assert facts["broken"] == []


def test_binary_provenance_built_ahead_of_release_is_not_drift(tmp_path):
    # 2026-11-25: the network mind installed 0.2.0 from 79c137c4, which is newer than the
    # served release 1257eb25. The release carries nothing those bytes lack, so it is named,
    # not flagged RED as drift.
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    (repo / "rust" / "mishe-tape" / "src" / "main.rs").write_text("fn main() { }\n")
    _commit(repo, "crate source the install is built from")
    _install_all(home, repo)
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q", "HEAD~1"], check=True)
    facts = mod.binary_provenance(home, repo)
    assert facts["checked"] == len(mod.CRATES)
    assert facts["drift"] == [] and facts["broken"] == [] and facts["unknown"] is None
    assert len(facts["ahead"]) == len(mod.CRATES) and "mishe-tape" in " ".join(facts["ahead"])


def test_binary_provenance_flags_bytes_that_no_longer_match_their_record(tmp_path):
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    (home / "bin" / "mishe-tape").write_bytes(b"not the built bytes\n")
    facts = mod.binary_provenance(home, repo)
    assert facts["checked"] == len(mod.CRATES) and facts["drift"] == []
    assert len(facts["broken"]) == 1 and "sha256" in facts["broken"][0]


def test_binary_provenance_names_a_crate_with_no_install_record(tmp_path):
    # A crate the resolver would refuse must not be silently dropped from the count
    # that the GREEN line advertises.
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    (home / "bin" / "mishe-records.install.json").unlink()
    facts = mod.binary_provenance(home, repo)
    assert facts["checked"] == len(mod.CRATES) - 1
    assert facts["unknown"] is not None and "mishe-records" in facts["unknown"]


def test_unreadable_provenance_names_every_crate_not_only_the_last(tmp_path):
    # Two independent gaps must both appear: a missing record used to overwrite
    # the earlier "cannot compare" reason, leaving a person with half the story.
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    (home / "bin" / "mishe-records.install.json").write_text(json.dumps({
        "binary": "mishe-records",
        "sha256": "0" * 64,
        "source_commit": "0" * 40,
    }))
    (home / "bin" / "mishe-unit-paths.install.json").unlink()
    facts = mod.binary_provenance(home, repo)
    assert "mishe-records" in facts["unknown"]
    assert "mishe-unit-paths" in facts["unknown"]


def test_installed_binary_drift_is_red_not_green(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    repo = _rust_release(tmp_path)
    _install(home, repo)
    (repo / "rust" / "mishe-tape" / "src" / "main.rs").write_text("fn main() { }\n")
    _commit(repo, "newer crate source")
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(repo),
         "--roles", "senses", "--cpu-trigger", "1000", "--rss-trigger-kb", "10000000",
         "--max-load", "100000", "--max-cpu-pressure", "100000",
         "--max-mem-pressure", "100000"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RED installed Rust bytes" in proc.stderr and "mishe-tape" in proc.stderr
    record = json.loads((home / "artifacts" / "rust-plan-20261107"
                         / "rust-measurement-history.jsonl").read_text().splitlines()[0])
    assert record["verdict"] == "red" and record["binaries"]["drift"]


def test_sample_records_verified_release_and_imported_feed(tmp_path):
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(repo),
         "--roles", "senses", "--max-load", "100000", "--max-cpu-pressure", "100000",
         "--max-mem-pressure", "100000"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    artifact = json.loads((home / "artifacts" / "rust-plan-20261107" /
                           "rust-measurement-latest.json").read_text())
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert artifact["release"] == {"path": str(repo.resolve()), "commit": commit}
    assert artifact["readings"][0]["feed_path"] == str((repo / "src/mishe_tauftauf/feed.py").resolve())
    history = json.loads((home / "artifacts" / "rust-plan-20261107" /
                          "rust-measurement-history.jsonl").read_text())
    assert history["release"] == artifact["release"]


def test_changed_release_during_fold_is_unknown(tmp_path, monkeypatch, capsys):
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    original = mod.fold_role
    def change_then_fold(*args):
        reading = original(*args)
        # A comment keeps the release importable for the live variant's child,
        # while the dirty source is what release_identity must catch.
        path = repo / "src" / "mishe_tauftauf" / "feed.py"
        path.write_text(path.read_text() + "# release moved\n")
        return reading
    monkeypatch.setattr(mod, "fold_role", change_then_fold)
    assert mod.main(["--home", str(home), "--repo", str(repo), "--roles", "senses",
                     "--max-load", "100000", "--max-cpu-pressure", "100000",
                     "--max-mem-pressure", "100000"]) == 3
    assert "UNKNOWN" in capsys.readouterr().err
    record = json.loads((home / mod.ARTIFACT_DIR / "rust-measurement-history.jsonl").read_text())
    assert record["verdict"] == "unknown"


def test_binary_changed_during_fold_is_unknown(tmp_path, monkeypatch, capsys):
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    original = mod.fold_role
    def change_then_fold(*args):
        reading = original(*args)
        (home / "bin/mishe-tape").write_bytes(b"different executable")
        return reading
    monkeypatch.setattr(mod, "fold_role", change_then_fold)
    assert mod.main(["--home", str(home), "--repo", str(repo), "--roles", "senses",
                     "--max-load", "100000", "--max-cpu-pressure", "100000",
                     "--max-mem-pressure", "100000"]) == 3
    assert "installed binary provenance changed" in capsys.readouterr().err


def test_wrong_import_path_is_unknown(tmp_path, monkeypatch, capsys):
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    original = mod.fold_role
    def wrong_import(*args):
        reading = original(*args)
        reading["feed_path"] = "/other/feed.py"
        return reading
    monkeypatch.setattr(mod, "fold_role", wrong_import)
    assert mod.main(["--home", str(home), "--repo", str(repo), "--roles", "senses",
                     "--max-load", "100000", "--max-cpu-pressure", "100000",
                     "--max-mem-pressure", "100000"]) == 3
    assert "fold imported feed from outside" in capsys.readouterr().err


def test_simultaneous_provenance_failures_remain_in_diagnostics(tmp_path, monkeypatch, capsys):
    mod = check_module()
    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    _install_all(home, repo)
    (repo / "src/untracked.py").write_text("# dirty source\n")
    original = mod.fold_role

    def change_during_fold(*args):
        reading = original(*args)
        (home / "bin/mishe-tape").write_bytes(b"different executable")
        reading["feed_path"] = "/other/feed.py"
        return reading

    monkeypatch.setattr(mod, "fold_role", change_during_fold)
    assert mod.main(["--home", str(home), "--repo", str(repo), "--roles", "senses",
                     "--max-load", "100000", "--max-cpu-pressure", "100000",
                     "--max-mem-pressure", "100000"]) == 3
    stderr = capsys.readouterr().err
    artifact = json.loads((home / mod.ARTIFACT_DIR / "rust-measurement-latest.json").read_text())
    history = json.loads((home / mod.ARTIFACT_DIR / "rust-measurement-history.jsonl").read_text())
    assert history["verdict"] == "unknown"
    for reason in ("release source has uncommitted or untracked changes",
                   "installed binary provenance changed during sampling",
                   "fold imported feed from outside the release"):
        assert reason in stderr
        assert reason in artifact["provenance_error"]
        assert reason in history["provenance_error"]


def test_fold_uses_release_source_despite_stale_bytecode(tmp_path):
    import py_compile

    repo = _rust_release(tmp_path)
    home = tmp_path / "site"
    (repo / ".gitignore").write_text("__pycache__/\n")
    _commit(repo, "ignore Python caches")
    feed = repo / "src/mishe_tauftauf/feed.py"
    source = feed.read_text()
    altered = source.replace("* 3", "* 9")
    assert len(source) == len(altered) and source != altered
    stamp = 1700000000
    feed.write_text(altered)
    os.utime(feed, (stamp, stamp))
    py_compile.compile(str(feed), doraise=True)
    feed.write_text(source)
    os.utime(feed, (stamp, stamp))
    assert not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain", "--", "src"], text=True)

    reading = check_module().fold_role(repo, home, "senses", 30)
    assert reading["entries"] == 3


def _child_reading(tmp_path, feed_source):
    mod = check_module()
    pkg = tmp_path / "mishe_tauftauf"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "feed.py").write_text(feed_source)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", mod.CHILD, str(tmp_path), "probe"],
        capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_child_cpu_is_attributed_to_each_executable(tmp_path):
    # Popen's waitpid discards rusage; wait4 returns it. A fold launching two
    # children must name both, and their sum must equal the aggregate
    # children_cpu_s — otherwise the breakdown is a label with no measurement.
    source = (
        "import subprocess, sys\n"
        "class Feed:\n"
        "    def __init__(self, home): pass\n"
        "    def role_entries(self, role, _force_full=False, _fold_diagnostics=None):\n"
        "        subprocess.run([sys.executable, '-c', 'sum(range(200000))'], check=True)\n"
        "        subprocess.run(['/bin/sh', '-c',\n"
        "                        'i=0; while [ $i -lt 20000 ]; do i=$((i+1)); done'], check=True)\n"
        "        return [1, 2]\n"
    )
    reading = _child_reading(tmp_path, source)
    by_exe = reading["child_cpu_by_exe"]
    assert set(by_exe) == {os.path.basename(sys.executable), "sh"}, by_exe
    assert any(secs > 0 for secs in by_exe.values()), by_exe
    assert abs(sum(by_exe.values()) - reading["children_cpu_s"]) < 0.05, reading
    assert reading["cpu_pressure_some_avg10_before"] is not None
    assert reading["cpu_pressure_some_avg10_after"] is not None


def test_child_cpu_breakdown_is_empty_when_no_child_ran(tmp_path):
    source = (
        "class Feed:\n"
        "    def __init__(self, home): pass\n"
        "    def role_entries(self, role, _force_full=False, _fold_diagnostics=None):\n"
        "        return [1]\n"
    )
    reading = _child_reading(tmp_path, source)
    assert reading["child_cpu_by_exe"] == {}
    assert reading["children_cpu_s"] == 0.0


@pytest.mark.usefixtures("pin_records_binary", "pin_tape_binary")
def test_fold_reports_verifier_decisions_and_checkpoint_rebuild(tmp_path):
    from mishe_tauftauf.feed import Feed, _encode_entry
    from mishe_tauftauf.records import prepare

    mod = check_module()
    feed = Feed(tmp_path)
    missing = mod.fold_role(REPO, tmp_path, "senses", 30)
    assert missing["record_decisions"] is None
    assert missing["checkpoint_rebuilt"] is None

    reference = prepare(tmp_path, {"fixture": True}, kind="event")
    feed.path.write_bytes(_encode_entry(1, "2026-11-21T16:00:00Z", "senses",
                                        f"Measurement fixture\n{reference}"))
    cold = mod.fold_role(REPO, tmp_path, "senses", 30)
    warm = mod.fold_role(REPO, tmp_path, "senses", 30)

    assert cold["record_decisions"] == {"V": 0, "N": 1, "S": 0}
    assert warm["record_decisions"] == {"V": 1, "N": 0, "S": 0}
    assert cold["checkpoint_rebuilt"] is True
    assert warm["checkpoint_rebuilt"] is False
    assert cold["entries"] == warm["entries"]

    record, = (tmp_path / "records").glob("*.json")
    record.unlink()
    failed = run(tmp_path, "--max-load", "100000")
    assert failed.returncode == 3
    assert "UNKNOWN fold failed" in failed.stderr and "record unavailable" in failed.stderr
    assert "GREEN" not in failed.stdout


@pytest.mark.usefixtures("pin_records_binary", "pin_tape_binary")
def test_live_variant_reads_the_cache_path_and_agrees_with_the_rebuild(tmp_path):
    # The trigger rides on the forced fold, which a wake does not pay; the live
    # child must therefore exist and answer the same view as the rebuild. On an
    # empty tape both variants are trivially equal, which is the shape this pins.
    mod = check_module()
    forced = mod.fold_role(REPO, tmp_path, "senses", 30)
    live = mod.fold_role(REPO, tmp_path, "senses", 30, "live")
    assert live["entries"] == forced["entries"] == 0
    assert live["view_digest"] == forced["view_digest"]
    assert live["fold_cpu_s"] <= forced["fold_cpu_s"]


def test_cached_view_disagreeing_with_the_rebuild_is_red(tmp_path):
    # A role cache that serves fewer rows than the tape holds would hand a wake a
    # stale dossier. The digest comparison must name it and fail RED, not GREEN.
    repo = _rust_release(tmp_path)
    (repo / "src" / "mishe_tauftauf" / "feed.py").write_text(
        "class Feed:\n"
        "    def __init__(self, home): pass\n"
        "    def role_entries(self, role, _force_full=False, _fold_diagnostics=None):\n"
        "        return [1, 2, 3] if _force_full else [1, 2]\n")
    _commit(repo, "feed serving a stale cached view")
    home = tmp_path / "site"
    home.mkdir()
    _install_all(home, repo)
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(repo),
         "--roles", "senses", "--no-artifact", "--max-load", "100000",
         "--max-cpu-pressure", "100000", "--max-mem-pressure", "100000"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "cached role view" in proc.stderr and "senses" in proc.stderr


def test_cache_state_none_is_unknown_not_hit():
    # No tape: the feed never sets role_cache_miss, so the child emits null.
    # Null must not print as a cache hit.
    check = check_module()
    assert check._cache_state(None) == "UNKNOWN"
    assert check._cache_state(True) == "MISS"
    assert check._cache_state(False) == "hit"
