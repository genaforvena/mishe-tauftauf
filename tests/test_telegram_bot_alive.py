"""Tests for the Telegram bot liveness detector.

Each test names the mutation that would make it fail: a detector that trusts
'active (running)' without a fresh poll heartbeat, that reads RED as UNKNOWN,
or that restarts without a bound would all pass the others and fail here.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "telegram_bot_alive", ROOT / "checks" / "telegram-bot-alive.py"
)
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)

NOW = 10_000.0
ACTIVE = {"ActiveState": "active", "MainPID": "4242", "Result": "success"}
FRESH = {"ts": NOW - 20, "cycles": 40, "replies": 3, "username": "ilya_fake_bot"}


def test_unit_state_reads_the_four_systemd_properties():
    calls = []

    def fake(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout="ActiveState=active\nMainPID=4242\nResult=success\n"
                   "ActiveEnterTimestamp=Mon 2026-11-16 17:00:00 UTC\n",
            stderr="",
        )

    state = check.unit_state("ilya-telegram-bot.service", runner=fake)
    assert state["ActiveState"] == "active" and state["MainPID"] == "4242"
    assert calls[0][:3] == ["systemctl", "--user", "show"]
    assert calls[0].count("-p") == len(check.SHOW_PROPS)


def test_active_unit_with_fresh_heartbeat_is_green():
    assert check.evaluate(ACTIVE, FRESH, NOW)[0] == 0


def test_dead_unit_is_red_even_with_a_fresh_heartbeat():
    dead = dict(ACTIVE, ActiveState="failed", Result="exit-code")
    assert check.evaluate(dead, FRESH, NOW)[0] == 2


def test_stale_heartbeat_is_red_when_the_unit_still_says_active():
    stale = dict(FRESH, ts=NOW - check.MAX_HEARTBEAT_AGE - 1)
    assert check.evaluate(ACTIVE, stale, NOW)[0] == 2


def test_missing_heartbeat_is_unknown_not_green():
    assert check.evaluate(ACTIVE, None, NOW)[0] == 3

def test_fresh_startup_heartbeat_with_zero_cycles_is_green():
    # The first getUpdates call blocks up to 10 s; a duty running in that window
    # must not restart a healthy bot just because no cycle has finished yet.
    assert check.evaluate(ACTIVE, dict(FRESH, cycles=0), NOW)[0] == 0


def test_restart_budget_is_bounded_and_windowed():
    heal = {"restarts": [NOW - 10, NOW - 20, NOW - 3600]}
    assert check.restart_budget(heal, NOW) == 0
    heal = {"restarts": [NOW - 20]}
    assert check.restart_budget(heal, NOW) == 1
    assert check.restart_budget({"restarts": []}, NOW) == check.RESTART_MAX


def test_red_triggers_one_restart_and_green_after(monkeypatch, tmp_path):
    """A dead poll loop is repaired once; the fresh heartbeat proves it."""
    state_calls = []
    heartbeats = iter([dict(FRESH, ts=NOW - 500), FRESH])
    restarts = []

    monkeypatch.setattr(check, "unit_state",
                        lambda unit=check.UNIT: state_calls.append(unit) or ACTIVE)
    monkeypatch.setattr(check, "read_heartbeat", lambda path: next(heartbeats))
    monkeypatch.setattr(check, "time", SimpleNamespace(
        time=lambda: NOW, sleep=lambda seconds: None))
    monkeypatch.setattr(check, "subprocess", SimpleNamespace(
        run=lambda *a, **kw: restarts.append(a[0]) or SimpleNamespace(
            returncode=0, stdout="", stderr=""),
        SubprocessError=subprocess.SubprocessError))
    monkeypatch.setattr(sys, "argv", [
        "telegram-bot-alive.py", "--home", str(tmp_path),
    ])

    assert check.main() == 0
    assert len(restarts) == 1 and restarts[0][:3] == ["systemctl", "--user", "restart"]
    heal = check.read_heal(tmp_path / "health" / "ilya-telegram-bot-heal.json")
    assert len(heal["restarts"]) == 1


def test_unknown_never_restarts(monkeypatch, tmp_path):
    restarts = []
    monkeypatch.setattr(check, "unit_state", lambda unit=check.UNIT: ACTIVE)
    monkeypatch.setattr(check, "read_heartbeat", lambda path: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(check, "subprocess", SimpleNamespace(
        run=lambda *a, **kw: restarts.append(a[0]),
        SubprocessError=subprocess.SubprocessError))
    monkeypatch.setattr(sys, "argv", ["telegram-bot-alive.py", "--home", str(tmp_path)])

    assert check.main() == 3
    assert restarts == []


def test_restart_budget_spent_leaves_red(monkeypatch, tmp_path):
    restarts = []
    heal_path = tmp_path / "health" / "ilya-telegram-bot-heal.json"
    heal_path.parent.mkdir(parents=True)
    heal_path.write_text('{"restarts": [%s, %s]}' % (NOW, NOW - 10))
    monkeypatch.setattr(check, "unit_state", lambda unit=check.UNIT: ACTIVE)
    monkeypatch.setattr(check, "read_heartbeat", lambda path: dict(FRESH, ts=NOW - 500))
    monkeypatch.setattr(check, "time", SimpleNamespace(
        time=lambda: NOW, sleep=lambda seconds: None))
    monkeypatch.setattr(check, "subprocess", SimpleNamespace(
        run=lambda *a, **kw: restarts.append(a[0]),
        SubprocessError=subprocess.SubprocessError))
    monkeypatch.setattr(sys, "argv", ["telegram-bot-alive.py", "--home", str(tmp_path)])

    assert check.main() == 2
    assert restarts == []
