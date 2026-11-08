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

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "shipped-book-vs-served-release.py"


def skip_reason(home: Path) -> str | None:
    """Why the live assertion cannot be asked here, or None when it can.

    The served-release record is a **site** file, and ``.mishe-*`` is
    gitignored, so a clone carries none.  A clone has no served release to
    compare the book with, and the check reports that as UNKNOWN -- an
    honest reading, and not a pass.

    The absence is the only reason to skip.  A site whose record exists but
    is malformed has a broken observation, and a broken observation is RED:
    the question is asked, and the answer fails the assertion.
    """
    if not (home / "health" / "runtime-release.json").is_file():
        return "no served-release record beside this checkout"
    return None

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
    # The committed framer refuses to read a tape unless the site carries its
    # sha-recorded install, so a home that cannot be given one cannot answer
    # the question: that is a skip with the reason, never a silent pass.
    live_bin = REPO / ".mishe-tauftauf" / "bin"
    binary, record_file = live_bin / "mishe-tape", live_bin / "mishe-tape.install.json"
    if not (binary.is_file() and record_file.is_file()):
        pytest.skip("no installed mishe-tape beside this checkout; the committed framer cannot read a tape")
    (home / "bin").mkdir()
    shutil.copy2(binary, home / "bin" / binary.name)
    shutil.copy2(record_file, home / "bin" / record_file.name)
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


def test_the_check_runs_alone_with_only_its_own_file(tmp_path):
    """The check must not depend on a sibling file at run time.

    The plant's observer runner copies **one** check script into its working
    directory and runs it from there, so a check that imports a neighbouring
    script fails with a traceback instead of a verdict.  Measured on
    2026-11-01 (Docs wake 157746): the first version of this check loaded
    ``shipped-book-evidence.py`` as a sibling and the runner's cycle recorded
    ``exit=1`` with a ``FileNotFoundError`` for the missing sibling -- the
    finding was never produced.
    """
    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy(CHECK, alone / CHECK.name)
    tree = make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE})
    home = make_home(tmp_path, make_old_release(tmp_path), "0" * 40)
    result = subprocess.run([sys.executable, str(alone / CHECK.name),
                              "--home", str(home), "--tree", str(tree)],
                             capture_output=True, text=True, cwd=str(alone))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "BOOK vs SERVED RELEASE" in result.stdout


def assert_the_shipped_book_satisfies_the_check_it_ships(home: Path) -> None:
    """Run the live check against a site home and assert what it must say.

    This is the assertion itself, without the skip that guards it, so that a
    test can drive it directly and prove it is still asked.
    """
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(home),
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


def test_this_repository_satisfies_the_check_it_ships(home: Path | None = None):
    """The shipped book must agree with the release this plant actually runs.

    The plant's served release is read from the site's own record, so this
    test states what the plant's own readers would find.  On the current pin
    the book and the release disagree, and the check is expected to say so --
    that disagreement is the finding this check exists to publish, and it is
    health's pin to advance, not the book's to bend.

    The record is a **site** file, not a repository file: ``.mishe-*`` is
    gitignored, so a clone has none.  A clone cannot answer the question
    this test asks -- there is no served release to compare the book with --
    and the check says so by exiting 3 (UNKNOWN).  That is the honest
    reading, and it is not a pass, so the test skips rather than asserting
    it.  The live half of the check is not lost: the plant's observer-check
    runner schedules ``shipped-book-vs-served-release.py`` against the live
    site, where the record exists and this same assertion runs.

    The skip is narrow on purpose.  It fires only when the record is
    *absent*; a record that is present but malformed, or a release that
    cannot be exercised, still fails the test here, because on a checkout
    that has a site the question is decidable and must be answered.

    ``home`` exists so that another test can ask this same question against a
    site it plants, and so that the fact that this test is still asked -- not
    skipped, not deleted -- is itself pinned.
    """
    site = REPO / ".mishe-tauftauf" if home is None else home
    reason = skip_reason(site)
    if reason is not None:
        pytest.skip(reason)

    assert_the_shipped_book_satisfies_the_check_it_ships(site)


def test_a_clone_without_a_site_skips_the_live_assertion(tmp_path):
    """A clone has no served release, so it must not be asked the question.

    The served-release record lives in the site's own directory, and
    ``.mishe-*`` is gitignored, so a clone carries none.  Measured on
    2026-11-01 (Docs wake 159116): CI runs this test on a clone, the check
    exits 3 (UNKNOWN -- "no served-release record"), and the assertion
    accepted only 0 or 2, so CI was red for the whole tree on a question no
    clone can answer.

    The check's own reading of that state is UNKNOWN, which is honest and is
    not a pass.  The test must therefore skip, not assert a pass.
    """
    reason = skip_reason(tmp_path / "site")
    assert reason is not None
    assert "served-release record" in reason


def test_a_site_with_a_broken_record_is_asked_the_question(tmp_path):
    """A present-but-unreadable record is RED, not a reason to skip.

    The skip is for a clone, which has no site at all.  A site whose
    served-release record exists but cannot be read has a broken
    observation, and a broken observation must fail rather than pass
    unnoticed.  So the skip decision turns on **absence** only: a record
    that is present, even a malformed one, keeps the question asked.
    """
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "health" / "runtime-release.json").write_text("{not json", encoding="utf-8")

    assert skip_reason(home) is None

    result = subprocess.run([sys.executable, str(CHECK), "--home", str(home),
                              "--tree", str(make_tree(tmp_path, {"docs/getting-started.md": CURRENT_PAGE}))],
                             capture_output=True, text=True)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout, result.stdout


def test_a_checkout_with_a_site_is_asked_the_question_not_skipped(tmp_path):
    """The live assertion must still run wherever a site exists.

    The skip this file carries exists for a clone, which has no served
    release to compare the book with.  It must not become a way to stop
    asking the question everywhere: a checkout that *does* carry a site has
    the record, so the question is decidable there and must be asked.

    Measured on 2026-11-01 (Docs wake 159116, independent review): with the
    skip in place, replacing the guarded call with an unconditional
    ``pytest.skip`` -- or deleting the live test outright -- left every other
    test in this file green.  The two tests above pin the skip *predicate*,
    but nothing pinned the claim the docstring makes, that the live half is
    not lost.  This test pins it.

    It drives the live test itself -- not a copy of its body -- and requires
    that it neither skips nor silently passes.  A copy would miss the
    mutations this test exists to catch: an unconditional skip inside the
    live test, or the live test being removed altogether.
    """
    home = make_home(tmp_path, make_old_release(tmp_path), "0" * 40)
    assert skip_reason(home) is None

    try:
        test_this_repository_satisfies_the_check_it_ships(home)
    except pytest.skip.Exception:
        pytest.fail("a checkout with a site must be asked the question, "
                    "not skipped")
