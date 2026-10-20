"""Readiness-gate and delegation-branch detector coverage.

The exercises run the real probe script against a stubbed ``tmux`` on PATH, so
the engine test, the dead-pane test and the busy-file test are observed through
the same subprocess boundary the site uses. The measured defect is pinned by
running the retired ``python3``-only gate beside the canonical one.
"""
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import native_gate
from mishe_tauftauf.feed import Feed

STUB_CASES = {
    "python-idle": {"probe": "0 python", "command": "python", "dead": "0"},
    "python3-idle": {"probe": "0 python3", "command": "python3", "dead": "0"},
    "python-versioned": {"probe": "0 python3.12", "command": "python3.12", "dead": "0"},
    "node": {"probe": "0 node", "command": "node", "dead": "0"},
    "dead-python": {"probe": "1 python", "command": "python", "dead": "1"},
    "omp": {"probe": "0 omp", "command": "omp", "dead": "0"},
}

STUB = """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
case = os.environ["STUB_CASE"]
if args[:1] == ["show-option"]:
    print(os.environ["STUB_HOME"])
    sys.exit(0)
if args[:1] == ["display-message"]:
    fields = args[-1]
    if "pane_dead" in fields and "pane_current_command" in fields:
        print(%(cases)r[case]["probe"])
    elif "pane_current_command" in fields:
        print(%(cases)r[case]["command"])
    elif "pane_dead" in fields:
        print(%(cases)r[case]["dead"])
    else:
        sys.exit(1)
    sys.exit(0)
sys.exit(1)
""" % {"cases": STUB_CASES}

