"""Gate preflight, real revert argv and dead-owner admission detection."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import delivery_requests as q, wall_patch
from mishe_tauftauf.state_files import write_json


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "repo"
    home = repo / ".site"
    home.mkdir(parents=True)
    (repo / "op").mkdir()
    (repo / "op/install.sh").write_text("exit 0\n")
    return repo, home


def test_preflight_refuses_missing_script_and_program_before_admission(checkout):
    repo, home = checkout
    with pytest.raises(ValueError, match="script not found"):
        wall_patch.preflight(home, {"activate": ["bash", "op/missing.sh", "x"]})
    with pytest.raises(ValueError, match="not on PATH"):
        wall_patch.preflight(home, {"observe": ["no-such-binary-for-preflight"]})
    with pytest.raises(ValueError, match="not an executable"):
        wall_patch.preflight(home, {"revert": [str(repo / "nope/tool"), "x"]})


def test_preflight_accepts_resolvable_argv(checkout):
    repo, home = checkout
    wall_patch.preflight(home, {
        "activate": ["bash", str(repo / "op/install.sh"), "abc"],
        "observe": ["env", "PYTHONPATH=src", sys.executable, "-c", "pass"],
        "revert_observe": ["bash", "op/install.sh"],
        "revert": ["bash", str(repo / "op/install.sh")],
    })


def test_admission_is_red_only_for_dead_owner_past_bound(checkout):
    _, home = checkout
    dead = subprocess.Popen(["true"]); dead.wait()
    path = q.admission_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    write_json(path, dict(id="req-1", token="t", pid=dead.pid, at=now - 3600))
    assert "dead owner" in q.admission_problem(home, now=now)
    write_json(path, dict(id="req-1", token="t", pid=dead.pid, at=now - 60))
    assert q.admission_problem(home, now=now) is None
    write_json(path, dict(id="req-1", token="t", pid=__import__("os").getpid(), at=now - 3600))
    assert q.admission_problem(home, now=now) is None
    path.unlink()
    assert q.admission_problem(home, now=now) is None


def test_revert_runs_plan_revert_argv_not_the_new_activation(checkout, monkeypatch):
    _, home = checkout
    (home / "patches").mkdir()
    ran = []
    monkeypatch.setattr(wall_patch, "run", lambda h, i, label, command: ran.append((label, command)) or {"code": 0, "command": command})
    record = dict(runtime=str(home), files={}, delivery_plan={"revert": ["bash", "old-pin.sh"]})
    wall_patch._revert(home, "gate-revert-1", record, ["bash", "new-pin.sh"])
    assert ran == [("revert", ["bash", "old-pin.sh"])]
    assert record["phase"] == "reverted"


def test_plan_without_revert_keeps_legacy_equality(checkout):
    repo, home = checkout
    plan = wall_patch.delivery_plan(home, ["true"], ["true"], ["true"])
    assert "revert" not in plan
    assert wall_patch.delivery_plan(home, ["true"], ["true"], ["true"], revert=["true"])["revert"] == ["true"]


def test_failed_revert_with_restored_bytes_is_recoverable_input(tmp_path, monkeypatch):
    """Recovery inputs: revert-failed phase plus restored scoped bytes. The
    probe decision itself is in consume and is exercised by the live re-drive."""
    home = tmp_path / "site"; home.mkdir(); (home / "patches").mkdir()
    runtime = tmp_path / "runtime"; (runtime / "src/mishe_tauftauf").mkdir(parents=True)
    deployed = runtime / "src/mishe_tauftauf/example.py"
    deployed.write_text("before\n")
    before = wall_patch.snapshot(deployed)
    deployed.write_text("after\n")
    after = wall_patch.snapshot(deployed)
    record = dict(runtime=str(runtime), files={"src/mishe_tauftauf/example.py": dict(runtime_before=before, after=after)},
                  phase="revert-failed", delivery_plan={"revert": ["/no/such/revert"]})
    monkeypatch.setattr(wall_patch, "run", lambda *a, **k: {"code": 127, "command": a[-1]})
    with pytest.raises(ValueError, match="revert activation failed"):
        wall_patch._revert(home, "gate-revert-2", record, ["bash", "new.sh"])
    assert record["phase"] == "revert-failed"
    assert wall_patch.snapshot(deployed) == before
    assert q._runtime_restored(record)
    assert q._runtime_restored(dict(record, files={"src/mishe_tauftauf/example.py": dict(runtime_before=after)})) is False
