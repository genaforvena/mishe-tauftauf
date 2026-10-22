"""The Pub DEV watch timer must be asked whether it will fire again.

``checks/pub-devto-watch-timer.py`` answers a question the Pub role's only
reader observation depends on: after a reboot the five-minute DEV watch timer
was ``active`` with no elapse scheduled, so it never fired again, the scan
aged out to UNKNOWN, and the top pane still printed ``DEV WATCH TIMER:
GREEN`` because it had asked ``systemctl --user is-active`` and nothing else.

These tests pin the detector's decisions on fixtures, so the same assertions
hold in CI and on the plant:

* a unit that declares only boot-relative and activation-relative anchors is
  RED on its own, because both anchors can already be past;
* a unit that declares a calendar anchor and a timer with a scheduled elapse
  is GREEN;
* the same unit with the timer in the state measured after the reboot --
  active, ``elapsed``, no elapse -- is RED, and so is an inactive timer;
* a published dashboard frame that claims the watch is GREEN while the timer
  cannot fire again is RED: the false GREEN is named, not inherited;
* a missing unit and an unreadable timer state are UNKNOWN, never clean;
* the controlled failure: removing the scheduled elapse from the same state
  turns the GREEN verdict RED, so recurrence stays observable.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "pub-devto-watch-timer.py"

TIMER = "mishe-self-development-current-pub-devto-watch.timer"

UNIT_OLD = """[Unit]
Description=Observe Mishe Pub DEV threads every five minutes

[Timer]
OnBootSec=30
OnUnitActiveSec=5min
AccuracySec=10s
Persistent=true
Unit=mishe-self-development-current-pub-devto-watch.service
"""

UNIT_CALENDAR = """[Unit]
Description=Observe Mishe Pub DEV threads every five minutes

[Timer]
OnBootSec=30
OnCalendar=*:0/5
AccuracySec=10s
Persistent=true
Unit=mishe-self-development-current-pub-devto-watch.service
"""

STATE_DEAD = ("ActiveState=active\n"
              "SubState=elapsed\n"
              "NextElapseUSecRealtime=\n"
              "NextElapseUSecMonotonic=infinity\n")

STATE_SCHEDULED = ("ActiveState=active\n"
                   "SubState=waiting\n"
                   "NextElapseUSecRealtime=Thu 2026-10-22 12:15:00 UTC\n"
                   "NextElapseUSecMonotonic=0\n")

STATE_INACTIVE = ("ActiveState=inactive\n"
                  "SubState=dead\n"
                  "NextElapseUSecRealtime=\n"
                  "NextElapseUSecMonotonic=infinity\n")


def load():
    """The detector module, loaded from the checks lane."""
    spec = importlib.util.spec_from_file_location("pub_devto_watch_timer", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def home_with(tmp_path: Path, unit: str | None = UNIT_CALENDAR, frame: str = "") -> Path:
    """A plant home carrying the installed unit and the published frame."""
    if unit is not None:
        path = tmp_path / "pub" / "systemd" / TIMER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(unit)
    if frame:
        path = tmp_path / "dashboards" / "pub.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "slug": "pub", "frame": frame}))
    return tmp_path


def run(home: Path, state: str | None) -> subprocess.CompletedProcess:
    """Run the check against a home and a captured timer state."""
    args = [sys.executable, str(CHECK), "--home", str(home)]
    if state is not None:
        captured = home / "timer-state.txt"
        captured.write_text(state)
        args += ["--timer-state", str(captured)]
    return subprocess.run(args, capture_output=True, text=True)


def test_a_unit_with_only_past_anchors_is_red_on_its_own(tmp_path):
    """Both anchors can already be past, so the shape alone is the fault."""
    check = load()
    state, reason = check.verdict(check.parse_timer_unit(UNIT_OLD), check.parse_show(STATE_SCHEDULED))
    assert state == "RED"
    assert "OnBootSec" in reason and "OnUnitActiveSec" in reason


def test_a_calendar_anchor_with_a_scheduled_elapse_is_green(tmp_path):
    """The repaired unit, with a timer that will fire again."""
    check = load()
    state, _ = check.verdict(check.parse_timer_unit(UNIT_CALENDAR), check.parse_show(STATE_SCHEDULED))
    assert state == "GREEN"


def test_the_state_measured_after_the_reboot_is_red(tmp_path):
    """Active, elapsed, no elapse: alive and never firing again."""
    check = load()
    state, reason = check.verdict(check.parse_timer_unit(UNIT_CALENDAR), check.parse_show(STATE_DEAD))
    assert state == "RED"
    assert "will not" in reason


def test_an_inactive_timer_is_red(tmp_path):
    """A timer that is not running is not watching."""
    check = load()
    state, _ = check.verdict(check.parse_timer_unit(UNIT_CALENDAR), check.parse_show(STATE_INACTIVE))
    assert state == "RED"


def test_a_dashboard_that_claims_green_over_a_dead_timer_is_red(tmp_path):
    """The false GREEN is named, not inherited from the pane."""
    check = load()
    frame = "DEV WATCH TIMER: GREEN — active, five-minute read-only scans\n"
    state, reason = check.verdict(check.parse_timer_unit(UNIT_CALENDAR), check.parse_show(STATE_DEAD), frame)
    assert state == "RED"


def test_a_missing_unit_and_an_unreadable_state_are_unknown(tmp_path):
    """Absence is UNKNOWN, never clean."""
    check = load()
    assert check.verdict(None, check.parse_show(STATE_SCHEDULED))[0] == "UNKNOWN"
    assert check.verdict(check.parse_timer_unit(UNIT_CALENDAR), None)[0] == "UNKNOWN"


def test_removing_the_scheduled_elapse_turns_green_red(tmp_path):
    """The controlled failure: the same state, minus the elapse, is caught."""
    check = load()
    scheduled = check.parse_show(STATE_SCHEDULED)
    dead = check.parse_show(STATE_DEAD)
    assert check.verdict(check.parse_timer_unit(UNIT_CALENDAR), scheduled)[0] == "GREEN"
    assert check.verdict(check.parse_timer_unit(UNIT_CALENDAR), dead)[0] == "RED"


def test_the_exit_status_carries_the_verdict(tmp_path):
    """0 clean, 2 broken, 3 undecidable."""
    home = home_with(tmp_path, UNIT_CALENDAR)
    assert run(home, STATE_SCHEDULED).returncode == 0
    assert run(home, STATE_DEAD).returncode == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(empty, STATE_SCHEDULED).returncode == 3
