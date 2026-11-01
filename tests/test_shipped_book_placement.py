"""The shipped-book placement detector must catch a stale rule and leave a sound book alone.

The detector reads a shipped page's stated rule about **where the plant's
site home may live**, and compares it with what the **committed tree** does
when the real ``init`` command runs against that placement.

What these tests pin:

* a page that says the site lives outside the repository fails when the tree
  refuses to plant there (the defect measured on 2026-11-01, Docs wake
  152920);
* a page that says the site lives inside the worktree passes when the tree
  accepts it there;
* a negated statement ("the state is never outside the checkout") is read as
  the placement it states, not as the bare phrase it contains;
* the tree is exercised, so a page that states nothing cannot pass by
  accident, and a tree that cannot be exercised reads UNKNOWN rather than a
  pass;
* this repository's own shipped book satisfies the check it ships;
* CI runs the gate over the committed bytes, before the test suite.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-placement.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

# The rule the committed tree applies, and the page that stated the opposite
# before Docs wake 152920 corrected it.
STALE_PAGE = """# Mesh

Each plant keeps its local state outside the shared source repository.
"""

CURRENT_PAGE = """# Mesh

Each plant keeps its local state in an ignored site directory inside the
shared source repository — tracked code and local state stay separate, but
the state is never outside the checkout.
"""

# A page that names both forms, with one of them negated.  The detector must
# read the negation, or it reports the page's own correction as the defect.
NEGATED_PAGE = """# Mesh

The site lives inside the worktree; its state is never outside the checkout.
"""


def run_check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                           "--tree", str(tree)],
                          capture_output=True, text=True)


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    """A tree with pages and the plant package the check exercises.

    The check *runs* the tree's own code -- it plants a site and executes
    ``init`` -- so the tree needs a package that actually runs.  The
    repository's own ``src/`` is copied in, so the tree behaves like a clone
    of this repository with different pages.
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


def test_the_gate_rejects_a_page_that_states_a_stale_rule(tmp_path):
    """A page claiming the site lives outside the repository must fail."""
    tree = make_tree(tmp_path, {"docs/mesh.md": STALE_PAGE})
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "STALE PLACEMENT RULE" in result.stdout
    assert "docs/mesh.md" in result.stdout


def test_the_gate_accepts_a_page_that_states_the_current_rule(tmp_path):
    """The same page, corrected, passes."""
    tree = make_tree(tmp_path, {"docs/mesh.md": CURRENT_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_a_negated_statement_is_read_as_the_placement_it_states(tmp_path):
    """"never outside the checkout" states inside, and must not read as outside."""
    tree = make_tree(tmp_path, {"docs/mesh.md": NEGATED_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gate_observes_the_tree_rather_than_asserting_a_result(tmp_path):
    """The observed outcome is read from the tree and reported."""
    tree = make_tree(tmp_path, {"docs/mesh.md": CURRENT_PAGE})
    result = run_check(tree)
    assert "observed inside=accepted" in result.stdout, result.stdout
    assert "outside=refused" in result.stdout, result.stdout


def test_a_page_that_states_nothing_is_not_a_finding(tmp_path):
    """A page about something else is not evidence about this rule."""
    tree = make_tree(tmp_path, {"docs/other.md": "# Other\n\nThe plant is local.\n"})
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


def test_ci_runs_the_shipped_book_placement_gate():
    """CI must invoke the detector over the committed bytes."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "checks/shipped-book-placement.py" in text, (
        "the shipped-book placement detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/shipped-book-placement.py" in line
                  and not line.strip().startswith("#")]
    assert gate_lines, "the placement gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the placement gate must test the committed bytes (--repo), not the "
        "working tree")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip()
             for line in WORKFLOW.read_text(encoding="utf-8").splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/shipped-book-placement.py" in line
                      and not line.startswith("#"))
    suite_index = next(i for i, line in enumerate(lines)
                       if line.startswith("- run: python -m pytest"))
    assert gate_index < suite_index, (
        "the shipped-book placement gate must run before the test suite")
