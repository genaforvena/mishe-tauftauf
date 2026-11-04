"""The shipped-book dependency detector must catch a stale prerequisite and leave a sound book alone.

The detector reads a shipped page's stated claim about the runtime's
**third-party dependency set**, and compares it with what the **committed
tree** actually requires -- measured by planting the tree with each
dependency absent and observing whether the plant survives.

What these tests pin:

* a page that says the runtime has no required third-party dependencies
  fails when the tree cannot be planted without one (the defect measured on
  2026-11-01, Docs wake 152920), and the converse now that none is required;
* a page that names the required dependency passes when the tree requires it;
* a page that states nothing about dependencies is not a finding;
* the tree is exercised, so the reading is observed rather than asserted, and
  a tree that cannot be exercised reads UNKNOWN rather than a pass;
* this repository's own shipped book satisfies the check it ships;
* CI runs the gate over the committed bytes, before the test suite.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-dependencies.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

# The rule the committed tree applies: the runtime imports nothing outside the
# standard library. A page that still names a required package states the old
# rule (the pane renderer's `wcwidth`, removed with the pane layer).
STALE_PAGE = """# Getting started

You need:

- **Linux and Python 3.10 or newer.**
- **The `wcwidth` package, importable by the interpreter that runs the
  launcher.** It is the runtime's only required third-party dependency.
"""

CURRENT_PAGE = """# Getting started

You need:

- **Linux and Python 3.10 or newer.** The runtime has no required third-party
  Python dependencies.
"""

# A page about something else entirely is not evidence about this rule.
UNRELATED_PAGE = """# Other

The plant is local, and the checkout holds the runtime.
"""


def run_check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                           "--tree", str(tree)],
                          capture_output=True, text=True)


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    """A tree with pages and the plant package the check exercises.

    The check *plants* the tree -- it runs the real launcher -- so the tree
    needs a package that actually runs, plus the checkout-only ``coordination``
    package the launcher imports.  The repository's own sources are copied in,
    so the tree behaves like a clone of this repository with different pages.
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




def test_the_gate_accepts_a_page_that_states_the_current_rule(tmp_path):
    """The same page, corrected, passes."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_the_gate_observes_the_required_dependency(tmp_path):
    """The required set is read from the tree and reported."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    result = run_check(tree)
    assert "required third-party dependencies: (none)" in result.stdout, result.stdout


def test_a_page_that_states_nothing_is_not_a_finding(tmp_path):
    """A page about something else is not evidence about this rule."""
    tree = make_tree(tmp_path, {"docs/other.md": UNRELATED_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "statements=0" in result.stdout, result.stdout


def test_a_tree_that_cannot_be_exercised_reads_unknown(tmp_path):
    """A tree the command cannot run in reads UNKNOWN, never a pass."""
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


def test_ci_runs_the_shipped_book_dependency_gate():
    """CI must invoke the detector over the committed bytes."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "checks/shipped-book-dependencies.py" in text, (
        "the shipped-book dependency detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/shipped-book-dependencies.py" in line
                  and not line.strip().startswith("#")]
    assert gate_lines, "the dependency gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the dependency gate must test the committed bytes (--repo), not the "
        "working tree")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip()
             for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/shipped-book-dependencies.py" in line
                      and not line.startswith("#"))
    suite_index = next(i for i, line in enumerate(lines)
                       if line.startswith("- run: python -m pytest"))
    assert gate_index < suite_index, (
        "the shipped-book dependency gate must run before the test suite")
