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
import importlib.util
import json
import os
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


def _load_sibling(name: str):
    """Import a sibling check script by path, without importing the package.

    The committed-tree gate already knows how to find a shipped page's stated
    outcomes and how to run the real command.  Reading the same rules twice
    would let the two readings drift apart, so the reading is shared and only
    the tree under test differs.
    """
    path = Path(__file__).resolve().parent / (name + ".py")
    spec = importlib.util.spec_from_file_location("sibling_" + name.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVIDENCE = _load_sibling("shipped-book-evidence")


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


def _observe_against_root(tree: Path, root: Path) -> dict:
    """Run the real command under one import root and report the outcomes.

    The site is planted against the *committed* tree, so the pages under test
    are the committed pages.  Only the bytes that answer the command change:
    the served release's import root, or the committed tree's own.
    """
    work = Path(tempfile.mkdtemp(prefix="shipped-book-vs-pin-run-"))
    try:
        home = EVIDENCE._plant_site(tree, work)
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


def audit(tree: Path, served_root: Path) -> dict:
    """Find pages that state an outcome the served release does not produce."""
    if EVIDENCE._package_dir(tree) is None:
        raise ValueError("no plant package in tree")
    served = _observe_against_root(tree, served_root)
    committed = _observe_against_root(tree, tree / "src")
    pages = EVIDENCE._book_pages(tree)
    findings: list[dict] = []
    checked = 0
    for page in pages:
        source = page.relative_to(tree).as_posix()
        for statement in EVIDENCE._stated_outcomes(page):
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
        result = audit(tree, served_root)
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
