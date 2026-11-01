"""The shipped-book command detector must catch a command a reader cannot run.

The detector reads a shipped page's **command** -- the plant code it hands
the reader -- and asks whether that command runs in a clone that has not
installed the package, which is the state the book promises when it says no
package install is required.

Measured on this plant on 2026-11-01 (Docs wake 164192): ``docs/getting-
started.md`` told a reader to read the host's admission state with
``python -m mishe_tauftauf.host_capacity --root ...``.  This host is Ubuntu
24.04, which ships no ``python`` binary at all, so the reader's own command
fails with "command not found" before it reaches the plant.  The same pages
named ``python3 -m mishe_tauftauf.<module>`` with no path to ``src/``, so a
clone that has not installed the package answered ``ModuleNotFoundError``.
And ``docs/completions-judge.md`` named ``.venv/bin/python``, a path the
book never tells the reader to create and the clone never receives.

What these tests pin:

* a page that names an absent interpreter fails, and the verdict names the
  page, the command and the reason;
* a page that names a module the clone cannot import fails;
* a page that names an interpreter path the clone does not contain fails;
* the same command with ``env PYTHONPATH=src`` in front passes -- the rule
  is about what the reader can run, not about the spelling;
* a command whose import path the reader supplies through a variable is
  UNKNOWN rather than a finding, because the reader's value is not knowable
  here;
* a page that states no plant-code command is UNKNOWN rather than a
  vacuous pass;
* this repository's own shipped book satisfies the check it ships.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-commands.py"

# The defect: an interpreter a standard Linux host does not ship.  Ubuntu
# 24.04 answers "python: command not found" for exactly this command.
ABSENT_INTERPRETER_PAGE = """# Host state

```sh
python -m mishe_tauftauf.host_capacity --root "$HOME/recovery" status
```
"""

# The same command with the path the plant's own package lives under.
RESOLVING_PAGE = """# Host state

```sh
env PYTHONPATH=src python3 -m mishe_tauftauf.host_capacity --root "$HOME/recovery" status
```
"""

# A module the clone cannot import, because nothing puts src/ on the path.
UNIMPORTABLE_PAGE = """# Host state

```sh
python3 -m mishe_tauftauf.host_capacity --root "$HOME/recovery" status
```
"""

# An interpreter path the book never creates and the clone never receives.
ABSENT_INTERPRETER_PATH_PAGE = """# Judge

```sh
.venv/bin/python -m mishe_tauftauf.completions_judge --base-url https://x/v1 --model m
```
"""

# The import path comes from a variable the reader supplies, so the reader's
# own value decides whether it resolves.  The tree under test stands in for
# the reader's checkout, and it carries src/ -- so this one resolves.
READER_SUPPLIED_PAGE = """# Host state

```sh
PYTHONPATH="$CORE/src" python3 -m mishe_tauftauf --home "$SITE" init
```
"""

# The same shape, but the reader's checkout does not carry the directory the
# variable names, so the question cannot be decided here.
READER_SUPPLIED_UNRESOLVING_PAGE = """# Host state

```sh
PYTHONPATH="$CORE/vendor" python3 -m mishe_tauftauf --home "$SITE" init
```
"""

# A page that hands the reader no plant code at all.
NO_PLANT_CODE_PAGE = """# Words

```sh
tmux list-windows -t "$SESSION"
```
"""


def make_tree(tmp_path: Path, pages: dict[str, str]) -> Path:
    """A tree with pages for the check to read."""
    tree = tmp_path / "tree"
    for name, text in pages.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    # The plant package the commands import, so a command that states the
    # right import path really does resolve.
    package = tree / "src" / "mishe_tauftauf"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text('"""Plantable mishe-tauftauf runtime core."""\n',
                                         encoding="utf-8")
    for module in ("host_capacity", "completions_judge"):
        (package / f"{module}.py").write_text('"""Module."""\n', encoding="utf-8")
    return tree


def run_check(tree: Path) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(CHECK), "--tree", str(tree)]
    return subprocess.run(argv, capture_output=True, text=True)


def test_a_page_that_names_an_absent_interpreter_fails(tmp_path):
    """The measured defect must fail, and the verdict must name the page."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": ABSENT_INTERPRETER_PAGE})
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "docs/getting-started.md" in result.stdout
    assert "python" in result.stdout


def test_the_same_command_with_the_import_path_stated_passes(tmp_path):
    """The rule is about what the reader can run, not about the spelling."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": RESOLVING_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_module_the_clone_cannot_import_fails(tmp_path):
    """A correct interpreter does not help when the package is not on the path."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": UNIMPORTABLE_PAGE})
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "does not resolve" in result.stdout


def test_an_interpreter_path_the_clone_lacks_fails(tmp_path):
    """A venv the book never creates is not a file the reader has."""
    tree = make_tree(tmp_path, {"docs/completions-judge.md": ABSENT_INTERPRETER_PATH_PAGE})
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert ".venv/bin/python" in result.stdout


def test_a_reader_supplied_import_path_is_unknown_not_a_finding(tmp_path):
    """The reader's value for the variable is not knowable here."""
    tree = make_tree(tmp_path, {"skills/cultivate/SKILL.md": READER_SUPPLIED_UNRESOLVING_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_reader_supplied_import_path_that_resolves_passes(tmp_path):
    """When the reader's own checkout carries the directory, the command runs."""
    tree = make_tree(tmp_path, {"skills/cultivate/SKILL.md": READER_SUPPLIED_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_a_page_that_states_no_plant_code_is_unknown_not_a_pass(tmp_path):
    """A vacuous pass is not a pass."""
    tree = make_tree(tmp_path, {"docs/mesh.md": NO_PLANT_CODE_PAGE})
    result = run_check(tree)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_this_repository_satisfies_the_check_it_ships():
    """The shipped book must satisfy the gate the repository ships."""
    if shutil.which("git") is None:
        return
    result = subprocess.run(
        [sys.executable, str(CHECK), "--repo", str(REPO)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