# The retired probe: one engine literal. Kept here so the measured failure —
# a live native pane running ``python`` read not-ready — cannot silently return.
RETIRED_GATE = '''#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT = 2


def tmux(*args):
    return subprocess.run(("tmux", *args), capture_output=True, text=True, timeout=TIMEOUT)


def main() -> int:
    pane = os.environ.get("MISHE_SEED_PANE", "")
    role = os.environ.get("MISHE_SEED_ROLE", "")
    session = os.environ.get("MISHE_SEED_SESSION", "")
    if not pane or not role or not session:
        return 1
    owned = tmux("show-option", "-qv", "-t", session, "@mishe-tauftauf-home")
    if owned.returncode != 0 or not owned.stdout.strip():
        return 1
    home = Path(owned.stdout.strip())
    command = tmux("display-message", "-p", "-t", pane, "#{pane_current_command}")
    if command.returncode != 0 or command.stdout.strip() != "python3":
        return 1
    dead = tmux("display-message", "-p", "-t", pane, "#{pane_dead}")
    if dead.returncode != 0 or dead.stdout.strip() == "1":
        return 1
    if (home / "journals" / f"{role}-native.busy").exists():
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def make_home(tmp_path):
    (tmp_path / "minds").mkdir()
    (tmp_path / "top-pains").mkdir()
    (tmp_path / "journals").mkdir()
    return tmp_path


def run_stub(tmp_path, monkeypatch, case):
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "tmux"
    stub.write_text(STUB, encoding="utf-8")
    os.chmod(stub, 0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_CASE", case)
    monkeypatch.setenv("STUB_HOME", str(tmp_path))


def run_gate(gate: Path, tmp_path, monkeypatch, case, role="health"):
    run_stub(tmp_path, monkeypatch, case)
    env = {**os.environ, "MISHE_SEED_SESSION": "s", "MISHE_SEED_ROLE": role,
           "MISHE_SEED_PANE": f"s:{role}.1"}
    return subprocess.run([sys.executable, str(gate)], env=env, capture_output=True, text=True, timeout=10)


def append_wake(home, role="genome", body="seed wake {role} observation=1\nOwed work.\n"):
    return Feed(home).append("seed", body.format(role=role))


def test_retired_gate_rejected_a_live_python_pane(tmp_path, monkeypatch):
    """The measured defect: the python3-only literal held a live native mind."""
    home = make_home(tmp_path)
    retired = home / "retired-gate"
    retired.write_text(RETIRED_GATE, encoding="utf-8")
    os.chmod(retired, 0o755)
    assert run_gate(retired, home, monkeypatch, "python-idle").returncode == 1
    assert run_gate(retired, home, monkeypatch, "python3-idle").returncode == 0


def test_canonical_gate_accepts_every_python_name(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    gate = native_gate.install(home, "health")
    assert gate.stat().st_mode & stat.S_IXUSR
    for case in ("python-idle", "python3-idle", "python-versioned"):
        assert run_gate(gate, home, monkeypatch, case).returncode == 0, case


def test_canonical_gate_still_refuses_other_states(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    gate = native_gate.install(home, "health")
    assert run_gate(gate, home, monkeypatch, "node").returncode == 1
    assert run_gate(gate, home, monkeypatch, "dead-python").returncode == 1
    (home / "journals" / "health-native.busy").write_text("busy")
    assert run_gate(gate, home, monkeypatch, "python-idle").returncode == 1


def test_install_is_idempotent(tmp_path):
    home = make_home(tmp_path)
    first = native_gate.install(home, "health")
    second = native_gate.install(home, "health")
    assert first == second and first.read_text(encoding="utf-8") == native_gate.GATE_SOURCE


def test_detect_reads_green_when_the_gate_accepts_the_pane(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    native_gate.install(home, "genome")
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home)
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["branch"] == "delegation" and verdict["verdict"] == "GREEN"


def test_detect_red_when_the_gate_rejects_a_live_native_pane(tmp_path, monkeypatch):
    """The detector's target: gate not-ready, no busy file, lifecycle live."""
    home = make_home(tmp_path)
    retired = home / "checks" / "mind-ready" / "genome"
    retired.parent.mkdir(parents=True)
    retired.write_text(RETIRED_GATE, encoding="utf-8")
    os.chmod(retired, 0o755)
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home)
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["verdict"] == "RED" and verdict["engine"] == "python"
    assert "lifecycle shows" in verdict["reason"]
    # The canonical probe closes the same reading: the false negative was the
    # engine literal, not the pane.
    native_gate.install(home, "genome")
    assert native_gate.detect(home, "s", "genome")["verdict"] == "GREEN"


def test_detect_ok_when_the_native_mind_is_mid_turn(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    native_gate.install(home, "genome")
    (home / "journals" / "genome-native.busy").write_text("busy")
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home)
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["verdict"] == "OK" and "mid-turn" in verdict["reason"]


def test_detect_ok_when_no_live_lifecycle_is_owed_work(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    native_gate.install(home, "genome")
    run_stub(home, monkeypatch, "node")
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["verdict"] == "OK" and "owes no work" in verdict["reason"]


def test_detect_red_when_the_delegation_branch_has_no_probe(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home)
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["verdict"] == "RED" and "no readiness probe" in verdict["reason"]


def test_detect_unknown_when_the_pane_probe_cannot_answer(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    native_gate.install(home, "genome")
    stub_dir = home / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "tmux"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(stub, 0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ['PATH']}")
    append_wake(home)
    assert native_gate.detect(home, "s", "genome")["verdict"] == "UNKNOWN"


def test_detect_reports_the_tui_branch_without_the_gate(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    run_stub(home, monkeypatch, "omp")
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["branch"] == "tui" and verdict["verdict"] == "OK"
    assert not (home / "checks" / "mind-ready" / "genome").exists()


def test_record_persists_only_a_changed_verdict(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    native_gate.install(home, "genome")
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home)
    verdict = native_gate.detect(home, "s", "genome")
    assert native_gate.record(home, verdict) is not None
    assert native_gate.record(home, verdict) is None
    path = home / "checks" / "native-gate-genome.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["verdict"] == "GREEN" and stored["role"] == "genome"


def test_deliver_names_the_gate_branch_and_records_the_false_negative(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    home = make_home(tmp_path)
    retired = home / "checks" / "mind-ready" / "genome"
    retired.parent.mkdir(parents=True)
    retired.write_text(RETIRED_GATE, encoding="utf-8")
    os.chmod(retired, 0o755)
    run_stub(home, monkeypatch, "python-idle")
    wake = append_wake(home)
    held = wall.deliver(home, "s", "genome", wake.sequence, 1, "trigger")
    assert "native gate not-ready" in held and "checks/mind-ready/genome" in held
    stored = json.loads((home / "checks" / "native-gate-genome.json").read_text(encoding="utf-8"))
    assert stored["verdict"] == "RED" and stored["engine"] == "python"
    # The canonical probe repairs the same delivery without touching the tape.
    native_gate.install(home, "genome")
    stored = json.loads((home / "checks" / "native-gate-genome.json").read_text(encoding="utf-8"))
    assert stored["verdict"] == "RED"


def test_deliver_keeps_the_tui_busy_line(tmp_path, monkeypatch):
    from mishe_tauftauf import seed, wall
    home = make_home(tmp_path)
    wake = append_wake(home)
    monkeypatch.setattr(seed, "_mind_ready", lambda *args: False)
    held = wall.deliver(home, "s", "genome", wake.sequence, 1, "trigger")
    assert "mind busy" in held


def test_lines_render_the_evidence(tmp_path):
    verdict = {"verdict": "RED", "reason": "probe rejects the python pane",
               "engine": "python", "branch": "delegation", "gate_exit": 1,
               "busy": False, "lifecycle": "wake 7 at 2026-10-11T04:00:00Z", "lifecycle_age": 12.0}
    rendered = native_gate.lines(verdict)
    assert rendered[0].startswith("NATIVE GATE: RED")
    assert "engine=python" in rendered[1] and "busy=False" in rendered[1]


def test_detect_red_when_the_busy_marker_is_stale(tmp_path, monkeypatch):
    """A marker a failed wake left behind is not a mid-turn."""
    home = make_home(tmp_path)
    native_gate.install(home, "genome")
    marker = home / "journals" / "genome-native.busy"
    marker.write_text("70040")
    stale = time.time() - (native_gate.LIVE_WINDOW_SECONDS + 3600)
    os.utime(marker, (stale, stale))
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home)
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["verdict"] == "RED" and "wedged" in verdict["reason"]
    assert verdict["busy_age"] > native_gate.LIVE_WINDOW_SECONDS


def test_detect_red_when_the_resident_holds_a_wake_it_cannot_start(tmp_path, monkeypatch):
    """A held wake is visible even when no busy file exists at all."""
    from mishe_tauftauf import native_recovery
    home = make_home(tmp_path)
    native_gate.install(home, "docs")
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home, role="docs")
    native_recovery.write_blocked_state(home, "docs", 70477, verdict=native_recovery.RECONCILE,
                                        reason="an earlier attempt dispatched an effect")
    verdict = native_gate.detect(home, "s", "docs")
    assert verdict["verdict"] == "RED" and "is held" in verdict["reason"]
    assert verdict["blocked"]["wake"] == 70477


def test_detect_unknown_when_the_blocked_state_cannot_be_decoded(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    native_gate.install(home, "docs")
    run_stub(home, monkeypatch, "python-idle")
    path = home / "journals" / "docs-native.blocked.json"
    path.write_text("{not json", encoding="utf-8")
    verdict = native_gate.detect(home, "s", "docs")
    assert verdict["verdict"] == "UNKNOWN" and "malformed" in verdict["reason"]


def test_probe_refuses_when_the_resident_holds_a_wake_it_cannot_start(tmp_path, monkeypatch):
    """A held wake must stop the delivery, not only the dashboard."""
    from mishe_tauftauf import native_recovery
    home = make_home(tmp_path)
    gate = native_gate.install(home, "docs")
    run_stub(home, monkeypatch, "python-idle")
    assert run_gate(gate, home, monkeypatch, "python-idle").returncode == 0
    native_recovery.write_blocked_state(home, "docs", 70477, verdict=native_recovery.RECONCILE,
                                        reason="an earlier attempt dispatched an effect")
    assert run_gate(gate, home, monkeypatch, "python-idle", role="docs").returncode == 1


def test_detect_unknown_when_the_busy_marker_age_is_unreadable(tmp_path, monkeypatch):
    """Broken marker metadata is UNKNOWN, not a pane that reads ready."""
    home = make_home(tmp_path)
    gate = home / "checks" / "mind-ready" / "genome"
    gate.parent.mkdir(parents=True)
    gate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(gate, 0o755)
    marker = home / "journals" / "genome-native.busy"
    marker.write_text("70040")
    monkeypatch.setattr(native_gate, "_marker_age", lambda *args: None)
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home)
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["verdict"] == "UNKNOWN" and "unreadable" in verdict["reason"]


def test_detect_unknown_when_the_busy_marker_timestamp_is_in_the_future(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    native_gate.install(home, "genome")
    marker = home / "journals" / "genome-native.busy"
    marker.write_text("70040")
    ahead = time.time() + 86400
    os.utime(marker, (ahead, ahead))
    run_stub(home, monkeypatch, "python-idle")
    append_wake(home)
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["verdict"] == "UNKNOWN" and "future" in verdict["reason"]


def test_detect_ok_when_a_foreign_engine_leaves_a_stale_marker(tmp_path, monkeypatch):
    """A marker a foreign engine left is not a native wedge."""
    home = make_home(tmp_path)
    native_gate.install(home, "genome")
    marker = home / "journals" / "genome-native.busy"
    marker.write_text("70040")
    stale = time.time() - (native_gate.LIVE_WINDOW_SECONDS + 3600)
    os.utime(marker, (stale, stale))
    run_stub(home, monkeypatch, "node")
    verdict = native_gate.detect(home, "s", "genome")
    assert verdict["verdict"] == "OK" and "not the native resident" in verdict["reason"]


def test_the_detector_and_the_probe_read_the_same_engines(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    gate = native_gate.install(home, "genome")
    for case, command in (("python-idle", "python"), ("python3-idle", "python3"),
                           ("python-versioned", "python3.12"), ("node", "node")):
        accepted = run_gate(gate, home, monkeypatch, case).returncode == 0
        assert accepted is native_gate.is_native_engine(command), case
