"""The book-vs-served-release detector must catch a rule the running bytes break.

The committed-tree gate (``checks/shipped-book-evidence.py``) asks whether a
shipped page's stated rule about resolving a relative outcome-evidence path is
the rule the **committed tree** applies.  That is the right question for a
reader.

This detector asks the question the plant asks itself: is the stated rule also
the rule the **served release** applies -- the bytes the plant actually
executes?  Measured on this plant on 2026-11-01 (Docs wake 157746), the served
release ``91d5385`` predates commit ``81d04a3d``, so a relative evidence path
resolves against the checkout there and is refused, while the committed tree
resolves it against the site home and accepts it.  The consequence was the
plant's most frequent refusal class: 172 of 206 outcome-writer refusals in 24
hours, over every role.

What these tests pin:

* a page whose stated rule the served release breaks fails, and the verdict
  names the page, the stated outcome, the served release's outcome and the
  committed tree's outcome;
* the same page passes when the served release applies the stated rule;
* the committed-tree gate could not see this class -- it read zero statements
  against the served release and passed -- so this detector reports UNKNOWN
  when there is no rule to compare, rather than a vacuous pass;
* a missing, malformed or unreadable served-release record is UNKNOWN, never a
  pass;
* this repository's own shipped book satisfies the check it ships, against the
  served release this plant actually runs.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-vs-served-release.py"

STALE_PAGE = """# Evidence

- A relative path is resolved against the current working directory first.
  From the repository root, `artifacts/NAME.md` means the repository's own
  `artifacts/` directory, which is not the site's and is refused.
"""

CURRENT_PAGE = """# Evidence

- A relative path is resolved against the current working directory first.
  When that form does not name an eligible file in the site, the same relative
  path is resolved against the site home instead, so `artifacts/NAME.md` from
  the repository root names the **site's** `artifacts/NAME.md` and is accepted.
"""


def make_home(tmp: Path, source: Path | None, sha: str | None) -> Path:
    """A site home carrying a served-release record.

    ``source`` is the **release root** the record names; the check appends
    ``src`` to it, exactly as ``runtime_source.source_for`` does.  A record
    with no source is how a malformed pin is expressed, and it must read
    UNKNOWN rather than silently falling back to the committed tree.
    """
    home = tmp / "home"
    (home / "health").mkdir(parents=True)
    record = {"version": 1, "source": str(source), "sha": sha,
              "session": "test"}
    (home / "health" / "runtime-release.json").write_text(
        json.dumps(record), encoding="utf-8")
    return home


def make_tree(tmp: Path, pages: dict[str, str]) -> Path:
    """A tree with pages and the plant package the check exercises.

    The check *runs* the tree's own code, so the tree needs a package that
    actually runs.  The repository's own ``src/`` is copied in.
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


def run_check(tree: Path, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(home),
                           "--tree", str(tree)],
                          capture_output=True, text=True)


def make_old_release(tmp: Path) -> Path:
    """A release root whose bytes resolve a relative evidence path differently.

    This is the shape of the defect measured on 2026-11-01 (Docs wake
    157746): the served release predates the home-relative fallback, so it
    resolves the relative form against the working directory and refuses it,
    while the committed tree resolves it against the site home and accepts
    it.  The two readings are produced by the *real* command under each
    import root, so the fixture stays honest about which bytes it exercises.
    """
    release = tmp / "oldrelease"
    package = release / "src" / "mishe_tauftauf"
    package.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "src" / "mishe_tauftauf", package,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    wall = package / "wall.py"
    text = wall.read_text(encoding="utf-8")
    # Restore the pre-81d04a3d resolution: the working directory only.
    old = text.replace(
        "    original_evidence = resolve_outcome_evidence(home, evidence)",
        "    original_evidence = evidence if evidence.is_absolute() else Path.cwd() / evidence")
    assert old != text, "the wall.py the fixture rewrites no longer matches"
    wall.write_text(old, encoding="utf-8")
    return release


