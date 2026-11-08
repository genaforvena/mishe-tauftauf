#!/usr/bin/env python3
"""Detector: the shipped book must not state a rule the served release breaks.

``checks/shipped-book-evidence.py`` asks whether a shipped page's stated
evidence-resolution rule is the rule the **committed tree** applies.  It
plants a site against the committed bytes and runs the real ``wall outcome``
command there.  That is the right question for a reader: a clone receives the
committed bytes.

It is not the question the plant asks itself.  The plant runs a **pinned
release**, and the pin moves on its own schedule.  A page can be correct about
the committed tree and wrong about the bytes the plant actually executes, and
every existing gate passes it: the committed tree agrees with the page, the
link resolves, the command is named, and the CI run is green.

Measured on this plant on 2026-11-01 (Docs wake 157746):

* the served release is ``91d5385`` (2026-10-30 04:58);
* commit ``81d04a3d`` ("wall: resolve relative outcome evidence home-relative
  when the cwd form is not eligible", 2026-10-31 11:39) changed that rule, and
  it is on origin/main and an ancestor of HEAD -- but **not** an ancestor of
  the pinned release;
* the shipped ``docs/getting-started.md`` states the committed tree's rule
  ("a relative path is resolved against the site home instead, so
  ``artifacts/NAME.md`` from the repository root ... is accepted");
* the pinned release resolves a relative evidence path against ``Path.cwd()``
  only, so that same command is **refused** there:

      mishe-tauftauf: outcome evidence must be an eligible single file under
      artifacts/, discovery/scan-*.json or dms/*.md; symlinks and traversal
      are forbidden

* the consequence was the plant's most frequent refusal class: **172 of 206**
  outcome-writer refusals in 24 hours, spread over every role (witness 43,
  field-review 30, discover 26, senses 17, pub 17, genome 13, health 12,
  docs 14).  Every mind that named its evidence by a relative path was
  refused, and the relative form is the one the book names.

The committed-tree gate could not see this, and it did not merely miss it --
it read **zero statements** against the served release and passed.  The
served release's tree carries the older ``docs/getting-started.md``, which
has no evidence-path section at all, so the gate had nothing to compare and
reported OK.  A gate that checks nothing is a clean audit of nothing, which is
the same vacuous pass this repository removed in Docs wake 155786.

The rule this check enforces:

* a shipped page may state how a **relative evidence path** resolves;
* when it does, the statement must agree with the rule the **served release**
  applies -- the bytes the plant executes -- and not only with the committed
  tree.

Both readings are taken by **executing** the rule.  The check materialises the
committed bytes, plants a site against them, and runs the real ``wall
outcome`` command twice -- once with the working directory outside the site
(the reader's position) and once inside it.  The same probe is then run
against the served release's own import root.  The observed outcomes are what
the page must match.

Two outcomes are compared, because a page may legitimately describe either
one: the rule can differ by working directory, and a page that states one
rule for the outside position and another for the inside position is right.

A difference is a **finding**, not a failure of the page: the page describes
the clone, the plant runs a release, and the gap between them is the
measurement this check exists to publish.  The finding names both sides so a
reader of the verdict knows which of the two is out of step.

Exit status:
  0  every stated evidence-resolution outcome matches the served release
  2  a shipped page states an outcome the served release contradicts
  3  the question cannot be decided: no tree, no git, no served-release
     record, an unreadable release, or bytes that answer for another import
     root

The verdict is the last line of the output; the lines before it say what was
compared.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT = "/usr/bin/git"

# The same book roots and files the committed-tree gate reads.  The served
# release's tree is a full checkout, so it carries pages the clone does not
# (the gitignored operating records).  Those pages are read here for the same
# reason the committed-tree gate reads them: a page that exists only in a
# working tree is not a page a clone receives, but it *is* a page a reader of
# the plant's own checkout can read, so it is in scope for the served-release
# reading.  The committed-tree gate remains the authority on the clone.
BOOK_ROOTS = ("docs/", ".agents/skills/", "skills/")
BOOK_FILES = ("README.md",)

# How a shipped page may state the outcome of a relative evidence path.
# Each entry is (marker, outcome) where outcome is "refused" or "accepted".
STATEMENTS = (
    (re.compile(r"\bis refused\b", re.I), "refused"),
    (re.compile(r"\bare refused\b", re.I), "refused"),
    (re.compile(r"\bis accepted\b", re.I), "accepted"),
    (re.compile(r"\bare accepted\b", re.I), "accepted"),
    (re.compile(r"\bnames the site'?s\b", re.I), "accepted"),
)

# A sentence is read only where the text is about resolving a relative path:
# it names the resolution (the working directory, the site home) and states
# an outcome.
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


def _book_pages(tree: Path) -> list[Path]:
    """The pages of the shipped book, in a stable order."""
    pages = []
    for path in sorted(tree.rglob("*.md")):
        name = path.relative_to(tree).as_posix()
        if name in BOOK_FILES or name.startswith(BOOK_ROOTS):
            pages.append(path)
    return pages


def _logical_units(block: str) -> list[str]:
    """The rules a block states, one per bullet or paragraph line."""
    units = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if BULLET.match(line):
            units.append(stripped)
            continue
        if not units:
            units.append(stripped)
        else:
            units[-1] += " " + stripped
    return units


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
            if pattern.search(sentence):
                return outcome
    return None


def _stated_outcomes(page: Path) -> list[dict]:
    """The outcomes a page states for a relative evidence path.

    A fenced block is an example, not a claim about the rule, so it is not
    read.

    The unit of reading is the **rule**, not the paragraph and not the
    sentence.  A bullet list of evidence rules states several rules in one
    block -- "a nested path is refused", "a relative path resolves against the
    working directory", "a reused path is refused" -- and those are different
    rules with different answers.  Reading the block would attach the nested
    path's refusal to the resolution rule and report a page that is correct
    about resolution as if it were wrong.

    The sentence is not the unit either: a rule often states its outcome in
    a later sentence than the one that names the resolution.  The outcome
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


