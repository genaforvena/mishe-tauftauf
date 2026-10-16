from pathlib import Path
import subprocess

import pytest

from mishe_tauftauf import session_runner as runner


def backend(monkeypatch, *, available=False, delay=0, failure=False):
    state = {"available": available, "checks": 0, "raises": [], "pending": False}
    def run(argv, **kwargs):
        if argv[0] == "tmux":
            state["checks"] += 1
            if state["pending"] and state["checks"] > delay and not failure:
                state["available"] = True
                state["pending"] = False
            return subprocess.CompletedProcess(argv, 0 if state["available"] else 1)
        state["raises"].append(argv)
        state["pending"] = True
        return subprocess.CompletedProcess(argv, 1 if failure else 0)
    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    return state


def test_waits_for_delayed_session_before_reporting_success(tmp_path, monkeypatch):
    state = backend(monkeypatch, delay=4)
    runner.raise_session("owned", "health", str(tmp_path), tmp_path, timeout=1)
    assert state["checks"] >= 5
    raises = [argv for argv in state["raises"] if "systemd-run" in argv[0]]
    assert len(raises) == 1
    assert "--scope" not in raises[0]
    assert "&& exec sleep infinity" in raises[0][-1]


def test_reuses_existing_session_without_touching_keeper(tmp_path, monkeypatch):
    state = backend(monkeypatch, available=True)
    runner.raise_session("owned", "health", str(tmp_path), tmp_path)
    assert state["raises"] == []


def test_missing_session_gets_fresh_unit_even_if_previous_keeper_remains(tmp_path, monkeypatch):
    state = backend(monkeypatch)
    runner.raise_session("owned", "health", str(tmp_path), tmp_path)
    state["available"] = False
    state["pending"] = False
    runner.raise_session("owned", "health", str(tmp_path), tmp_path)
    units = [argv[argv.index("--unit") + 1] for argv in state["raises"] if "--unit" in argv]
    assert len(units) == 2 and units[0] != units[1]
    assert all(unit.startswith("owned-session-") and unit.endswith(".service") for unit in units)

def test_superseded_unit_stopped_after_replacement_confirmed(tmp_path, monkeypatch):
    state = {"available": False, "checks": 0, "raises": [], "pending": False, "list_output": ""}
    def run(argv, **kwargs):
        if argv[0] == "tmux":
            state["checks"] += 1
            if state["pending"] and state["checks"] > 0:
                state["available"] = True
                state["pending"] = False
            return subprocess.CompletedProcess(argv, 0 if state["available"] else 1)
        if argv[0] == "systemctl" and "list-units" in argv:
            state["raises"].append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout=state["list_output"])
        state["raises"].append(argv)
        state["pending"] = True
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    runner.raise_session("owned", "health", str(tmp_path), tmp_path)
    first_unit = state["raises"][0][state["raises"][0].index("--unit") + 1]
    state["list_output"] = f"  {first_unit}  loaded active running  test\n"
    state["available"] = False
    state["pending"] = False
    state["checks"] = 0
    runner.raise_session("owned", "health", str(tmp_path), tmp_path)
    stops = [argv for argv in state["raises"] if argv[0] == "systemctl" and "stop" in argv]
    assert len(stops) == 1
    assert first_unit in stops[0]



def test_start_failure_stays_visible(tmp_path, monkeypatch):
    backend(monkeypatch, failure=True)
    with pytest.raises(RuntimeError, match="UNKNOWN.*startup"):
        runner.raise_session("owned", "health", str(tmp_path), tmp_path, timeout=.01)


def test_successful_systemd_without_session_is_unknown(tmp_path, monkeypatch):
    state = backend(monkeypatch, delay=100000)
    with pytest.raises(RuntimeError, match="UNKNOWN.*session.*deadline"):
        runner.raise_session("owned", "health", str(tmp_path), tmp_path, timeout=.001)
    assert len(state["raises"]) == 1


@pytest.mark.parametrize("session", ["bad/name", "-option", "", "name.service; rm"])
def test_rejects_invalid_identity_before_start(tmp_path, monkeypatch, session):
    state = backend(monkeypatch)
    with pytest.raises(ValueError):
        runner.raise_session(session, "health", str(tmp_path), tmp_path)
    assert state["raises"] == []


def test_cli_never_reports_success_for_failed_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOG", str(tmp_path / "runner.log"), raising=False)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1))
    monkeypatch.setenv("MISHE_SESSION_LOCK_DIR", str(tmp_path))
    assert runner.main(["--user", "--scope", "--collect", "--unit", "owned-session.scope",
                        "--description", "test", "tmux", "new-session", "-d", "-s", "owned",
                        "-n", "health", "-c", str(tmp_path), "sh"]) == 1


def test_seed_custom_adapter_success_waits_for_actual_session(monkeypatch):
    from mishe_tauftauf import seed
    from mishe_tauftauf.tmux import TmuxError
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv("MISHE_SESSION_RUNNER", "/custom/session-runner")
    monkeypatch.setattr(seed.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(seed, "_await_session", lambda *a: False)
    with pytest.raises(TmuxError, match="UNKNOWN.*deadline"):
        seed._new_session("owned", "health", "/workspace")


def test_native_runner_honors_explicit_executable(tmp_path,monkeypatch):
    state=backend(monkeypatch)
    runner.raise_session("owned","health",str(tmp_path),tmp_path,
                         systemd_runner="/custom/systemd-run")
    assert state["raises"][0][0]=="/custom/systemd-run"
