"""The shipped-book unit-pattern detector must catch a check a reader cannot pass.

The detector reads a shipped page's **unit pattern** -- the glob or unit name
it hands the reader for ``systemctl --user list-units`` -- and asks whether
that pattern can name a unit the plant actually installed.

Measured on this plant on 2026-11-01 (Docs wake 163304): ``docs/getting-
started.md`` told a reader to confirm the resident units were active with
``systemctl --user list-units 'SITE-*.service'``.  ``$SITE`` is that page's
**site directory** (``SITE="$PWD/.mishe-seed"``), while a unit is named
``SESSION-ROLE.service``.  The pattern therefore matched **zero** of the 40
units on the host, and the reader's own acceptance check answered "0 loaded
units listed" for a perfectly healthy plant.

What these tests pin:

* a page that prefixes a unit pattern with the site's directory fails, and the
  verdict names the page, the pattern and the count of units it matched;
* a page that uses the same words for a **session** rather than a directory
  passes -- the rule is about the directory, not the spelling;
* a page that states no unit pattern is UNKNOWN rather than a vacuous pass;
* a site with no manifest, or a manifest naming no units, is UNKNOWN;
* this repository's own shipped book satisfies the check it ships.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-unit-pattern.py"

# The defect: the site directory used where the session belongs.  The page
# defines SITE as a path, exactly as docs/getting-started.md does.
DIRECTORY_PREFIXED_PAGE = """# Units

SITE="$PWD/.mishe-seed"

Check the resident units with:

```bash
systemctl --user list-units 'SITE-*.service'
```
"""

# The same page with the session name the plant actually raises.
SESSION_PREFIXED_PAGE = """# Units

SITE="$PWD/.mishe-seed"
SESSION='mishe-seed'

Check the resident units with:

```bash
systemctl --user list-units "$SESSION-*.service"
```
"""

# The same words used for a session rather than a directory.  The rule is
# about the directory, not about the spelling.
SESSION_NAMED_PAGE = """# Units

SESSION='mishe-seed'

Check the resident units with:

```bash
systemctl --user list-units 'SESSION-*.service'
```
"""

# A page that names no unit pattern at all.
NO_PATTERN_PAGE = """# Units

The resident units are listed in the site's own manifest.
"""


def make_home(tmp: Path, units: list[str] | None) -> Path:
    """A site home carrying a services manifest."""
    home = tmp / "home"
    (home / "health").mkdir(parents=True)
    if units is not None:
        (home / "health" / "services.json").write_text(
            json.dumps(units), encoding="utf-8")
    return home


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    """A tree with pages for the check to read.

    The check plants its own site against the tree under test when no
    ``--home`` is given, so the tree needs the plant package whose
    unit-naming code names the units.
    """
    package = tmp / "tree" / "src" / "mishe_tauftauf"
    package.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "src" / "mishe_tauftauf", package,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name, body in pages.items():
        path = tmp / "tree" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp / "tree"


def run_check(tree: Path, home: Path | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(CHECK), "--tree", str(tree)]
    if home is not None:
        argv += ["--home", str(home)]
    return subprocess.run(argv, capture_output=True, text=True)


def test_a_page_that_prefixes_a_unit_with_the_site_directory_fails(tmp_path):
    """The measured defect must fail, and the verdict must name the page."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": DIRECTORY_PREFIXED_PAGE})
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "docs/getting-started.md" in result.stdout
    assert "SITE-*.service" in result.stdout
    # The count is part of the finding: the pattern selected nothing.
    assert "0 manifest unit(s)" in result.stdout


def test_a_page_that_prefixes_a_unit_with_the_session_passes(tmp_path):
    """The session is the right prefix, so the page must pass."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": SESSION_PREFIXED_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_the_same_words_used_for_a_session_are_not_a_finding(tmp_path):
    """The rule is about the directory, not about the spelling of the prefix."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": SESSION_NAMED_PAGE})
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_page_that_states_no_unit_pattern_is_unknown_not_a_pass(tmp_path):
    """A vacuous pass is not a pass."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": NO_PATTERN_PAGE})
    result = run_check(tree)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_site_with_no_manifest_is_unknown(tmp_path):
    """Without the manifest there is no unit set to compare against."""
    home = make_home(tmp_path, None)
    tree = make_tree(tmp_path, {"docs/getting-started.md": SESSION_PREFIXED_PAGE})
    result = run_check(tree, home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_manifest_naming_no_units_is_unknown(tmp_path):
    """An empty manifest cannot decide the question either."""
    home = make_home(tmp_path, [])
    tree = make_tree(tmp_path, {"docs/getting-started.md": SESSION_PREFIXED_PAGE})
    result = run_check(tree, home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_this_repository_satisfies_the_check_it_ships():
    """The shipped book must satisfy the gate the repository ships.

    The check plants its own site against the committed tree, so this runs
    on a clone that has never planted anything.
    """
    result = subprocess.run(
        [sys.executable, str(CHECK), "--repo", str(REPO)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
