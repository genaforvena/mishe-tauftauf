"""Controlled-failure tests for checks/initiative.py (stdlib only, run from the committed check file)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[1] / "checks" / "initiative.py"
spec = importlib.util.spec_from_file_location("initiative", CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

DUE = "2026-11-20T12:00:00Z"
DUE_TS = check._parse_due(DUE)


def _register(home: Path, acceptance, due=DUE, owner="steward", escalate="genome"):
    home.mkdir(parents=True, exist_ok=True)
    entry = {"id": "pin-at-head", "owner": owner, "goal": "pin delivered at HEAD", "due": due,
             "escalate": escalate, "acceptance": acceptance}
    (home / "initiatives.json").write_text(json.dumps({"initiatives": [entry]}), encoding="utf-8")


def _exit_with(code: int):
    # argv that exits with the given status, no shell
    return [sys.executable, "-c", f"import sys; sys.exit({code})"]


def test_done_is_green(tmp_path):
    _register(tmp_path / "home", _exit_with(0))
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS + 10_000)
    assert code == 0 and "done" in msg


def test_open_before_due_is_green(tmp_path):
    _register(tmp_path / "home", _exit_with(1))
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS - 3600)
    assert code == 0 and "open" in msg


def test_open_past_due_is_red_and_names_owner_and_escalation(tmp_path):
    _register(tmp_path / "home", _exit_with(1))
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS + 600)
    assert code == 2
    assert "owner steward" in msg and "escalate genome" in msg and "10 min past due" in msg


def test_unknown_exit_status_is_unknown_not_green(tmp_path):
    _register(tmp_path / "home", _exit_with(7))
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS + 600)
    assert code == 3 and "exited 7" in msg


def test_missing_program_is_unknown(tmp_path):
    _register(tmp_path / "home", ["/nonexistent/definitely-not-a-program"])
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS - 1)
    assert code == 3 and "cannot run" in msg


def test_missing_register_is_unknown(tmp_path):
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS)
    assert code == 3 and "no register" in msg


def test_unregistered_id_is_unknown(tmp_path):
    _register(tmp_path / "home", _exit_with(0))
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "other-id", DUE_TS)
    assert code == 3 and "not registered" in msg


def test_bad_due_is_unknown(tmp_path):
    _register(tmp_path / "home", _exit_with(1), due="next tuesday")
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS)
    assert code == 3 and "due" in msg


def test_acceptance_not_argv_is_unknown(tmp_path):
    _register(tmp_path / "home", "echo done")
    code, msg = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS)
    assert code == 3 and "argv" in msg


def test_home_and_repo_tokens_substituted(tmp_path):
    marker = tmp_path / "home" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("x")
    # exit 0 only if the {home} token resolved to the real site home
    probe = [sys.executable, "-c",
             "import os,sys; sys.exit(0 if os.path.exists(sys.argv[1]) else 1)", "{home}/marker.txt"]
    _register(tmp_path / "home", probe)
    code, _ = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS - 1)
    assert code == 0


def test_python_token_is_the_running_interpreter(tmp_path):
    _register(tmp_path / "home", ["{python}", "-c", "import sys; sys.exit(0)"])
    code, _ = check.evaluate(tmp_path / "home", tmp_path, "pin-at-head", DUE_TS - 1)
    assert code == 0


def test_cli_prints_green_to_stdout_and_red_to_stderr(tmp_path, capsys, monkeypatch):
    _register(tmp_path / "home", _exit_with(1))
    monkeypatch.setattr(check.time, "time", lambda: DUE_TS + 60)
    monkeypatch.setattr(sys, "argv", ["initiative.py", "--home", str(tmp_path / "home"),
                                     "--repo", str(tmp_path), "--id", "pin-at-head"])
    assert check.main() == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "RED" in captured.err
