"""Controlled-failure tests for checks/hold-state.py (a producer: no wake runs it).

The 2026-11-17 leak: a bare manual hold suppressed every wake for 2 h 40 min and no signal named it.
These tests exercise each verdict the detector can give, including the one after the hold is gone.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[1] / "checks" / "hold-state.py"
spec = importlib.util.spec_from_file_location("hold_state", CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

NOW = 100_000.0
BOUND = 3600.0


def _hold(home: Path, text: str, written_at: float = NOW - 10.0) -> Path:
    path = home / "hold"
    path.write_text(text + "\n" if not text.endswith("\n") else text, encoding="utf-8")
    os.utime(path, (written_at, written_at))
    return path


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_a_plant_with_no_hold_is_green(tmp_path):
    code, lines = check.evaluate(tmp_path, NOW, BOUND)
    assert code == 0 and "no hold" in lines[0]


def test_a_promotion_hold_held_by_a_live_writer_is_green_however_old(tmp_path):
    _hold(tmp_path, "promotion: short activation hold; wakes resume automatically\npid=%d started=1"
          % os.getpid(), written_at=NOW - 10 * BOUND)
    code, lines = check.evaluate(tmp_path, NOW, BOUND)
    assert code == 0 and "live pid" in lines[0]


def test_a_hold_naming_a_dead_pid_is_red_at_once(tmp_path):
    _hold(tmp_path, "promotion: short activation hold; wakes resume automatically\npid=%d started=1"
          % _dead_pid())
    code, lines = check.evaluate(tmp_path, NOW, BOUND)
    assert code == 2 and "which is gone" in lines[0]


def test_a_flip_token_names_its_writer_too(tmp_path):
    _hold(tmp_path, "root migration flip %d" % os.getpid(), written_at=NOW - 2 * BOUND)
    assert check.evaluate(tmp_path, NOW, BOUND)[0] == 0
    _hold(tmp_path, "root migration flip %d" % _dead_pid())
    assert check.evaluate(tmp_path, NOW, BOUND)[0] == 2


def test_an_anonymous_hold_inside_the_bound_is_green_and_past_it_is_red(tmp_path):
    _hold(tmp_path, "operator: migration window 16:00-16:10", written_at=NOW - BOUND / 2)
    assert check.evaluate(tmp_path, NOW, BOUND)[0] == 0
    _hold(tmp_path, "operator: migration window 16:00-16:10", written_at=NOW - BOUND - 60)
    code, lines = check.evaluate(tmp_path, NOW, BOUND)
    assert code == 2 and "without a refresh" in lines[0] and "migration window" in lines[0]


def test_a_stale_hold_that_ended_without_a_disposition_is_red_once_after_it_is_gone(tmp_path):
    hold = _hold(tmp_path, "held", written_at=NOW - 3 * BOUND)
    assert check.evaluate(tmp_path, NOW, BOUND)[0] == 2       # while it stands
    hold.unlink()
    code, lines = check.evaluate(tmp_path, NOW + 60, BOUND)
    assert code == 2 and "no recorded disposition" in lines[0] and "'held'" in lines[0]
    assert check.evaluate(tmp_path, NOW + 120, BOUND)[0] == 0  # reported once, then quiet


def test_a_refreshed_hold_does_not_carry_the_old_episode(tmp_path):
    hold = _hold(tmp_path, "operator: long window", written_at=NOW - 3 * BOUND)
    assert check.evaluate(tmp_path, NOW, BOUND)[0] == 2
    os.utime(hold, (NOW, NOW))                                 # `mishe hold on` again: the writer confirms it
    assert check.evaluate(tmp_path, NOW + 10, BOUND)[0] == 0
    hold.unlink()
    assert check.evaluate(tmp_path, NOW + 20, BOUND)[0] == 0


def test_an_unreadable_state_is_unknown_not_green(tmp_path):
    state = tmp_path / "health" / "hold-state.json"
    state.parent.mkdir(parents=True)
    state.write_text("[1, 2]", encoding="utf-8")
    code, lines = check.evaluate(tmp_path, NOW, BOUND)
    assert code == 3 and lines[0].startswith("UNKNOWN:")


def test_main_reports_the_verdict_on_stdout_and_its_code(tmp_path):
    _hold(tmp_path, "held", written_at=NOW - 10 * BOUND)
    proc = subprocess.run([sys.executable, str(CHECK), "--home", str(tmp_path),
                           "--state", str(tmp_path / "health" / "hold-state.json")],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and "without a refresh" in proc.stdout
    assert json.loads((tmp_path / "health" / "hold-state.json").read_text())["stale"] is True


def _lock_free(monkeypatch, free=True):
    import types
    import mishe_tauftauf.release_promotion as rp
    monkeypatch.setattr(rp, "_dispatch_lock_free", lambda home: free)


def test_release_removes_a_stale_manual_hold_and_reports_red_once(tmp_path, monkeypatch):
    _lock_free(monkeypatch)
    hold = _hold(tmp_path, "operator maintenance", written_at=NOW - BOUND - 5)
    code, lines = check.evaluate(tmp_path, NOW, BOUND, release=True, tripped=tmp_path / "tripped")
    assert code == 2 and not hold.exists() and any("AUTO-RELEASED" in line for line in lines)
    code, lines = check.evaluate(tmp_path, NOW + 1, BOUND, release=True, tripped=tmp_path / "tripped")
    assert code == 0 and "no hold" in lines[0]


def test_release_removes_a_dead_writer_hold(tmp_path, monkeypatch):
    _lock_free(monkeypatch)
    hold = _hold(tmp_path, "promotion: short activation hold; wakes resume automatically\npid=%d started=1" % _dead_pid())
    code, lines = check.evaluate(tmp_path, NOW, BOUND, release=True, tripped=tmp_path / "tripped")
    assert code == 2 and not hold.exists()


def test_release_never_touches_a_fresh_manual_hold_or_a_live_writer(tmp_path, monkeypatch):
    _lock_free(monkeypatch)
    hold = _hold(tmp_path, "fresh manual", written_at=NOW - 60)
    assert check.evaluate(tmp_path, NOW, BOUND, release=True, tripped=tmp_path / "tripped")[0] == 0
    assert hold.exists()
    hold.unlink()
    hold = _hold(tmp_path, "promotion: short activation hold; wakes resume automatically\npid=%d started=1" % os.getpid(), written_at=NOW - BOUND - 500)
    assert check.evaluate(tmp_path, NOW, BOUND, release=True, tripped=tmp_path / "tripped")[0] == 0
    assert hold.exists()


def test_release_waits_for_a_free_lock_and_never_when_the_guard_is_tripped(tmp_path, monkeypatch):
    hold = _hold(tmp_path, "stale", written_at=NOW - BOUND - 5)
    _lock_free(monkeypatch, free=False)
    code, lines = check.evaluate(tmp_path, NOW, BOUND, release=True, tripped=tmp_path / "tripped")
    assert code == 2 and hold.exists() and any("not released yet" in line and "dispatch lock" in line for line in lines)
    _lock_free(monkeypatch, free=True)
    (tmp_path / "tripped").write_text("x")
    code, lines = check.evaluate(tmp_path, NOW + 5, BOUND, release=True, tripped=tmp_path / "tripped")
    assert code == 2 and hold.exists() and any("guard is tripped" in line for line in lines)


def test_without_release_flag_a_stale_hold_stays_red_as_before(tmp_path):
    hold = _hold(tmp_path, "stale", written_at=NOW - BOUND - 5)
    assert check.evaluate(tmp_path, NOW, BOUND)[0] == 2 and hold.exists()
