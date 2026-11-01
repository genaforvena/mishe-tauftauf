"""The shipped-book idle-scope detector must catch a wrong scope and leave a
sound one alone.

The detector reads a shipped page's stated scope for the ``idle <N>s`` figure
every pane's headline carries, and compares it with the scope the **committed
tree** implements.  The tree is exercised: two monitored roles are given
different activity ages and the figure that reaches a pane is read.

What these tests pin:

* a page that says the figure is per-mind fails when the tree measures it
  plant-wide (the defect measured on 2026-11-01, Docs wake 152920);
* a page that says the figure is plant-wide passes when the tree measures it
  plant-wide;
* a page that mentions a scope for something *else* -- the plant-wide seed
  lock -- is not a finding about the idle figure;
* the tree is exercised, so a page that states nothing cannot pass by
  accident, and a tree that cannot be exercised reads UNKNOWN rather than a
  pass;
* this repository's own shipped book satisfies the check it ships;
* CI runs the gate over the committed bytes, before the test suite.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-idle-scope.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

# The scope the tree implements, measured on 2026-11-01: the figure on every
# pane is the time since *any* monitored mind last acted.
PLANT_WIDE_PAGE = """# Panes

The `idle <N>s` figure in each pane's headline is **plant-wide**, not
per-mind: it is the time since any resident last acted, and the same number is
printed on every pane.
"""

# The wrong scope: the figure as if it tracked only the mind that owns the
# pane.  This is what the book said by omission before 2026-11-01.
PER_MIND_PAGE = """# Panes

The `idle <N>s` figure in each pane's headline is **per-mind**: it is the time
since that pane's own mind last acted.
"""

# A scope about something else.  The figure is not named in the same
# sentence, so this is not a claim about the idle figure.
UNRELATED_SCOPE_PAGE = """# Locks

The watcher shares the plant-wide seed lock with every resident supervisor,
which holds it across slow steps.
"""


def run_check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                           "--tree", str(tree)],
                          capture_output=True, text=True)


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    """A tree with pages and the plant package the check exercises.

    The check *runs* the tree's own code -- it plants a site and reads the
    figure off a rendered headline -- so the tree needs a package that
    actually runs.  The repository's own ``src/`` is copied in, so the tree
    behaves like a clone of this repository with different pages.
    """
    package = tmp / "src" / "mishe_tauftauf"
    package.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "src" / "mishe_tauftauf", package,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name, body in pages.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp


def test_the_gate_rejects_a_page_that_states_the_wrong_scope(tmp_path):
    """A page claiming the figure is per-mind must fail when it is plant-wide."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": PER_MIND_PAGE})
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "STALE IDLE SCOPE" in result.stdout
    assert "docs/getting-started.md" in result.stdout


def test_the_gate_accepts_a_page_that_states_the_measured_scope(tmp_path):
    """The same page, corrected, passes."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": PLANT_WIDE_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_a_scope_about_something_else_is_not_a_finding(tmp_path):
    """A plant-wide *lock* is not a claim about the idle figure."""
    tree = make_tree(tmp_path, {"docs/other.md": UNRELATED_SCOPE_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "statements=0" in result.stdout, result.stdout


def test_the_gate_tells_a_per_mind_implementation_from_a_plant_wide_one(tmp_path):
    """The gate must measure the scope, not assume it.

    A check that reads "plant-wide" whatever the tree does would pass every
    page and catch nothing.  So the tree is patched to measure one role's own
    wall, and the gate must then read the scope as per-mind -- and judge the
    page against that, not against the plant-wide answer.
    """
    tree = make_tree(tmp_path, {"docs/getting-started.md": PER_MIND_PAGE})
    activity = tree / "src" / "mishe_tauftauf" / "activity.py"
    source = activity.read_text(encoding="utf-8")
    original = ('    for path in (home / "walls").glob("*.md"):\n'
                '        if path.stem in monitored:\n')
    patched = ('    own = os.environ.get("PROBE_ROLE")\n'
               '    for path in (home / "walls").glob("*.md"):\n'
               '        if path.stem in monitored and (own is None or path.stem == own):\n')
    assert original in source, "the activity reading changed shape"
    source = source.replace(original, patched)
    if "\nimport os\n" not in source:
        source = source.replace("import math", "import math\nimport os", 1)
    activity.write_text(source, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(REPO), "--tree", str(tree)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(tree / "src"), "PROBE_ROLE": "docs"})
    assert "observed scope=per-mind" in result.stdout, result.stdout
    # The page states per-mind, and the tree now measures per-mind, so the
    # page is correct here -- the gate must not fail a true statement.
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_page_that_states_nothing_is_not_a_finding(tmp_path):
    """A page about something else is not evidence about this rule."""
    tree = make_tree(tmp_path, {"docs/other.md": "# Other\n\nThe plant is local.\n"})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "statements=0" in result.stdout, result.stdout


def test_the_gate_observes_the_tree_rather_than_asserting_a_scope(tmp_path):
    """The observed scope is read from the tree and reported."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": PLANT_WIDE_PAGE})
    result = run_check(tree)
    assert "observed scope=plant-wide" in result.stdout, result.stdout


def test_a_tree_that_cannot_be_exercised_reads_unknown(tmp_path):
    """A tree the code cannot run in reads UNKNOWN, never a pass."""
    tree = tmp_path / "empty"
    tree.mkdir()
    result = run_check(tree)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_the_committed_tree_of_this_repository_runs_what_it_documents():
    """This repository's own shipped book must satisfy the check it ships."""
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(REPO)],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_runs_the_shipped_book_idle_scope_gate():
    """CI must invoke the detector over the committed bytes."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "checks/shipped-book-idle-scope.py" in text, (
        "the shipped-book idle-scope detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/shipped-book-idle-scope.py" in line
                  and not line.strip().startswith("#")]
    assert gate_lines, "the idle-scope gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the idle-scope gate must test the committed bytes (--repo), not the "
        "working tree")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip()
             for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/shipped-book-idle-scope.py" in line
                      and not line.startswith("#"))
    suite_index = next(i for i, line in enumerate(lines)
                       if line.startswith("- run: python -m pytest"))
    assert gate_index < suite_index, (
        "the shipped-book idle-scope gate must run before the test suite")
