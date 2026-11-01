"""The shipped-book program-path detector must catch a command a reader cannot run.

The defect: the shipped README told a reader to run the checks with
``PYTHONPATH=src .venv/bin/pytest -q``.  ``.venv/`` is excluded by the
repository's own ignore rules, so the clone the reader receives has no such
file, and the reader's command answers "No such file or directory" and
exits 127 -- it fails before it reaches the plant.

The sibling gates could not see it.  ``checks/shipped-book-paths.py`` reads a
**directive** in prose and a path-shaped span; the README says "run", and
the span it names is a command, not a file reference.
``checks/shipped-book-commands.py`` reads the *module* a command imports;
``pytest`` is not plant code, so the command was never that gate's question.

These tests pin the measured defect, the shapes that must not cry wolf, and
the repository's own compliance.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-program-paths.py"

# The measured defect: a program the repository's ignore rules exclude, so
# the clone never carries it.
EXCLUDED_PROGRAM_PAGE = """# Host state

Run the checks with `PYTHONPATH=src .venv/bin/pytest -q`.
"""

# The same command with a program the clone does carry.
PRESENT_PROGRAM_PAGE = """# Host state

Run the checks with `PYTHONPATH=src tools/check.py`.
"""

# A program the reader supplies through a variable: the reader's own value
# decides, so the question cannot be decided here.
READER_SUPPLIED_PAGE = """# Host state

Run the command with `"$SITE/bin/mishe" --home "$SITE" discover show`.
"""

# A program whose path carries a placeholder segment: the reader supplies
# the value of that segment.
PLACEHOLDER_PROGRAM_PAGE = """# Host state

Run the command with `SITE/bin/mishe --home SITE discover show`.
"""

# An absolute path: it names a place outside the clone on purpose.
ABSOLUTE_PROGRAM_PAGE = """# Host state

Start the session with `/tmp/tmux-UID/WORKTREE_BASENAME/bin/mishe --help`.
"""

# A program that is not a path: whether the reader's host has the tool is a
# dependency question, not this check's.
BARE_PROGRAM_PAGE = """# Host state

Run the checks with `pytest -q`.
"""


def make_tree(tmp_path: Path, pages: dict[str, str]) -> Path:
    """A tree with pages for the check to read."""
    tree = tmp_path / "tree"
    for name, text in pages.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tree


def run_check(tree: Path, repo: Path | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(CHECK), "--tree", str(tree)]
    if repo is not None:
        argv += ["--repo", str(repo)]
    return subprocess.run(argv, capture_output=True, text=True)


def test_a_program_the_ignore_rules_exclude_fails(tmp_path):
    """The measured defect must fail, and the verdict must name the program."""
    tree = make_tree(tmp_path, {"README.md": EXCLUDED_PROGRAM_PAGE})
    result = run_check(tree, repo=REPO)
    assert result.returncode == 2, result.stdout + result.stderr
    assert ".venv/bin/pytest" in result.stdout


def test_a_program_the_clone_carries_passes(tmp_path):
    """The rule is about what the clone has, not about the spelling."""
    tree = make_tree(tmp_path, {"README.md": PRESENT_PROGRAM_PAGE})
    (tree / "tools").mkdir(parents=True, exist_ok=True)
    (tree / "tools" / "check.py").write_text('"""Check."""\n', encoding="utf-8")
    result = run_check(tree, repo=REPO)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_reader_supplied_program_is_unknown_not_a_finding(tmp_path):
    """The reader's value for the variable is not knowable here."""
    tree = make_tree(tmp_path, {"README.md": READER_SUPPLIED_PAGE})
    result = run_check(tree, repo=REPO)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_placeholder_program_is_unknown_not_a_finding(tmp_path):
    """A placeholder segment carries the reader's own value."""
    tree = make_tree(tmp_path, {"README.md": PLACEHOLDER_PROGRAM_PAGE})
    result = run_check(tree, repo=REPO)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_an_absolute_program_is_unknown_not_a_finding(tmp_path):
    """An absolute path names a place outside the clone on purpose."""
    tree = make_tree(tmp_path, {"README.md": ABSOLUTE_PROGRAM_PAGE})
    result = run_check(tree, repo=REPO)
    # The absolute path is read and reported UNKNOWN: it names a place
    # outside the clone, so the clone's contents cannot decide it.  An
    # UNKNOWN row is not a finding, so the run does not fail.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout
    assert "BOOK vs PROGRAM PATH" not in result.stdout


def test_a_bare_program_is_not_this_checks_question(tmp_path):
    """Whether the reader's host has the tool is a dependency question."""
    tree = make_tree(tmp_path, {"README.md": BARE_PROGRAM_PAGE})
    result = run_check(tree, repo=REPO)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_tree_with_no_ignore_rules_is_unknown_not_a_finding(tmp_path):
    """Without the repository's rules the question cannot be decided."""
    tree = make_tree(tmp_path, {"README.md": EXCLUDED_PROGRAM_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_tree_carrying_its_own_ignore_rules_is_read(tmp_path):
    """The committed ignore rules travel with the tree.

    A reader who clones has a ``.git``; a reader who unpacks an archive
    does not.  The rules are the tree's own ``.gitignore``, so both
    readings must find the same defect -- otherwise the finding would
    disappear for whoever receives the tree as an archive.
    """
    tree = make_tree(tmp_path, {"README.md": EXCLUDED_PROGRAM_PAGE})
    (tree / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert ".venv/bin/pytest" in result.stdout


def test_this_repository_satisfies_the_check_it_ships():
    """The shipped book must satisfy the gate the repository ships.

    The **working tree** is read, not the committed bytes: the check reads
    the tree it is given, so a fix that is not yet committed is still seen.
    The committed-bytes reading is pinned by the gate itself.
    """
    result = subprocess.run([sys.executable, str(CHECK), "--tree", str(REPO),
                              "--repo", str(REPO)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
