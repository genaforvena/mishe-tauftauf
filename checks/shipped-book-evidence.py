#!/usr/bin/env python3
"""Detector: a shipped page's evidence rule must be the one the clone runs.

``checks/shipped-book-links.py`` asks whether the reader can *reach* what a
page points at.  ``checks/shipped-book-paths.py`` asks whether the path a
page names exists in the clone.  ``checks/shipped-book-promises.py`` asks
whether the page a promise points at *contains* what the promise names.
``checks/shipped-book-runtimes.py`` asks whether a runtime a page describes is
reachable from the committed tree.

None of them asks whether the *rule* a page states is the rule the committed
code applies.  A page can state a rule that is true of the running plant and
false of the clone the reader receives, and every existing gate passes it:
the path it names exists, the link resolves, the command is named.

Measured on this plant on 2026-11-01 (Docs wake 151606): the shipped
``docs/getting-started.md`` told the reader that a relative ``--evidence``
path is resolved against the working directory and that
``artifacts/NAME.md`` from the repository root "is not the site's and is
refused".  Commit ``81d04a3d`` ("wall: resolve relative outcome evidence
home-relative when the cwd form is not eligible", 2026-10-31) had already
changed that rule -- a relative path that is not eligible in the working
directory is resolved against the site home instead -- and the page was
written *after* it (``fdb1f72c``, 2026-10-31 22:41) from the plant's own
**pinned release**, which still carried the old rule.  So the book
described the pinned runtime, not the clone.

The reader's experience, measured through the real CLI from a materialised
clone, with the working directory outside the site:

* the committed tree: ``--evidence artifacts/NAME.md`` **publishes**, and the
  bound record names the *site's* ``artifacts/NAME.md``;
* the pinned release ``91d5385``: the same command is **refused** with
  "outcome evidence must be an eligible single file under artifacts/...".

Both readings were true at once, and the book stated only the older one.

The rule this check enforces:

* a shipped page may state how a **relative evidence path** is resolved;
* when it does, the statement must agree with the rule the **committed tree**
  applies -- not with any release, and not with the working tree.

The rule is read from the committed tree by **executing** it: the check
materialises the committed bytes, plants a site against them, and runs the
real ``wall outcome`` command twice -- once with the working directory
outside the site (the reader's position) and once inside it.  The observed
outcomes are what the page must match.

Two outcomes are compared, because a page may legitimately describe either:

* **outside** -- the relative form is run from a directory outside the site;
* **inside** -- the same relative form is run from the site home.

A page that says the outside form is refused must not also be the page a
reader follows when the outside form is accepted, and the reverse.  The
check compares the page's stated outcome against the observed one.

Scope and limits, named:

* Only pages that **state an outcome for a relative evidence path** are read.
  A page that says nothing about relative paths is not a finding; the plant
  has many pages and most of them are about other things.
* The stated outcome is read from a small, explicit vocabulary of phrases
  ("is refused", "is accepted", "names the site's ... and is accepted").  A
  page that describes the rule in prose the vocabulary does not cover is not
  a finding, and that is a known limit rather than a cleared question.
* The check reads the **committed** bytes through ``git archive``, so an
  untracked working-tree file cannot make a stale page look current.
* The check runs the real command, so it is bounded by what a site needs to
  publish an outcome: a tape, a records directory and an eligible artifact.
  When the planted site cannot reach the publishing path at all, the reading
  is UNKNOWN (exit 3), never a pass.
* The check compares the *rule*, not the wording.  A page that describes the
  rule correctly in its own words still passes, because the check looks for
  the outcome the page states, not for a sentence it expects.
* Only the **eligibility** refusal is the rule's outcome.  "outcome evidence
  path already bound" is a different refusal -- the single-use evidence
  contract -- and reading it as the rule's outcome would turn a re-bound
  artifact into a rule difference.  Each reading uses its own artifact name,
  so the contract cannot fire; if it does, the reading is UNKNOWN.

Exit status:
  0  every stated evidence-resolution outcome matches the committed tree
  2  a shipped page states an outcome the committed tree does not produce
  3  the question cannot be decided: no tree, no git, or the tree cannot be run
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT = "/usr/bin/git"

BOOK_ROOTS = ("docs/", ".agents/skills/", "skills/")
BOOK_FILES = ("README.md",)

# The phrases a page may use to state the outcome of a relative evidence path.
# Each entry is (marker, outcome) where outcome is "refused" or "accepted".
STATEMENTS = (
    (re.compile(r"\bis refused\b", re.I), "refused"),
    (re.compile(r"\bare refused\b", re.I), "refused"),
    (re.compile(r"\bis accepted\b", re.I), "accepted"),
    (re.compile(r"\bare accepted\b", re.I), "accepted"),
    (re.compile(r"\bnames the site'?s\b", re.I), "accepted"),
)

# The rule this check reads: how a **relative** evidence path is resolved.
#
# The scope is narrow on purpose.  A page states many rules about evidence --
# that a nested path is refused, that a reused path is refused, that a
# malformed digest is refused -- and each of those is a rule about its own
# shape, not about *where a relative path is resolved*.  Reading them all
# would make this check cry wolf on every page that states any rule at all.
#
# So a statement is read only where the text is about resolving a relative
# path: it names the resolution (the working directory, the site home), and
# it states an outcome.
RELATIVE_RULE = re.compile(
    r"(resolved|resolves|resolution|names the site'?s|working directory|"
    r"current working directory)", re.I)

# A sentence that talks about a relative evidence path at all.
RELATIVE_EVIDENCE = re.compile(
    r"(--evidence|evidence path|relative path|relative form)", re.I)

BLOCK = re.compile(r"\n\s*\n")
FENCE = re.compile(r"```.*?```", re.DOTALL)

# A list item, which starts a new rule.
BULLET = re.compile(r"^\s*([-*+]|\d+\.)\s")

# A sentence, for reading a rule's outcome.  The split keeps the terminating
# punctuation so a reader can see where the sentence ended.
SENTENCE = re.compile(r"(?<=[.;:])\s+|\n")

# A sentence that names the relative form whose outcome is being stated.
RELATIVE_FORM = re.compile(
    r"(relative path|relative form|`artifacts/|\bNAME\.md\b|--evidence)", re.I)


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-evidence-")
    archive = subprocess.run([GIT, "-C", str(repo), "archive", "HEAD"],
                             capture_output=True)
    if archive.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, archive.stderr.decode("utf-8", "replace").strip()
    unpack = subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout,
                            capture_output=True)
    if unpack.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, unpack.stderr.decode("utf-8", "replace").strip()
    return Path(tmp), ""


def _package_dir(tree: Path) -> Path | None:
    """The plant package inside a tree, if the tree carries one."""
    for candidate in (tree / "src" / "mishe_tauftauf", tree / "mishe_tauftauf"):
        if candidate.is_dir():
            return candidate
    return None


def _book_pages(tree: Path) -> list[Path]:
    """The pages of the shipped book, in a stable order."""
    pages = []
    for path in sorted(tree.rglob("*.md")):
        name = path.relative_to(tree).as_posix()
        if name in BOOK_FILES or name.startswith(BOOK_ROOTS):
            pages.append(path)
    return pages


def _logical_units(block: str) -> list[str]:
    """The rules a block states, one per bullet or paragraph line.

    Markdown hard-wraps a paragraph, so the lines of one rule are several
    physical lines.  A bullet starts a new rule; the lines that follow it,
    indented or not, belong to that rule.  Joining them first is what lets a
    reader's rule be read as the reader wrote it.
    """
    units: list[str] = []
    current: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if BULLET.match(line) and current:
            units.append(" ".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        units.append(" ".join(current))
    return units


def _stated_outcomes(page: Path) -> list[dict]:
    """The outcomes a page states for a relative evidence path.

    A fenced block is an example, not a claim about the rule, so it is not
    read.

    The unit of reading is the **rule**, not the paragraph and not the
    sentence.  A bullet list of evidence rules states several rules in one
    block -- "a nested path is refused", "a relative path resolves against
    the working directory", "a reused path is refused" -- and those are
    different rules with different answers.  Reading the block would attach
    the nested path's refusal to the resolution rule and report a page that
    is correct about resolution as if it were wrong.

    The sentence is not the unit either: a rule often states its outcome in
    a later sentence than the one that names the resolution ("A relative
    path is resolved against the working directory first.  From the
    repository root, `artifacts/NAME.md` ... is refused.").  The outcome
    belongs to the rule, so the rule is what is read.
    """
    try:
        text = page.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    prose = FENCE.sub("", text)
    found: list[dict] = []
    for block in BLOCK.split(prose):
        if not block.strip():
            continue
        for unit in _logical_units(block):
            unit = unit.strip().lstrip("-*").strip()
            if not unit:
                continue
            if not RELATIVE_RULE.search(unit):
                continue
            if not RELATIVE_EVIDENCE.search(unit):
                continue
            outcome = _outcome_of_relative_form(unit)
            if outcome is None:
                continue
            found.append({"outcome": outcome,
                          "claim": " ".join(unit.split())[:300]})
    return found


def _outcome_of_relative_form(unit: str) -> str | None:
    """The outcome a rule states for the relative form it names.

    A rule can state several outcomes -- the relative form is accepted, and a
    path eligible under neither form is refused -- and they are about different
    paths.  So the outcome is read from the sentence that **names the
    relative form**, not from the rule as a whole: the first outcome that
    sentence states is the outcome for that path.

    A rule whose sentences name a relative form but state no outcome for it
    is not read: the page describes the rule without saying what happens.
    """
    for sentence in SENTENCE.split(unit):
        sentence = sentence.strip()
        if not sentence or not RELATIVE_FORM.search(sentence):
            continue
        for pattern, outcome in STATEMENTS:
            match = pattern.search(sentence)
            if match:
                return outcome
    return None


def _tape_install(live_home: Path | None) -> tuple[Path, Path] | None:
    """The live site's sha-recorded mishe-tape install, or None when absent.

    The feed reader frames every tape read through ``$SITE/bin/mishe-tape`` and
    refuses to run unless the binary matches its recorded sha256.  A freshly
    planted site has neither, so the committed tree cannot read a tape or
    publish an outcome there and every reading is UNKNOWN.  The live install is
    copied in, so the framer's own integrity gate still applies.
    """
    if live_home is None:
        return None
    binary = live_home / "bin" / "mishe-tape"
    record = live_home / "bin" / "mishe-tape.install.json"
    if not (binary.is_file() and record.is_file()):
        return None
    return binary, record


def _plant_site(tree: Path, work: Path, live_home: Path | None = None) -> Path:
    """Plant a site against the committed tree and return its home.

    The site is planted with the committed bytes on ``PYTHONPATH``, so the
    CLI it runs is the one a reader who cloned this tree would run.
    """
    site_root = work / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "init", "-q", str(site_root)], capture_output=True)
    home = site_root / ".mishe-tauftauf"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    env["MISHE_SEED_HOME"] = str(home)
    subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                    "init"], env=env, capture_output=True, cwd=str(work))
    for name in ("walls", "artifacts", "records"):
        (home / name).mkdir(exist_ok=True)
    install = _tape_install(live_home)
    if install is not None:
        (home / "bin").mkdir(exist_ok=True)
        for source in install:
            shutil.copy2(source, home / "bin" / source.name)
    # A tape entry and a records directory are what let an outcome publish;
    # without them the command fails closed for an unrelated reason and the
    # reading would be UNKNOWN rather than a comparison.
    subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
                    "append", "--source", "docs", "reader notes"],
                   env=env, capture_output=True, cwd=str(work))
    return home


def _run_outcome(home: Path, tree: Path, work: Path, cwd: Path, artifact: str) -> str:
    """Run one real ``wall outcome`` and report what happened.

    Returns "accepted", "refused" or "unknown".  "unknown" means the command
    failed for a reason the check cannot interpret -- the reading is then
    UNKNOWN, never a pass.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    env["MISHE_SEED_HOME"] = str(home)
    notes = home / "walls" / "docs-NOTES.md"
    notes.write_text("reader notes\n", encoding="utf-8")
    (home / "artifacts" / artifact).write_text("evidence body\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
         "wall", "outcome", "--owner", "docs", "--kind", "accepted",
         "--file", "NOTES.md", "--evidence", f"artifacts/{artifact}"],
        env=env, capture_output=True, text=True, cwd=str(cwd))
    if result.returncode == 0:
        return "accepted"
    message = (result.stdout + result.stderr).lower()
    if "must be an eligible single file" in message:
        return "refused"
    # "outcome evidence path already bound" is a **different** refusal: the
    # single-use evidence contract, not the rule about where a relative path
    # resolves.  Treating it as the rule's outcome would make the check read
    # a re-bound artifact as a rule difference, so it is not this rule's
    # outcome and the reading is UNKNOWN.
    if "already bound" in message:
        return "unknown"
    return "unknown"


def observe(tree: Path, live_home: Path | None = None) -> dict:
    """Observe what the committed tree does with a relative evidence path.

    Two readings, from the two working directories a reader can be in:
    outside the site (a checkout) and inside it.
    """
    work = Path(tempfile.mkdtemp(prefix="shipped-book-evidence-run-"))
    try:
        home = _plant_site(tree, work, live_home)
        outside = work / "checkout"
        outside.mkdir(exist_ok=True)
        readings = {
            "outside": _run_outcome(home, tree, work, outside, "OUTSIDE.md"),
            "inside": _run_outcome(home, tree, work, home, "INSIDE.md"),
        }
        return readings
    finally:
        shutil.rmtree(work, ignore_errors=True)


def audit(tree: Path, live_home: Path | None = None) -> dict:
    """Find pages that state an outcome the committed tree does not produce."""
    if _package_dir(tree) is None:
        raise ValueError("no plant package in tree")
    readings = observe(tree, live_home)
    pages = _book_pages(tree)
    findings: list[dict] = []
    checked = 0
    for page in pages:
        source = page.relative_to(tree).as_posix()
        for statement in _stated_outcomes(page):
            checked += 1
            # A statement about the relative form is judged against the
            # reading taken from outside the site: that is the position the
            # statement is about, and the one a reader meets first.
            observed = readings["outside"]
            if statement["outcome"] == observed:
                continue
            findings.append({
                "file": source,
                "stated": statement["outcome"],
                "observed": observed,
                "claim": statement["claim"],
            })
    return {"book_pages": len(pages), "statements": checked,
            "readings": readings, "findings": findings}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True,
                        help="site home (kept for the observer-checks runner contract)")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository; the committed bytes of HEAD are tested")
    parser.add_argument("--tree", type=Path, default=None,
                        help="an explicit tree to test instead of the committed bytes")
    args = parser.parse_args(argv)

    tmp: str | None = None
    if args.tree:
        tree = args.tree.resolve()
    elif args.repo:
        tree, error = _committed_tree(args.repo.resolve())
        if tree is None:
            print("cannot read committed bytes: " + error)
            return 3
        tmp = str(tree)
    else:
        print("no tree and no repo given")
        return 3

    try:
        result = audit(tree, args.home.resolve())
        print(f"tree={tree}")
        print(f"book pages={result['book_pages']} "
              f"statements={result['statements']} "
              f"observed outside={result['readings']['outside']} "
              f"inside={result['readings']['inside']}")
        for item in result["findings"]:
            print(f"  STALE EVIDENCE RULE  {item['file']} states the relative "
                  f"form is {item['stated']}, but the committed tree {item['observed']} it")
            print(f"    {item['claim']}")
        if "unknown" in result["readings"].values():
            print("UNKNOWN — the committed tree could not be exercised, so the "
                  "rule it applies is not observed")
            return 3
        if not result["findings"]:
            print("OK every stated evidence-resolution outcome matches the "
                  "committed tree")
            return 0
        print("FAIL a shipped page states an evidence rule the committed tree "
              "does not apply")
        return 2
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print(f"UNKNOWN — shipped-book evidence check unavailable: {exc}")
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
