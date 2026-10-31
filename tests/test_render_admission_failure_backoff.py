"""Fail-first: a failed optional render must start a cooldown, not spawn again at once."""
from pathlib import Path

from mishe_tauftauf import render_admission


def _failing_probe(calls):
    def run_probe(command, *, timeout, env, output_limit, pass_fds):
        calls.append(command)
        return {"ok": False, "code": 124, "stdout": "", "stderr": "timed out", "seconds": timeout}
    return run_probe


def test_second_render_within_cooldown_defers_after_failure(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(render_admission, "identity", lambda role: None)
    monkeypatch.setattr(render_admission, "status",
                        lambda home, now=None: {"state": "LIMITED", "capacity": {"limited": True}, "render_slots": 1, "render_cooldown": 30})
    monkeypatch.setattr(render_admission, "run_probe", _failing_probe(calls))

    first = render_admission.render(tmp_path, "health", ["renderer"], now=1000.0)
    assert first["ok"] is False
    assert len(calls) == 1

    second = render_admission.render(tmp_path, "health", ["renderer"], now=1005.0)
    assert second["ok"] is None, "failure must open a cooldown; retry inside it must defer"
    assert "pressure cooldown" in second["stdout"]
    assert len(calls) == 1, "deferred render must not spawn the probe"


def test_render_after_failure_cooldown_probes_again(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(render_admission, "identity", lambda role: None)
    monkeypatch.setattr(render_admission, "status",
                        lambda home, now=None: {"state": "LIMITED", "capacity": {"limited": True}, "render_slots": 1, "render_cooldown": 30})
    monkeypatch.setattr(render_admission, "run_probe", _failing_probe(calls))

    render_admission.render(tmp_path, "health", ["renderer"], now=1000.0)
    render_admission.render(tmp_path, "health", ["renderer"], now=1000.0 + 31)
    assert len(calls) == 2, "after the failure cooldown elapses the probe may run again"


def test_failure_after_cached_success_still_opens_cooldown(tmp_path: Path, monkeypatch):
    calls = []
    outcomes = iter([{"ok": True, "code": 0, "stdout": "fresh\n", "stderr": "", "seconds": 0.1}])

    def run_probe(command, *, timeout, env, output_limit, pass_fds):
        calls.append(command)
        try:
            return next(outcomes)
        except StopIteration:
            return {"ok": False, "code": 124, "stdout": "", "stderr": "timed out", "seconds": timeout}

    monkeypatch.setattr(render_admission, "identity", lambda role: None)
    monkeypatch.setattr(render_admission, "status",
                        lambda home, now=None: {"state": "LIMITED", "capacity": {"limited": True}, "render_slots": 1, "render_cooldown": 30})
    monkeypatch.setattr(render_admission, "run_probe", run_probe)

    success = render_admission.render(tmp_path, "health", ["renderer"], now=1000.0)
    assert success["ok"] is True
    failure = render_admission.render(tmp_path, "health", ["renderer"], now=1040.0)
    assert failure["ok"] is False
    assert len(calls) == 2

    retry = render_admission.render(tmp_path, "health", ["renderer"], now=1045.0)
    assert retry["ok"] is None, "failure after a cached success must still start the cooldown"
    assert "pressure cooldown" in retry["stdout"]
    assert "fresh" in retry["stdout"], "retained cached success is shown as UNKNOWN content, not fresh"
    assert len(calls) == 2