def _package_dir(tree: Path) -> Path | None:
    """The plant package inside a tree, if the tree carries one."""
    for path in (tree / "src" / "mishe_tauftauf", tree / "mishe_tauftauf"):
        if path.is_dir():
            return path
    return None


def _tape_install(live_home: Path | None) -> tuple[Path, Path] | None:
    """The live site's installed mishe-tape binary and its install record.

    The feed reader frames every tape read through ``$SITE/bin/mishe-tape`` and
    refuses to run unless the sha256 in ``mishe-tape.install.json`` matches the
    binary.  A freshly planted site has neither, so the committed tree cannot
    read a tape or publish an outcome there.  The check copies the live,
    sha-recorded install in, so the framer's own integrity gate still applies.
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
    subprocess.run(["git", "init", "-q", str(site_root)], capture_output=True)
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



def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-vs-pin-")
    archive = subprocess.run([GIT, "-C", str(repo), "archive", "HEAD"],
                             capture_output=True)
    if archive.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, archive.stderr.decode("utf-8", "replace").strip() or "git archive failed"
    unpack = subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout,
                            capture_output=True)
    if unpack.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, "tar failed: " + unpack.stderr.decode("utf-8", "replace").strip()
    return Path(tmp), ""


def served_release_src(home: Path) -> tuple[Path, str]:
    """The import root of the bytes the plant is pinned to.

    The record is the site's own served-release record, the same authority
    ``runtime_source.source_for`` reads.  A missing or malformed record leaves
    the question undecidable rather than answered by a default: a plant that
    has not pinned a release runs its own checkout, and that is a different
    question, not an answer to this one.
    """
    path = home / "health" / "runtime-release.json"
    if not path.is_file():
        raise ValueError("no served-release record at " + str(path))
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("unreadable served-release record: " + str(exc)) from exc
    if not isinstance(record, dict):
        raise ValueError("the served-release record is not an object")
    source = record.get("source")
    sha = record.get("sha")
    if not isinstance(source, str) or not source:
        raise ValueError("the served-release record names no source")
    if not isinstance(sha, str) or len(sha) != 40:
        raise ValueError("the served-release record names no sha")
    root = Path(source) / "src"
    if not (root / "mishe_tauftauf" / "__init__.py").is_file():
        raise ValueError("the served release carries no plant package")
    return root, sha


def _observe_against_root(tree: Path, root: Path, live_home: Path | None = None) -> dict:
    """Run the real command under one import root and report the outcomes.

    The site is planted against the *committed* tree, so the pages under test
    are the committed pages.  Only the bytes that answer the command change:
    the served release's import root, or the committed tree's own.
    """
    work = Path(tempfile.mkdtemp(prefix="shipped-book-vs-pin-run-"))
    try:
        home = _plant_site(tree, work, live_home)
        outside = work / "checkout"
        outside.mkdir(exist_ok=True)
        readings = {}
        for where, cwd in (("outside", outside), ("inside", home)):
            readings[where] = _run_outcome(root, home, cwd, "VS-PIN-%s.md" % where.upper())
        return readings
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_outcome(root: Path, home: Path, cwd: Path, artifact: str) -> str:
    """Run one real ``wall outcome`` under ``root`` and report what happened.

    Returns "accepted", "refused" or "unknown".  "unknown" means the command
    failed for a reason this check cannot interpret -- the reading is then
    UNKNOWN, never a pass.
    """
    env = dict(os.environ)
    # The child rebuilds its own import path: the root under test plus the
    # standard library.  An ambient PYTHONPATH would otherwise answer for the
    # root under test, and the check would read agreement where there is none.
    env["PYTHONPATH"] = str(root)
    env["MISHE_SEED_HOME"] = str(home)
    notes = home / "walls" / "docs-NOTES.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("reader notes\n", encoding="utf-8")
    (home / "artifacts" / artifact).write_text("evidence body\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "mishe_tauftauf", "--home", str(home),
         "wall", "outcome", "--owner", "docs", "--kind", "accepted",
         "--file", "NOTES.md", "--evidence", "artifacts/" + artifact],
        env=env, capture_output=True, text=True, cwd=str(cwd))
    if result.returncode == 0:
        return "accepted"
    message = (result.stdout + result.stderr).lower()
    if "must be an eligible single file" in message:
        return "refused"
    # "outcome evidence path already bound" is a **different** refusal: the
    # single-use evidence contract, not the rule about where a relative path
    # resolves.  Treating it as the rule's outcome would make the check read
    # a re-bound artifact as a rule difference.
    if "already bound" in message:
        return "unknown"
    return "unknown"


def audit(tree: Path, served_root: Path, live_home: Path | None = None) -> dict:
    """Find pages that state an outcome the served release does not produce."""
    if _package_dir(tree) is None:
        raise ValueError("no plant package in tree")
    served = _observe_against_root(tree, served_root, live_home)
    committed = _observe_against_root(tree, tree / "src", live_home)
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
            observed = served["outside"]
            if statement["outcome"] == observed:
                continue
            findings.append({
                "file": source,
                "stated": statement["outcome"],
                "served": observed,
                "committed": committed["outside"],
                "claim": statement["claim"],
            })
    return {"book_pages": len(pages), "statements": checked,
            "served": served, "committed": committed, "findings": findings}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True,
                        help="site home holding the served-release record")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository; the committed bytes of HEAD are the pages under test")
    parser.add_argument("--tree", type=Path, default=None,
                        help="an explicit tree to test instead of the committed bytes")
    args = parser.parse_args(argv)

    tmp: str | None = None
    try:
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

        served_root, sha = served_release_src(args.home.resolve())
    except ValueError as exc:
        print("UNKNOWN — the served release cannot be read: " + str(exc))
        return 3

    try:
        result = audit(tree, served_root, args.home.resolve())
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print("UNKNOWN — shipped-book-vs-served-release unavailable: " + str(exc))
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"served release {sha}")
    print(f"tree={tree}")
    print(f"book pages={result['book_pages']} statements={result['statements']}")
    print("committed tree: outside=%s inside=%s"
          % (result["committed"]["outside"], result["committed"]["inside"]))
    print("served release: outside=%s inside=%s"
          % (result["served"]["outside"], result["served"]["inside"]))
    for item in result["findings"]:
        print("  BOOK vs SERVED RELEASE  %s states the relative form is %s, "
              "but the served release %s it (the committed tree %s it)"
              % (item["file"], item["stated"], item["served"], item["committed"]))
        print("    " + item["claim"])
    if "unknown" in result["served"].values() or "unknown" in result["committed"].values():
        print("UNKNOWN — the rule could not be exercised, so it is not observed")
        return 3
    if result["statements"] == 0:
        print("UNKNOWN — the shipped book states no evidence-resolution rule, so "
              "there is nothing to compare and a pass would be vacuous")
        return 3
    if not result["findings"]:
        print("OK every stated evidence-resolution outcome matches the served release")
        return 0
    print("FAIL the shipped book states an evidence rule the served release does "
          "not apply")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
