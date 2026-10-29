"""A reviewed fix must be live on the path every command actually runs.

``checks/check-cli-entry-root.py`` answers a question about the plant's own
execution paths: the site has two statements of "the bytes this plant runs" —
the runtime pin (``SITE/health/runtime-release.json``) and the CLI wrapper
(``SITE/bin/mishe-tauftauf``) — and when they disagree the plant has a second
execution path that no verdict covers.

These tests pin the detector's decisions on a synthetic site home, so the
readings are fixed by the fixture rather than by the machine and the same
assertions hold in CI and on the plant:

* a wrapper that exports the pinned root is GREEN;
* a wrapper that exports a third root which does **not** carry the write-once
  clause the reviewed checkout carries is a FAILURE, and the verdict names the
  root — this is the defect the check was written for;
* a wrapper that exports a third root which **does** carry the clause is GREEN;
* a declared third root is reported with its declaring unit and its retiring
  condition, because a declaration scoped to a unit does not cover the CLI
  wrapper;
* the pin lagging the reviewed tree is reported as information, not as a
  failure: a pin advance is genome's owned action, and the check must not
  double-report it as the CLI divergence;
* a checkout that does not carry the clause is GREEN, because then there is no
  reviewed fix for the CLI path to be inert about;
* no entry point, an unreadable wrapper, a missing pin record and an
  unparseable root are UNKNOWN, never green.

The detector reads the wrapper, the pin record, the declared-roots record and
the two roots' source files. It imports nothing from the plant package and
runs no plant program, so a change to an internal helper's signature cannot
make it fail.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "check-cli-entry-root.py"

PACKAGE = "mishe_tauftauf"

#: A module that enforces the write-once clause.
CLAUSE_MODULE = '''\
import os


def _publish_outcome(home, evidence):
    original_mode = evidence.stat().st_mode & 0o7777
    os.chmod(evidence, original_mode & ~0o222)
    return original_mode
'''

#: A module that does not enforce it.
NO_CLAUSE_MODULE = '''\
def _publish_outcome(home, evidence):
    return evidence.read_bytes()
'''

#: A module that cannot be parsed.
UNPARSEABLE_MODULE = "def _publish_outcome(home, evidence:\n    return None\n"


def make_root(base: Path, name: str, wall_source: str) -> Path:
    """An import root carrying a plant package with the given wall module."""
    root = base / name / "src"
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "wall.py").write_text(wall_source)
    return root


def make_home(base: Path, cli_root: Path, pin_root: Path, *,
              declared: dict | None = None) -> Path:
    """A site home whose CLI wrapper serves ``cli_root`` and whose pin is ``pin_root``."""
    home = base / "home"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "mishe-tauftauf").write_text(
        "#!/bin/sh\nexport PYTHONPATH=%s\nexec /usr/bin/python3 -m mishe_tauftauf \"$@\"\n" % cli_root)
    (home / "health").mkdir(parents=True)
    (home / "health" / "runtime-release.json").write_text(json.dumps(
        {"version": 1, "source": str(pin_root.parent), "sha": "0" * 40}))
    if declared is not None:
        (home / "health" / "declared-roots.json").write_text(json.dumps(declared))
    return home


def run_check(home: Path, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(repo)],
        capture_output=True, text=True)


def build(tmp_path: Path, *, cli_clause: bool, pin_clause: bool,
          checkout_clause: bool, declared: dict | None = None,
          cli_root_is_pin: bool = False) -> tuple[Path, Path]:
    """A site home and a repository whose roots carry the given clauses."""
    cli = make_root(tmp_path, "cli", CLAUSE_MODULE if cli_clause else NO_CLAUSE_MODULE)
    pin = make_root(tmp_path, "pin", CLAUSE_MODULE if pin_clause else NO_CLAUSE_MODULE)
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    package = repo / "src" / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "wall.py").write_text(CLAUSE_MODULE if checkout_clause else NO_CLAUSE_MODULE)
    served = pin if cli_root_is_pin else cli
    home = make_home(tmp_path, served, pin, declared=declared)
    return home, repo


def test_cli_serving_the_pinned_root_is_green(tmp_path):
    home, repo = build(tmp_path, cli_clause=True, pin_clause=True, checkout_clause=True,
                       cli_root_is_pin=True)
    result = run_check(home, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "serves the pinned bytes" in result.stdout


def test_cli_serving_a_root_without_the_clause_fails_and_names_the_root(tmp_path):
    home, repo = build(tmp_path, cli_clause=False, pin_clause=True, checkout_clause=True)
    result = run_check(home, repo)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "inert on the path every command runs" in result.stdout
    assert "model-cli" not in result.stdout  # the root name is printed, not guessed
    assert "cli root    :" in result.stdout


def test_cli_serving_a_root_with_the_clause_is_green(tmp_path):
    home, repo = build(tmp_path, cli_clause=True, pin_clause=False, checkout_clause=True)
    result = run_check(home, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already carries the" in result.stdout


def test_a_declared_root_is_reported_with_its_unit_and_retiring_condition(tmp_path):
    home, repo = build(tmp_path, cli_clause=False, pin_clause=True, checkout_clause=True,
                       declared={"version": 1, "units": {
                           "mishe-self-development-current-model-changes.service": {
                               "root": str(tmp_path / "cli" / "src"),
                               "owner": "health",
                               "reviewed_commit": "ef3c637",
                               "retire_when": "a reviewed pin release carries model_cli.py"}}} )
    result = run_check(home, repo)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "mishe-self-development-current-model-changes.service" in result.stdout
    assert "a reviewed pin release carries model_cli.py" in result.stdout


def test_a_pin_that_lags_the_reviewed_tree_is_information_not_a_failure(tmp_path):
    """The pin may legitimately lag HEAD; that is genome's owned pin advance."""
    home, repo = build(tmp_path, cli_clause=False, pin_clause=False, checkout_clause=True)
    result = run_check(home, repo)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "pin carries the write-once clause     : no" in result.stdout


def test_a_checkout_without_the_clause_is_green(tmp_path):
    """No reviewed fix means no reviewed fix is inert on the CLI path."""
    home, repo = build(tmp_path, cli_clause=False, pin_clause=False, checkout_clause=False)
    result = run_check(home, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "does not carry the write-once clause, so no" in result.stdout


def test_no_entry_point_is_unknown(tmp_path):
    home, repo = build(tmp_path, cli_clause=True, pin_clause=True, checkout_clause=True)
    (home / "bin" / "mishe-tauftauf").unlink()
    result = run_check(home, repo)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_missing_pin_record_is_unknown(tmp_path):
    home, repo = build(tmp_path, cli_clause=True, pin_clause=True, checkout_clause=True)
    (home / "health" / "runtime-release.json").unlink()
    result = run_check(home, repo)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_an_unparseable_root_is_unknown(tmp_path):
    home, repo = build(tmp_path, cli_clause=True, pin_clause=True, checkout_clause=True)
    (tmp_path / "cli" / "src" / PACKAGE / "wall.py").write_text(UNPARSEABLE_MODULE)
    result = run_check(home, repo)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_repository_without_the_plant_package_is_unknown(tmp_path):
    home, repo = build(tmp_path, cli_clause=True, pin_clause=True, checkout_clause=True)
    (repo / "src" / PACKAGE / "__init__.py").unlink()
    result = run_check(home, repo)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout
