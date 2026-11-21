import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("experiments_lifecycle", REPO / "checks" / "experiments-lifecycle.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

DAY = 86400


def _repo(tmp_path, experiments, tracked=()):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "experiments.json").write_text(json.dumps({"experiments": experiments}), encoding="utf-8")
    for name in tracked:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def _entry(**over):
    entry = {"id": "x", "owner": "steward", "started": "2026-11-01", "decision_due": "2026-11-10",
             "status": "running", "decision": "", "consumer_or_evidence": "", "paths": []}
    entry.update(over)
    return entry


def _due(text):
    return check.due_epoch(text)


def test_retired_and_adopted_entries_are_green(tmp_path, capsys):
    repo = _repo(tmp_path, [_entry(status="retired"), _entry(id="y", status="adopted")])
    assert check.main(["--repo", str(repo), "--now", str(_due("2026-12-01"))]) == 0
    assert "GREEN" in capsys.readouterr().out


def test_running_past_decision_date_is_amber_then_red_after_a_week(tmp_path, capsys):
    repo = _repo(tmp_path, [_entry()])
    assert check.main(["--repo", str(repo), "--now", str(_due("2026-11-12"))]) == 0
    assert "AMBER" in capsys.readouterr().out
    assert check.main(["--repo", str(repo), "--now", str(_due("2026-11-18"))]) == 2
    assert "RED" in capsys.readouterr().err


def test_running_before_decision_date_is_green(tmp_path):
    repo = _repo(tmp_path, [_entry()])
    assert check.main(["--repo", str(repo), "--now", str(_due("2026-11-05"))]) == 0


def test_unregistered_experiment_artifact_is_amber(tmp_path, capsys):
    repo = _repo(tmp_path, [_entry(status="retired")], tracked=["tools/pilot_scan.py", "docs/notes.md"])
    assert check.main(["--repo", str(repo), "--now", str(_due("2026-12-01"))]) == 0
    out = capsys.readouterr().out
    assert "AMBER" in out and "tools/pilot_scan.py" in out and "docs/notes.md" not in out


def test_registered_path_is_not_reported(tmp_path, capsys):
    repo = _repo(tmp_path, [_entry(status="adopted", paths=["tools/pilot_scan.py"])],
                 tracked=["tools/pilot_scan.py"])
    assert check.main(["--repo", str(repo), "--now", str(_due("2026-12-01"))]) == 0
    assert "GREEN" in capsys.readouterr().out


@pytest.mark.parametrize("body", ["not json", json.dumps({"experiments": "nope"}),
                                  json.dumps({"experiments": [_entry(status="paused")]})])
def test_invalid_registry_is_unknown(tmp_path, body):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "experiments.json").write_text(body, encoding="utf-8")
    assert check.main(["--repo", str(tmp_path)]) == 3


def test_missing_registry_is_unknown(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert check.main(["--repo", str(tmp_path)]) == 3


def test_committed_registry_passes_now():
    assert check.main(["--repo", str(REPO)]) == 0
