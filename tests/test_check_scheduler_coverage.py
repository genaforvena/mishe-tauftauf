"""A tracked check that no scheduler runs is a verdict nobody reads.

``checks/check-scheduler-coverage.py`` answers a question about the plant's
own monitoring: every detector the reviewed tree tracks is either scheduled by
the observer-check runner, or declared there as deliberately unscheduled with
a measured reason. A check in neither table produces verdicts the plant never
reads and never routes, so the class of defect it was written to catch stays
invisible while the plant publishes a problem set that does not mention it.

These tests pin the detector's decisions on a synthetic site home, so the
readings are fixed by the fixture rather than by the machine and the same
assertions hold in CI and on the plant:

* a tracked check that is neither scheduled nor declared is named as
  unobserved, and one that currently fails makes the check exit 2;
* a tracked check that is scheduled is not named;
* a tracked check that is declared-not-scheduled is not named;
* an *untracked* check script is not named, because a script in somebody's
  working tree is in-flight work, not a verdict the plant owes;
* a check that cannot accept the arguments it is given is reported as not
  runnable, and is not counted as a failing verdict — argparse's usage error
  and a failing verdict share exit code 2, and telling them apart is what
  keeps this check's own verdict honest;
* a runner whose published state disagrees with its own source about which
  checks it reads is reported, because the plant cannot then say what it
  reads;
* a missing repository, runner source or published state is UNKNOWN, never
  green.

The detector reads the reviewed tree's tracked file list, the runner's source
as text and the runner's published state. It imports nothing from the plant
package and runs no plant program, so a change to an internal helper's
signature cannot make it fail.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "check-scheduler-coverage.py"

RUNNER_SOURCE = '''\
"""A stand-in for the observer-check runner."""
INVENTORY = (
    ("scheduled-check.py", True, True, (), "evidence"),
)

NOT_SCHEDULED = (
    ("declared-check.py",
     "its applicability flaps, so scheduling it would flap membership"),
)
'''

PASSING_CHECK = '''\
"""A check that holds."""
import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    print("PASS: the check holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

FAILING_CHECK = '''\
"""A check that fails."""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    print("verdict   : the thing is broken")
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''

USAGE_ERROR_CHECK = '''\
"""A check that cannot accept the arguments it is given."""
import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=None)
    args = parser.parse_args()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def run_check(home: Path, repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(repo), *extra],
        capture_output=True, text=True)


def make_repo(path: Path, checks: dict[str, str]) -> Path:
    """A reviewed repository whose HEAD tracks the given check scripts."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    directory = path / "checks"
    directory.mkdir(exist_ok=True)
    for name, source in checks.items():
        (directory / name).write_text(source, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "checks"], check=True)
    return path


def make_home(path: Path, runner: str = RUNNER_SOURCE,
              entries: list[dict] | None = None,
              not_scheduled: list[dict] | None = None) -> Path:
    """A site home with the runner's source and its published state."""
    directory = path / "checks-runner"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "observer-checks.py").write_text(runner, encoding="utf-8")
    state = path / "checks"
    state.mkdir(parents=True, exist_ok=True)
    payload = {
        "checked_at": "2026-10-23T00:00:00+00:00",
        "entries": entries if entries is not None else [
            {"id": "scheduled-check:body-research", "exit": 0, "state": "OK",
             "verdict": "PASS"}],
        "not_scheduled": not_scheduled if not_scheduled is not None else [
            {"id": "declared-check", "reason": "its applicability flaps"}],
    }
    (state / "observer-checks.json").write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_an_unobserved_failing_check_is_named_and_fails_the_verdict(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        "scheduled-check.py": PASSING_CHECK,
        "declared-check.py": PASSING_CHECK,
        "unobserved-check.py": FAILING_CHECK,
    })
    home = make_home(tmp_path / "home")
    result = run_check(home, repo)
    assert result.returncode == 2, result.stdout
    assert "FAILING-UNOBSERVED unobserved-check.py" in result.stdout, result.stdout
    assert "VERDICT: 1 unobserved check(s) currently fail" in result.stdout, result.stdout


def test_a_scheduled_check_is_not_named(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        "scheduled-check.py": FAILING_CHECK,
        "declared-check.py": FAILING_CHECK,
    })
    home = make_home(tmp_path / "home")
    result = run_check(home, repo)
    assert result.returncode == 0, result.stdout
    assert "UNOBSERVED" not in result.stdout, result.stdout
    assert "VERDICT: every tracked check is scheduled or declared-not-scheduled" in result.stdout


