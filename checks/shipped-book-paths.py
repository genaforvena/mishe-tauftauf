#!/usr/bin/env python3
"""Detector: the shipped book must not send a reader to a file the clone lacks.

``checks/shipped-book-links.py`` already refuses a Markdown *link* that leaves
the shipped tree.  A link is only one way a book names a file.  A procedure can
also say "read ``docs/operating.md``" in prose, and the reader who cloned the
repository has no such file.  Nothing in CI caught that, because the local
checkout still holds the excluded documents: the working tree is not what a
reader receives.

Measured on this plant on 2026-10-31 (Docs wake 138278): the shipped
``.agents/skills/plant-mishe/SKILL.md`` -- the file the README's copyable
request tells a reader's agent to use -- instructed the agent to read the
checkout's ``AGENTS.md`` and to "use ``docs/operating.md`` for site discovery
and inspection".  Neither file is in the shipped tree: commit ``237db71f``
("Remove local operating records from the public repository", 2026-10-30) added
the ``.gitignore`` rules that exclude them, and the skill was not updated.  An
agent following the shipped skill verbatim reads two files that do not exist.

Why the path must be *excluded*, not merely absent:

* A path the repository never tracked is usually a **site-relative** runtime
  artifact -- ``engine/accounts.json``, ``SITE/health/services.json`` -- and
  naming it is correct.  Reporting those would make the check cry wolf on
  every page.
* A path the repository's own ignore rules **exclude** is a different thing.
  It is a repository path that was deliberately dropped from the public tree,
  so a reader who cloned it cannot open it.  The ignore rules are the
  repository's own statement about which paths a clone receives, and they are
  the exact source of this failure: ``237db71f`` added the exclusion rules and
  the book kept naming the excluded files.

The finding therefore needs three conditions, all of them checkable:

1. the span is in the **reader's book** -- ``README.md``, ``docs/*.md``, or a
   shipped ``SKILL.md``;
2. the surrounding sentence **directs the reader to it** ("read", "use",
   "see", "consult", "follow", "open", "load") -- a bare mention of a path is
   not a broken instruction;
3. the path is **absent from the shipped tree** and **excluded by the
   repository's ignore rules**.

The check reads the **committed** bytes through ``git archive``, exactly like
``checks/shipped-book-links.py``, so an untracked working-tree file can never
satisfy a reference.  The ignore rules are read with ``git check-ignore``, the
repository's own matcher, rather than a re-implementation of it.

Scope and limits:

* Only *backticked* path-shaped spans are examined.  A path written in plain
  prose, in a link's label, or with a placeholder segment is out of scope.
* The rule is about the **shipped book**.  Historical records under
  ``research/`` are evidence of past reviews, not procedures a reader
  follows, so they are not scanned.
* This is a shipped-tree property check, not a prose check.  It does not judge
  whether a reference is the *best* destination, and it cannot see a missing
  file that no ignore rule covers.

Exit status:
  0  the shipped book sends the reader only to files the clone contains
  2  the shipped book directs the reader to a file the clone does not contain
  3  the question cannot be decided: no tree, no git, or the tree cannot be read
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT = "/usr/bin/git"

# A path-shaped span: a relative path ending in a known file extension.
PATH = re.compile(r"^([A-Za-z0-9_.\-/]+\.(?:md|py|json|yml|yaml|txt|ts|toml|sh|sqlite3|jsonl))$")
# The verbs that turn a mention into a directive.
DIRECTIVE = re.compile(r"\b(read|open|consult|use|see|follow|load)\b", re.IGNORECASE)
FENCE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
SENTENCE = re.compile(r"(?<=[.;!?])\s+")

# The reader's book: the pages a reader receives and follows.
BOOK_ROOTS = ("docs/", ".agents/skills/", "skills/")
BOOK_FILES = ("README.md",)


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-paths-")
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


def _book_pages(tree: Path) -> list[Path]:
    """The pages of the shipped book, in a stable order."""
    pages = []
    for path in sorted(tree.rglob("*.md")):
        name = path.relative_to(tree).as_posix()
        if name in BOOK_FILES or name.startswith(BOOK_ROOTS):
            pages.append(path)
    return pages


def _excluded(repo: Path, candidates: list[str]) -> set[str]:
    """Which candidate paths the repository's own ignore rules exclude."""
    if not candidates:
        return set()
    result = subprocess.run(
        [GIT, "-C", str(repo), "check-ignore", "--no-index", "--stdin"],
        input="\n".join(candidates).encode("utf-8"), capture_output=True)
    if result.returncode not in (0, 1):
        raise ValueError("git check-ignore failed: "
                         + result.stderr.decode("utf-8", "replace").strip())
    return {line for line in result.stdout.decode("utf-8", "replace").splitlines()
            if line.strip()}


def audit(tree: Path, repo: Path) -> dict:
    """Find directives in the shipped book that name a file the clone lacks."""
    findings: list[dict] = []
    candidates: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in _book_pages(tree):
        source = path.relative_to(tree).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        # Fenced blocks carry commands, not prose directives.  Inline code is
        # kept: that is where a path reference is written.
        prose = FENCE.sub("", text)
        for sentence in SENTENCE.split(prose):
            if not DIRECTIVE.search(sentence):
                continue
            for span in INLINE_CODE.findall(sentence):
                match = PATH.match(span.strip())
                if not match:
                    continue
                target = match.group(1)
                if target.startswith("/") or ".." in target or " " in target:
                    continue
                if (path.parent / target).exists() or (tree / target).exists():
                    continue
                if (source, target) in seen:
                    continue
                seen.add((source, target))
                candidates.append(target)
                findings.append({"file": source, "target": target,
                                 "sentence": " ".join(sentence.split())})
    excluded = _excluded(repo, candidates)
    broken = [item for item in findings if item["target"] in excluded]
    return {
        "book_pages": len(_book_pages(tree)),
        "references": len(findings),
        "excluded_candidates": len(excluded),
        "broken": broken,
    }


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
        result = audit(tree, args.repo.resolve() if args.repo else tree)
        print(f"tree={tree}")
        print(f"book pages={result['book_pages']} "
              f"path references={result['references']} "
              f"excluded candidates={result['excluded_candidates']}")
        for item in result["broken"]:
            print(f"  MISSING FILE  {item['file']} -> {item['target']} "
                  f"(excluded by the repository's ignore rules)")
            print(f"    {item['sentence']}")
        if not result["broken"]:
            print("OK the shipped book sends the reader only to files the clone contains")
            return 0
        print("FAIL the shipped book directs the reader to a file the clone does not "
              "contain; every clone of this commit starts with a broken procedure")
        return 2
    except ValueError as exc:
        print(f"UNKNOWN — shipped-book path check unavailable: {exc}")
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