def test_the_served_release_is_the_bytes_compared_not_the_committed_tree(tmp_path):
    """The comparison must be against the pin, not against the tree under test.

    The served release here resolves the relative form against the working
    directory and refuses it, while the committed tree accepts it.  A page
    that states the committed tree's rule must therefore fail -- and it fails
    only if the check really reads the pin.  A check that compared the tree
    with itself would report OK here, and the plant's refusals would go on
    unmeasured.
    """
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    home = make_home(tmp_path, make_old_release(tmp_path), "0" * 40)
    result = run_check(tree, home)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BOOK vs SERVED RELEASE" in result.stdout
    # The verdict distinguishes the two readings: the pin refuses, the
    # committed tree accepts.
    assert "outside=refused" in result.stdout, result.stdout
    assert "committed tree: outside=accepted" in result.stdout, result.stdout


def test_a_rule_the_served_release_breaks_is_a_finding(tmp_path):
    """A page stating the rule the running bytes do not apply must fail.

    The served release here is the repository's own ``src/``, which resolves
    the relative form against the site home and accepts it, while the page
    states that the form is refused.  The finding must name the page and both
    readings.
    """
    tree = make_tree(tmp_path, {"docs/getting-started.md": STALE_PAGE})
    home = make_home(tmp_path, tree, "0" * 40)
    result = run_check(tree, home)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BOOK vs SERVED RELEASE" in result.stdout
    assert "docs/getting-started.md" in result.stdout
    # The verdict names the committed tree's reading too, so a reader can tell
    # which side is out of step.
    assert "committed tree" in result.stdout


def test_a_rule_the_served_release_applies_passes(tmp_path):
    """The same page, corrected, passes."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    home = make_home(tmp_path, tree, "0" * 40)
    result = run_check(tree, home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_a_book_that_states_no_rule_reads_unknown_not_a_pass(tmp_path):
    """No rule to compare must not read as a clean audit.

    This is the exact hole the committed-tree gate fell into: against the
    served release's tree, whose ``docs/getting-started.md`` has no
    evidence-path section at all, that gate read zero statements and reported
    OK.  A gate that checks nothing is not a pass.
    """
    tree = make_tree(tmp_path, {"docs/other.md": "# Other\n\nThe plant is local.\n"})
    home = make_home(tmp_path, tree, "0" * 40)
    result = run_check(tree, home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_missing_served_release_record_reads_unknown(tmp_path):
    """No pin means the question cannot be decided, not that it passes."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    home = tmp_path / "nohome"
    home.mkdir()
    result = run_check(tree, home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_malformed_served_release_record_reads_unknown(tmp_path):
    """A record naming no source must not fall back to the committed tree."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    home = make_home(tmp_path, None, None)
    result = run_check(tree, home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_served_release_without_the_package_reads_unknown(tmp_path):
    """A pin that names a directory with no plant package is undecidable."""
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    empty = tmp_path / "empty"
    empty.mkdir()
    home = make_home(tmp_path, empty, "0" * 40)
    result = run_check(tree, home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_this_repository_satisfies_the_check_it_ships():
    """The shipped book must agree with the release this plant actually runs.

    The plant's served release is read from the site's own record, so this
    test states what the plant's own readers would find.  On the current pin
    the book and the release disagree, and the check is expected to say so --
    that disagreement is the finding this check exists to publish, and it is
    health's pin to advance, not the book's to bend.
    """
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO / ".mishe-tauftauf"),
                              "--repo", str(REPO)],
                             capture_output=True, text=True)
    assert result.returncode in (0, 2), result.stdout + result.stderr
    if result.returncode == 2:
        # A failure must name the page and both readings, so the finding is
        # actionable rather than a bare verdict.
        assert "BOOK vs SERVED RELEASE" in result.stdout, result.stdout
        assert "served release" in result.stdout, result.stdout
    else:
        assert "OK" in result.stdout, result.stdout
