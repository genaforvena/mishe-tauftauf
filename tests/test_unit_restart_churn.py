"""Drill for checks/unit-restart-churn.py: a unit restarting in a loop goes RED before the start limit.

Simulated, not live: a fake systemctl stands in for the host, so no real unit is touched.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

CHECK = Path(__file__).resolve().parent.parent / "checks" / "unit-restart-churn.py"


def _load():
    spec = importlib.util.spec_from_file_location("unit_restart_churn", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Host:
    def __init__(self):
        self.count = 0
        self.active = "active"

    def run(self, args):
        class R:
            returncode = 0
            stderr = ""
            stdout = f"ActiveState={self.active}\nNRestarts={self.count}\n"
        return R()


def test_steady_unit_stays_green_and_loop_goes_red():
    mod = _load()
    host = _Host()
    state, now = {}, 1000.0
    code, _, state = mod.evaluate(state, ["u.service"], now, host.run, 900, 3)
    assert code == 0  # baseline
    host.count = 1
    code, _, state = mod.evaluate(state, ["u.service"], now + 300, host.run, 900, 3)
    assert code == 0  # one restart in 5 min is not a loop
    host.count, host.active = 4, "activating"
    code, lines, state = mod.evaluate(state, ["u.service"], now + 600, host.run, 900, 3)
    assert code == 2, lines  # 4 restarts since baseline within 15 min


def test_window_slides_and_manual_reset_restarts_baseline():
    mod = _load()
    host = _Host()
    state, now = {}, 1000.0
    mod.evaluate(state, ["u.service"], now, host.run, 900, 3)
    host.count = 2
    _, _, state = mod.evaluate(state, ["u.service"], now + 100, host.run, 900, 3)
    host.count = 0  # stopped and started by hand: counter went down
    code, lines, state = mod.evaluate(state, ["u.service"], now + 200, host.run, 900, 3)
    assert code == 0, lines


def test_unreadable_unit_is_unknown():
    mod = _load()

    def broken(args):
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"
        return R()

    code, _, _ = mod.evaluate({}, ["u.service"], 1000.0, broken, 900, 3)
    assert code == 3


def test_failed_unit_is_red_even_with_frozen_counter():
    # A unit that exhausted its start limit stops restarting: the counter is flat, so the churn window
    # alone reads 0. The failed state must still be RED (2026-11-25 light-cycle-monitor, GREEN for 23 h).
    mod = _load()
    host = _Host()
    state, now = {}, 1000.0
    mod.evaluate(state, ["u.service"], now, host.run, 900, 3)
    host.count, host.active = 6, "failed"
    code, lines, state = mod.evaluate(state, ["u.service"], now + 1200, host.run, 900, 3)
    assert code == 2, lines
    assert any("failed after 6 restart" in line for line in lines), lines


def test_failed_unit_is_red_on_first_sample():
    mod = _load()
    host = _Host()
    host.count, host.active = 6, "failed"
    code, lines, _ = mod.evaluate({}, ["u.service"], 1000.0, host.run, 900, 3)
    assert code == 2, lines