def test_an_untracked_check_script_is_not_named(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        "scheduled-check.py": PASSING_CHECK,
        "declared-check.py": PASSING_CHECK,
    })
    home = make_home(tmp_path / "home")
    # A script in the working tree that HEAD does not track is somebody's
    # in-flight work, not a verdict the plant owes.
    (repo / "checks" / "in-flight-check.py").write_text(FAILING_CHECK, encoding="utf-8")
    result = run_check(home, repo)
    assert result.returncode == 0, result.stdout
    assert "in-flight-check.py" not in result.stdout, result.stdout


def test_a_check_that_cannot_be_given_the_arguments_is_not_a_failing_verdict(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        "scheduled-check.py": PASSING_CHECK,
        "declared-check.py": PASSING_CHECK,
        "not-runnable-check.py": USAGE_ERROR_CHECK,
    })
    home = make_home(tmp_path / "home")
    result = run_check(home, repo)
    assert result.returncode == 0, result.stdout
    assert "NOT RUNNABLE" in result.stdout, result.stdout
    assert "FAILING-UNOBSERVED" not in result.stdout, result.stdout


def test_a_runner_whose_published_state_disagrees_with_its_source_is_reported(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        "scheduled-check.py": PASSING_CHECK,
        "declared-check.py": PASSING_CHECK,
    })
    home = make_home(tmp_path / "home", not_scheduled=[
        {"id": "declared-check", "reason": "its applicability flaps"},
        {"id": "some-other-check", "reason": "never in the source"},
    ])
    result = run_check(home, repo)
    assert result.returncode == 2, result.stdout
    assert "disagree about which checks it reads" in result.stdout, result.stdout
    assert "some-other-check" in result.stdout, result.stdout


def test_a_missing_repository_is_unknown_not_green(tmp_path):
    home = make_home(tmp_path / "home")
    result = run_check(home, tmp_path / "no-repo")
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout, result.stdout


def test_a_missing_runner_source_is_unknown_not_green(tmp_path):
    repo = make_repo(tmp_path / "repo", {"scheduled-check.py": PASSING_CHECK})
    home = make_home(tmp_path / "home")
    (home / "checks-runner" / "observer-checks.py").unlink()
    result = run_check(home, repo)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout, result.stdout


def test_a_missing_published_state_is_unknown_not_green(tmp_path):
    repo = make_repo(tmp_path / "repo", {"scheduled-check.py": PASSING_CHECK})
    home = make_home(tmp_path / "home")
    (home / "checks" / "observer-checks.json").unlink()
    result = run_check(home, repo)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout, result.stdout


def test_a_runner_with_no_inventory_is_unknown_not_green(tmp_path):
    repo = make_repo(tmp_path / "repo", {"scheduled-check.py": PASSING_CHECK})
    home = make_home(tmp_path / "home", runner='"""A runner with no tables."""\n')
    result = run_check(home, repo)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout, result.stdout


def test_the_list_mode_names_every_unobserved_check_without_running_them(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        "scheduled-check.py": PASSING_CHECK,
        "declared-check.py": PASSING_CHECK,
        "unobserved-check.py": FAILING_CHECK,
    })
    home = make_home(tmp_path / "home")
    result = run_check(home, repo, "--list")
    assert result.returncode == 0, result.stdout
    assert "UNOBSERVED unobserved-check.py" in result.stdout, result.stdout
    assert "scheduled-check.py" not in result.stdout, result.stdout


def test_the_check_does_not_run_itself(tmp_path):
    """Once the tree carries this check, it is itself unobserved.

    Running it inside itself would recurse, so the check reports itself and
    does not re-run it. Without this the check would spawn itself once per
    unobserved check and never return.
    """
    repo = make_repo(tmp_path / "repo", {
        "scheduled-check.py": PASSING_CHECK,
        "declared-check.py": PASSING_CHECK,
        "check-scheduler-coverage.py": (REPO / "checks" / "check-scheduler-coverage.py").read_text(
            encoding="utf-8"),
    })
    home = make_home(tmp_path / "home")
    result = run_check(home, repo)
    assert result.returncode == 0, result.stdout
    assert "check-scheduler-coverage.py — this check; its verdict is this one" in result.stdout, result.stdout
    assert "FAILING-UNOBSERVED check-scheduler-coverage.py" not in result.stdout, result.stdout
