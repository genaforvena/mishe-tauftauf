"""guard install / restore / revert: every gate stays, nothing runs out of order."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mishe_tauftauf import guard_cli


@pytest.fixture
def world(tmp_path, monkeypatch):
    calls, state = [], {"latched": True, "rc": {}}
    home = tmp_path / "site"
    home.mkdir()

    def run(argv, *, sudo=False):
        text = " ".join(str(a) for a in argv)
        calls.append(("sudo " if sudo else "") + text)
        for needle, rc in state["rc"].items():
            if needle in text:
                return rc
        if "start" in text and guard_cli.RESTORE_UNIT in text:
            state["latched"] = False
        return 0

    monkeypatch.setattr(guard_cli, "_run", run)
    monkeypatch.setattr(guard_cli, "latched", lambda: state["latched"])
    monkeypatch.setattr(guard_cli.time, "sleep", lambda s: None)
    return home, calls, state


def go(home, command):
    return guard_cli.cmd_guard(SimpleNamespace(home=str(home), guard_command=command))


def receipts(home):
    return [json.loads(l) for l in (home / "operator" / "guard-actions.jsonl").read_text().splitlines()]


def test_install_order_and_receipt(world):
    home, calls, _ = world
    assert go(home, "install") == 0
    assert "apply.py" in calls[0] and calls[0].startswith("sudo ") and "revert" not in calls[0]
    assert "restart mishe-containment.service" in calls[1]
    assert "check-activation.py" in calls[2]
    assert receipts(home)[-1]["action"] == "install" and receipts(home)[-1]["exit"] == 0


def test_revert_passes_revert(world):
    home, calls, _ = world
    assert go(home, "revert") == 0 and "apply.py revert" in calls[0]


def test_install_refuses_when_plant_running_and_touches_nothing(world):
    home, calls, state = world
    state["latched"] = False
    assert go(home, "install") == 2 and calls == []
    assert receipts(home)[-1]["exit"] == 2


def test_apply_refusal_stops_before_sentinel_restart(world):
    home, calls, state = world
    state["rc"]["apply.py"] = 1
    assert go(home, "install") == 1
    assert not any("restart" in c for c in calls)


def test_activation_not_proven_is_a_failure(world):
    home, calls, state = world
    state["rc"]["check-activation.py"] = 1
    assert go(home, "install") == 1


def test_restore_refused_until_readiness_passes(world):
    home, calls, state = world
    state["rc"]["restore-readiness.py"] = 2
    assert go(home, "restore") == 2
    assert not any("start" in c for c in calls) and state["latched"]


def test_restore_starts_unit_and_waits_for_latch_to_clear(world):
    home, calls, state = world
    assert go(home, "restore") == 0
    assert "restore-readiness.py" in calls[0] and "--user start" in calls[1]
    assert not state["latched"]


def test_restore_noop_when_not_latched(world):
    home, calls, state = world
    state["latched"] = False
    assert go(home, "restore") == 0 and calls == []


def test_restore_reports_unit_that_does_not_clear_latch(world, monkeypatch):
    home, calls, state = world
    monkeypatch.setattr(guard_cli, "RESTORE_WAIT", 0)
    state["rc"]["--user start"] = 0
    monkeypatch.setattr(guard_cli, "latched", lambda: True)
    assert go(home, "restore") == 1
