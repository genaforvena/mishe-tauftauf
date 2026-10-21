"""The refresh-closure detector must name the closure and the silence.

The plant's shared follow-through evidence comes from the observer bundle, a
hand copy of the reviewed tree that is refreshed one module at a time. The
reviewed tree's ``follow_through.py`` imports a name from ``wall.py``, and the
reviewed tree's ``wall.py`` imports a name from ``outcome_events.py``. A bundle
whose copies of those two predate those names cannot carry the reviewed
projection alone.

Two failures meet here. The refresh breaks the evidence leg, and the break is
silent: the top-pain script runs the evidence child last and exits with the
first command's status, so the plant publishes a truncated frame and reports
it as ok.

These tests pin the detector's decisions on synthetic packages, so the
readings are fixed by the fixture rather than by the machine:

* a one-file refresh that breaks the evidence leg reads UNSAFE and names the
  closure the refresh needs;
* a one-file refresh that leaves the evidence leg working reads SAFE;
* a renderer that reports a failed evidence child reads VISIBLE, and one that
  does not reads SILENT;
* a bundle, a reviewed tree or a renderer script that cannot be read reads
  UNKNOWN, never a verdict.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "observer-refresh-closure.py"
PACKAGE = "mishe_tauftauf"
SLUG = "research-methods"

# The reviewed projection imports a name the bundle's copy of wall.py lacks.
PROJECTION = "OUTCOME WRITER REFUSALS: 3 in the last 24h (0 coverage denials)"


def write_package(root: Path, *, projection: bool, carries_name: bool) -> Path:
    """A package whose evidence leg works, or fails on a missing sibling name.

    ``projection`` selects whether the reviewed projection is present.
    ``carries_name`` selects whether the sibling carries the name the
    projection imports: the bundle's copy does not, the reviewed tree's does.
    That difference is what makes a one-file refresh unsafe.
    """
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "outcome_events.py").write_text(
        "def blocking_gaps(rows):\n    return []\n")
    wall = ["REFUSAL_AUDIT = 'logs/outcome-refusal-audit.jsonl'"]
    if carries_name:
        wall.append("OUTCOME_REFUSAL_AUDIT = REFUSAL_AUDIT")
    (package / "wall.py").write_text("\n".join(wall) + "\n")
    if projection:
        body = ["from .wall import OUTCOME_REFUSAL_AUDIT", "",
                "LINES = [%r]" % (PROJECTION,), "",
                "def lines(home):", "    return list(LINES)"]
    else:
        # The bundle's own copy: it works, but it does not import the name
        # the reviewed projection needs.
        body = ["from .wall import REFUSAL_AUDIT", "",
                "LINES = ['SUBSTRATE: KNOWN unchanged checked_at=2026-10-21T00:00:00Z']", "",
                "def lines(home):", "    return list(LINES)"]
    (package / "follow_through.py").write_text("\n".join(body) + "\n")
    return root


def write_renderer(home: Path, bundle: Path, *, report_failure: bool) -> Path:
    """A top-pain script for a bundle, in the plant's own two-command shape.

    ``report_failure`` selects whether the script exits with the evidence
    child's status or with the first command's.
    """
    directory = home / "top-pains"
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / SLUG
    script.write_text(
        "#!/bin/sh\n"
        "true\n"
        "status=$?\n"
        "PYTHONPATH='%s' python3 -c \"import sys;"
        "sys.path.insert(0, '%s');"
        "from %s import follow_through;"
        "print('MIND WALL — shared follow-through evidence; not an authored wall');"
        "print(chr(10).join(follow_through.lines(None)))\"\n"
        "%s\n"
        % (bundle, bundle, PACKAGE,
           'exit "$status"' if not report_failure else 'status=$?; exit "$status"'))
    script.chmod(0o755)
    return script


@pytest.fixture
def plant(tmp_path):
    home = tmp_path / "site"
    (home / "top-pains").mkdir(parents=True)
    return home


def run_check(home: Path, reference: Path, extra=()):
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home),
         "--reference-src", str(reference), *extra],
        capture_output=True, text=True)


def test_one_file_refresh_that_breaks_the_leg_reads_unsafe(plant, tmp_path):
    """The reviewed projection cannot be carried by one file alone."""
    bundle = write_package(plant.parent / "bundle", projection=False, carries_name=False)
    reference = write_package(plant.parent / "reviewed", projection=True, carries_name=True)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNSAFE" in result.stdout, result.stdout
    assert "follow_through.py" in result.stdout
    assert "wall.py" in result.stdout, result.stdout


def test_a_bundle_already_carrying_the_projection_reads_safe(plant, tmp_path):
    """A bundle whose siblings carry the imported name needs no refresh."""
    bundle = write_package(plant.parent / "bundle", projection=False, carries_name=True)
    reference = write_package(plant.parent / "reviewed", projection=True, carries_name=True)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SAFE" in result.stdout, result.stdout


def test_a_renderer_that_reports_failure_reads_visible(plant, tmp_path):
    """When the renderer exits non-zero, a broken evidence child is reported."""
    bundle = write_package(plant.parent / "bundle", projection=False, carries_name=False)
    reference = write_package(plant.parent / "reviewed", projection=True, carries_name=True)
    write_renderer(plant, bundle, report_failure=True)
    result = run_check(plant, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "VISIBLE" in result.stdout, result.stdout
    assert "SILENT" not in result.stdout, result.stdout


def test_a_renderer_that_hides_failure_reads_silent(plant, tmp_path):
    """The plant's own script shape hides it, and the check says so."""
    bundle = write_package(plant.parent / "bundle", projection=False, carries_name=False)
    reference = write_package(plant.parent / "reviewed", projection=True, carries_name=True)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "SILENT" in result.stdout, result.stdout
    assert "exits 0" in result.stdout, result.stdout


def test_a_missing_bundle_reads_unknown(plant, tmp_path):
    """No bundle to measure is not a verdict."""
    reference = write_package(plant.parent / "reviewed", projection=True, carries_name=True)
    result = run_check(plant, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout, result.stdout


def test_a_missing_reviewed_tree_reads_unknown(plant, tmp_path):
    """No reviewed tree to refresh from is not a verdict."""
    bundle = write_package(plant.parent / "bundle", projection=False, carries_name=False)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, plant.parent / "absent")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout, result.stdout
