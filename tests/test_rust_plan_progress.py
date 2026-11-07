"""The rust-plan progress producer: a real detector, not a hand-written GREEN.

These tests pin the consumer-visible behavior of the detector that keeps the
rust-plan investigation honest: a missing or stale wall is RED, an
unreadable observation is UNKNOWN, and the signal it feeds ages to UNKNOWN
when the producer stops running.
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import producers, signal_store as store

CHECK = Path(__file__).resolve().parents[1] / "checks" / "rust-plan-progress.py"
PY = sys.executable
NOW = 7000.0


def run_check(home: Path, repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(CHECK), "--home", str(home), "--repo", str(repo), *extra],
        capture_output=True, text=True, timeout=30)


@pytest.fixture
def site(tmp_path):
    home = tmp_path / "site"
    (home / "walls").mkdir(parents=True)
    (home / "signals").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    return home, repo


def test_missing_wall_is_red_not_green(site):
    home, repo = site
    result = run_check(home, repo)
    assert result.returncode == 2
    assert "no wall" in result.stderr


def test_a_fresh_wall_with_the_plan_is_green(site):
    home, repo = site
    (home / "walls" / "rust-plan.md").write_text("# rust-plan wall\n", encoding="utf-8")
    (repo / "docs" / "rust-runtime-migration-plan.md").write_text("plan\n" * 40, encoding="utf-8")
    result = run_check(home, repo)
    assert result.returncode == 0, result.stderr
    assert "GREEN" in result.stdout


def test_a_fresh_wall_without_the_plan_is_not_a_failure(site):
    """A role that has a wall but has not written the plan yet is working, not stalled."""
    home, repo = site
    (home / "walls" / "rust-plan.md").write_text("# rust-plan wall\n", encoding="utf-8")
    result = run_check(home, repo)
    assert result.returncode == 0, result.stderr
    assert "not-yet-written" in result.stdout


def test_a_stale_wall_is_red(site):
    home, repo = site
    wall = home / "walls" / "rust-plan.md"
    wall.write_text("# rust-plan wall\n", encoding="utf-8")
    old = time.time() - 7200 - 60
    import os
    os.utime(wall, (old, old))
    result = run_check(home, repo)
    assert result.returncode == 2
    assert "past the" in result.stderr


def test_a_tiny_plan_document_is_red(site):
    home, repo = site
    (home / "walls" / "rust-plan.md").write_text("# rust-plan wall\n", encoding="utf-8")
    (repo / "docs" / "rust-runtime-migration-plan.md").write_text("x", encoding="utf-8")
    result = run_check(home, repo)
    assert result.returncode == 2


def test_an_unreadable_site_home_is_unknown_not_red(site):
    home, repo = site
    result = run_check(home.parent / "absent", repo)
    assert result.returncode == 3


def test_the_signal_ages_to_unknown_when_the_producer_stops_running(site):
    """A producer that stops running must leave UNKNOWN, never a standing GREEN."""
    home, repo = site
    store.register(home, "rust-plan-progress", max_age=900, subscribers=["rust-plan"])
    (home / "walls" / "rust-plan.md").write_text("# rust-plan wall\n", encoding="utf-8")
    command = [PY, str(CHECK), "--home", str(home), "--repo", str(repo)]
    assert producers.run(home, "rust-plan-progress", command, now=NOW, timeout=30) == 0
    assert store.read(home, "rust-plan-progress", now=NOW).value == "GREEN"

    later = NOW + 1000
    assert store.read(home, "rust-plan-progress", now=later).state == "UNKNOWN"
    assert "rust-plan-progress" in store.expire(home, now=later)


def test_a_failed_producer_leaves_the_signal_to_age_instead_of_reporting_red(site):
    """A crash is not an observation: the reading ages to UNKNOWN, it does not turn RED."""
    home, repo = site
    store.register(home, "rust-plan-progress", max_age=900, subscribers=["rust-plan"])
    assert producers.run(home, "rust-plan-progress",
                         [PY, "-c", "import sys; sys.exit(1)"],
                         now=NOW, timeout=30) == 2
    assert store.read(home, "rust-plan-progress", now=NOW).state == "UNKNOWN"
