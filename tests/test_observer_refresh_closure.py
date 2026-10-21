"""The refresh-closure detector must name the closure and the silence.

The detector answers two questions about the observer bundle: which files a
refresh must carry for the evidence leg to work (the run closure), and which
files it must carry for the published facts to agree with the reviewed tree's
(the fact closure). A module that imports cleanly and is still stale belongs
to the second question and is invisible to the first.

Exit status under test:
  0  the running bundle publishes the reviewed tree's facts and a one-file
     refresh would not break the evidence leg
  2  a one-file refresh would break the evidence leg
  3  the question cannot be decided
  4  the published facts differ from the reviewed tree's facts
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

# A role row the reviewed tree publishes, and the bundle's stale copy of it:
# same row, one field absent. This is the field-level gap in miniature, in a
# module that imports cleanly, so the run closure cannot see it.
REVIEWED_ROW = "role: OBSERVED; wakes=1 checked-evidence-age=none"
STALE_ROW = "role: OBSERVED; wakes=1"


def write_package(root: Path, *, projection: bool, carries_name: bool,
                  row: str = REVIEWED_ROW, projection_line: bool = True,
                  via_helper: bool = False) -> Path:
    """A package whose evidence leg works, or fails on a missing sibling name.

    ``projection`` selects whether the package's copy of the projection
    imports the name the reviewed tree's imports: the bundle's copy does not,
    so a one-file refresh breaks. ``carries_name`` selects whether the wall
    carries that name at all: the bundle's wall does not.
    ``row`` is the role row the package's ``initiative.py`` publishes, which
    is where a stale-but-importable module carries stale facts.
    ``projection_line`` selects whether the projection is in the published
    block. ``via_helper`` makes the row arrive through a second module, so
    that refreshing the first one needs the second one to be carried too.
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
    if via_helper:
        (package / "helper.py").write_text(
            "def rows():\n    return [%r]\n" % (row,))
        (package / "initiative.py").write_text(
            "from .helper import rows\nROWS = rows()\n")
    else:
        (package / "initiative.py").write_text("ROWS = [%r]\n" % (row,))
    body = []
    if projection:
        body.append("from .wall import OUTCOME_REFUSAL_AUDIT")
    else:
        body.append("from .wall import REFUSAL_AUDIT")
    body.append("from .initiative import ROWS")
    body.append("")
    body.append("LINES = []")
    if projection_line:
        body.append("LINES.append(%r)" % (PROJECTION,))
    body.append("LINES.extend(ROWS)")
    body.append("")
    body.append("def lines(home):")
    body.append("    return list(LINES)")
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


def reviewed(tmp_path) -> Path:
    """The reviewed tree: the projection, the name, and the current row."""
    return write_package(tmp_path / "reviewed", projection=True, carries_name=True)


def test_one_file_refresh_that_breaks_the_leg_reads_unsafe(plant, tmp_path):
    """The reviewed projection cannot be carried by one file alone."""
    bundle = write_package(plant.parent / "bundle", projection=False,
                            carries_name=False)
    reference = reviewed(tmp_path)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNSAFE" in result.stdout, result.stdout
    assert "follow_through.py" in result.stdout
    assert "wall.py" in result.stdout, result.stdout


def test_a_bundle_already_carrying_the_projection_reads_safe(plant, tmp_path):
    """A bundle whose siblings carry the imported name needs no refresh."""
    bundle = write_package(plant.parent / "bundle", projection=True,
                            carries_name=True)
    reference = reviewed(tmp_path)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SAFE" in result.stdout, result.stdout


def test_a_renderer_that_reports_failure_reads_visible(plant, tmp_path):
    """When the renderer exits non-zero, a broken evidence child is reported."""
    bundle = write_package(plant.parent / "bundle", projection=False,
                            carries_name=False)
    reference = reviewed(tmp_path)
    write_renderer(plant, bundle, report_failure=True)
    result = run_check(plant, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "VISIBLE" in result.stdout, result.stdout
    assert "SILENT" not in result.stdout, result.stdout


def test_a_renderer_that_hides_failure_reads_silent(plant, tmp_path):
    """The plant's own script shape hides it, and the check says so."""
    bundle = write_package(plant.parent / "bundle", projection=False,
                            carries_name=False)
    reference = reviewed(tmp_path)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "SILENT" in result.stdout, result.stdout
    assert "exits 0" in result.stdout, result.stdout


def test_a_missing_bundle_reads_unknown(plant, tmp_path):
    """No bundle to measure is not a verdict."""
    reference = reviewed(tmp_path)
    result = run_check(plant, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout, result.stdout


def test_a_missing_reviewed_tree_reads_unknown(plant, tmp_path):
    """No reviewed tree to refresh from is not a verdict."""
    bundle = write_package(plant.parent / "bundle", projection=False,
                            carries_name=False)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, plant.parent / "absent")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout, result.stdout


def test_a_stale_but_importable_module_names_the_fact_closure(plant, tmp_path):
    """A module that imports cleanly and is still stale must be named.

    The bundle's ``initiative.py`` publishes a role row without the field the
    reviewed tree publishes. Nothing fails, so the run closure cannot see it,
    and the published facts differ.
    """
    bundle = write_package(plant.parent / "bundle", projection=False,
                            carries_name=False, row=STALE_ROW)
    reference = reviewed(tmp_path)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "DIFFER" in result.stdout, result.stdout
    assert "these modules carry them: initiative.py" in result.stdout, result.stdout
    assert ("fact closure: follow_through.py, initiative.py, wall.py"
            in result.stdout), result.stdout


def test_a_probe_that_needs_a_sibling_carries_it_too(plant, tmp_path):
    """A refresh that cannot be carried alone is closed by running it.

    The reviewed tree's ``initiative.py`` reaches its row through
    ``helper.py``; the bundle has no ``helper.py`` at all. The probe must add
    the sibling the reviewed module imports, the way the run closure does.
    """
    bundle = write_package(plant.parent / "bundle", projection=False,
                            carries_name=False, row=STALE_ROW)
    reference = write_package(tmp_path / "reviewed", projection=True,
                              carries_name=True, via_helper=True)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "these modules carry them: helper.py, initiative.py" in result.stdout, result.stdout


def test_a_bundle_publishing_the_reviewed_facts_reads_agree(plant, tmp_path):
    """The running bundle's facts are compared with the reviewed tree's."""
    bundle = write_package(plant.parent / "bundle", projection=True,
                            carries_name=True)
    reference = reviewed(tmp_path)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "facts     : AGREE" in result.stdout, result.stdout


def test_a_counter_that_moved_is_not_a_difference_of_facts(plant, tmp_path):
    """A counter that moves between two samples is not a difference of facts."""
    bundle = write_package(plant.parent / "bundle", projection=True,
                            carries_name=True,
                            row="role: OBSERVED; wakes=2 checked-evidence-age=none")
    reference = reviewed(tmp_path)
    write_renderer(plant, bundle, report_failure=False)
    result = run_check(plant, reference)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "facts     : AGREE" in result.stdout, result.stdout
