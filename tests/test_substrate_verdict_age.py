"""The substrate verdict-age detector must age a bad reading, route it, and
never read broken monitoring as healthy.

Health's rule (DM 74917) has no detector until now. These cases are the
controlled failures the rule depends on:

* a bad reading that has persisted past the bound is a routed blocker, and
  the output names the onset, so a resolver can act on the evidence;
* a bad reading younger than the bound is not a blocker;
* a missing, unreadable or stale heartbeat is UNKNOWN, never healthy — the
  rule must not become a way of reporting a broken plant as fine;
* the age is measured in wall-clock time, not in observer intervals, because
  the immutable archive holds one event per change, not one per tick.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "substrate-verdict-age.py"
NOW = datetime(2026, 10, 20, 22, 0, 0, tzinfo=timezone.utc)


def load_check():
    """Import the check script, whose filename is not an importable module."""
    spec = importlib.util.spec_from_file_location("substrate_verdict_age", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check():
    return load_check()


def event(home: Path, when: datetime, status: str, name: str, problems=()) -> Path:
    """Write one archived observation, as the plant's observer does."""
    archive = home / "checks" / "substrate-watch-events"
    archive.mkdir(parents=True, exist_ok=True)
    path = archive / f"{name}.json"
    path.write_text(json.dumps({
        "version": 1,
        "event_id": name,
        "report": {
            "checked_at": when.isoformat(),
            "status": status,
            "problems": list(problems),
            "surface_states": {"checkout": "KNOWN", "runtime": status},
        },
    }))
    return path


def heartbeat(home: Path, when: datetime, status: str, problems=()) -> None:
    """Write the plant's current substrate reading."""
    path = home / "checks" / "substrate-watch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "checked_at": when.isoformat(),
        "status": status,
        "problems": list(problems),
        "surface_states": {"checkout": "KNOWN", "runtime": status},
    }))


def run(home: Path, now: datetime = NOW, bound: float = 10.0, stale: float = 5.0):
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--now", now.isoformat(),
         "--bound-minutes", str(bound), "--stale-minutes", str(stale)],
        capture_output=True, text=True)


def test_a_bad_reading_older_than_the_bound_is_routed(check, tmp_path):
    home = tmp_path / "home"
    onset = NOW - timedelta(minutes=333)
    event(home, onset, "UNKNOWN", "a", ["runtime: live service import roots unavailable: x"])
    event(home, onset + timedelta(minutes=1), "UNKNOWN", "b", ["runtime: live service import roots unavailable: x"])
    event(home, NOW - timedelta(seconds=20), "UNKNOWN", "c", ["runtime: live service import roots unavailable: x"])
    heartbeat(home, NOW - timedelta(seconds=20), "UNKNOWN", ["runtime: live service import roots unavailable: x"])

    result = run(home)

    assert result.returncode == 3
    assert "BOUND EXCEEDED" in result.stdout
    assert "route to health" in result.stdout
    assert onset.isoformat() in result.stdout


def test_a_bad_reading_younger_than_the_bound_is_not_a_blocker(check, tmp_path):
    home = tmp_path / "home"
    onset = NOW - timedelta(minutes=3)
    event(home, onset, "UNKNOWN", "a", ["runtime: live service import roots unavailable: x"])
    heartbeat(home, NOW - timedelta(seconds=20), "UNKNOWN", ["runtime: live service import roots unavailable: x"])

    result = run(home)

    assert result.returncode == 0
    assert "within bound" in result.stdout


def test_a_known_reading_is_healthy(check, tmp_path):
    home = tmp_path / "home"
    event(home, NOW - timedelta(minutes=60), "KNOWN", (), "a")
    heartbeat(home, NOW - timedelta(seconds=20), "KNOWN")

    result = run(home)

    assert result.returncode == 0
    assert "AGREE" in result.stdout


def test_a_missing_heartbeat_is_unknown_not_healthy(check, tmp_path):
    home = tmp_path / "home"
    (home / "checks").mkdir(parents=True)

    result = run(home)

    assert result.returncode == 2
    assert "UNKNOWN" in result.stdout
    assert "not a healthy plant" in result.stdout


def test_a_stale_heartbeat_is_unknown_not_healthy(check, tmp_path):
    home = tmp_path / "home"
    heartbeat(home, NOW - timedelta(minutes=30), "KNOWN")

    result = run(home)

    assert result.returncode == 2
    assert "stale monitoring is unknown, not healthy" in result.stdout


def test_an_unreadable_heartbeat_is_unknown_not_healthy(check, tmp_path):
    home = tmp_path / "home"
    path = home / "checks" / "substrate-watch.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    result = run(home)

    assert result.returncode == 2
    assert "UNKNOWN" in result.stdout


def test_the_age_is_wall_clock_and_not_an_interval_count(check, tmp_path):
    """Two events 30 minutes apart are one long run, not two short ones.

    The archive holds one event per change of the observation, so an
    implementation that counted events would call this pair young. The rule
    is about how long the plant has been wrong.
    """
    home = tmp_path / "home"
    onset = NOW - timedelta(minutes=30)
    event(home, onset, "UNKNOWN", "a", ["runtime: live service import roots unavailable: x"])
    event(home, NOW - timedelta(seconds=20), "UNKNOWN", "b", ["runtime: live service import roots unavailable: x"])
    heartbeat(home, NOW - timedelta(seconds=20), "UNKNOWN", ["runtime: live service import roots unavailable: x"])

    result = run(home)

    assert result.returncode == 3
    assert "age 30.0 min" in result.stdout


def test_a_known_reading_after_a_bad_run_is_healthy(check, tmp_path):
    home = tmp_path / "home"
    event(home, NOW - timedelta(minutes=30), "UNKNOWN", "a", ["runtime: x"])
    event(home, NOW - timedelta(minutes=20), "KNOWN", (), "b")
    heartbeat(home, NOW - timedelta(seconds=20), "KNOWN")

    result = run(home)

    assert result.returncode == 0
    assert "AGREE" in result.stdout


def test_a_known_reading_with_problems_is_still_bad(check, tmp_path):
    """A reading that lists problems is bad even when its status is KNOWN."""
    home = tmp_path / "home"
    onset = NOW - timedelta(minutes=20)
    event(home, onset, "KNOWN", "a", ["runtime: x"])
    heartbeat(home, NOW - timedelta(seconds=20), "KNOWN", ["runtime: x"])

    result = run(home)

    assert result.returncode == 3
    assert "BOUND EXCEEDED" in result.stdout
