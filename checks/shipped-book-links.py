#!/usr/bin/env python3
"""Detector: every relative link in the shipped book must resolve in the shipped tree.

The public reader receives exactly the *committed* tree.  A link in a tracked
page that points at a path the tree does not contain is a broken link for
every reader who clones the repository, and the failure is silent: nothing in
CI resolves links, and the local checkout does contain the missing files, so a
check that reads the working tree sees a healthy book.

Measured on this plant on 2026-10-31 (Docs wake 75277): a fresh clone of
``d951c928`` had **20 broken relative links** across three tracked files --
``README.md``, ``docs/getting-started.md`` and ``docs/completions-judge.md``.
Commit ``237db71f`` ("Remove local operating records from the public
repository", 2026-10-30) added the public guide ``docs/getting-started.md``
and, in the same commit, added the ``.gitignore`` rules that excluded
``docs/operating.md``, ``docs/how-it-works.md``, ``docs/wall-coordination.md``,
``docs/agent-step-experiments.md``, ``docs/decision-models.md`` and
``AGENTS.md``.  The new guide was written against the plant's *local* book,
which still holds all of them.

Why the existing reader checks did not catch it:

* ``tools/check_docs_command_names.py`` and
  ``tools/check_leaf_invocation_coverage.py`` walk ``README.md`` plus
  ``docs/*.md`` **from disk**.  On this plant that is 16 files: the 9 tracked
  ones plus the 7 gitignored local ones the exclusion left behind.  The
  gitignored files satisfy the corpus, so both checks pass.
* ``research/docs-wake65105-book-integrity-audit.py`` (local, ignored) checks
  relative links and anchors against the working tree for the same reason.

So the question "does this book work for a reader who clones it?" had no
deterministic answer.  This check is that answer.

The check reads the **committed** bytes through ``git archive``, exactly like
``checks/promotion-import-closure.py``, so an untracked working-tree file can
never satisfy a link.  The point is what a clone contains, not what this
checkout happens to hold.

Scope and limits:

* Only *relative* Markdown links are checked.  Absolute URLs, ``mailto:``
  targets and bare in-page anchors (``#heading``) are out of scope; a bare
  anchor is checked only when it is the whole target of a link.
* A relative link that points at a file inside the tree must also have a
  resolvable ``#anchor`` when one is given.
* A relative link that leaves the shipped tree is broken even when the target
  happens to exist next to the checkout on this host: a clone does not
  receive it.
* Reference-style links are checked too: a definition whose target does not
  resolve is a link the reader cannot follow.  A bracketed *use* with no
  definition is not a broken link — CommonMark renders it as literal text,
  and prose such as a Python annotation looks the same way.
* This is a shipped-tree property check, not a link-rot or prose check.  It
  does not judge whether a link is the *best* destination, and it does not
  test external URLs.

Exit status:
  0  every relative link in the shipped tree resolves, with resolvable anchors
  2  at least one relative link does not resolve in the shipped tree
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

LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
REFERENCE = re.compile(r"^\[([^\]]+)\]:\s*(\S+)\s*$", re.MULTILINE)
FENCE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]+`")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-")
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


def slugify(heading: str) -> str:
    """The GitHub-style anchor slug for a Markdown heading."""
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s+", "-", text)


def _markdown_files(tree: Path) -> list[Path]:
    return sorted(p for p in tree.rglob("*.md") if ".git" not in p.parts)


def _anchors(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return set()
    return {slugify(h) for h in HEADING.findall(FENCE.sub("", text))}


def audit(tree: Path) -> dict:
    """Find relative links in the tree that do not resolve inside it."""
    files = _markdown_files(tree)
    anchor_map = {path: _anchors(path) for path in files}
    root = tree.resolve()
    broken: list[dict] = []
    unresolved_anchor: list[dict] = []
    checked = 0

    def check(source: str, target: str, label: str, origin: Path) -> None:
        """Record one relative link against the shipped tree."""
        nonlocal checked
        if target.startswith(("http://", "https://", "mailto:")):
            return
        if target.startswith("#"):
            checked += 1
            if target[1:] not in anchor_map.get(origin, set()):
                unresolved_anchor.append({"file": source, "target": target})
            return
        base, _, anchor = target.partition("#")
        if not base:
            return
        checked += 1
        resolved = (origin.parent / base).resolve()
        # A link that leaves the shipped tree points at something the reader
        # does not receive.  It must not pass because the file happens to
        # exist next to the checkout on this host.
        if not resolved.is_relative_to(root):
            broken.append({"file": source, "target": target, "label": label,
                           "reason": "leaves the shipped tree"})
            return
        if not resolved.exists():
            broken.append({"file": source, "target": target, "label": label})
            return
        if anchor and resolved.is_file():
            if anchor not in anchor_map.get(resolved, set()):
                unresolved_anchor.append({"file": source, "target": target})

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        source = path.relative_to(tree).as_posix()
        prose = INLINE_CODE.sub("", FENCE.sub("", text))
        for label, target in LINK.findall(prose):
            check(source, target, label, path)
        # Reference-style links carry their target in a definition line.  A
        # definition whose target does not resolve is a link the reader
        # cannot follow.  A bracketed use with no definition is *not* a
        # broken link: CommonMark renders it as literal text, and prose such
        # as a Python annotation ``Final[str]`` looks the same way.
        definitions = {label.lower(): target
                       for label, target in REFERENCE.findall(prose)}
        for label, target in definitions.items():
            check(source, target, label, path)
    return {
        "markdown_files": len(files),
        "links_checked": checked,
        "broken": broken,
        "unresolved_anchors": unresolved_anchor,
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
        result = audit(tree)
        print(f"tree={tree}")
        print(f"markdown files={result['markdown_files']} "
              f"relative links checked={result['links_checked']}")
        for item in result["broken"]:
            reason = item.get("reason")
            suffix = f" ({reason})" if reason else ""
            print(f"  BROKEN LINK  {item['file']} -> {item['target']}{suffix}")
        for item in result["unresolved_anchors"]:
            print(f"  BROKEN ANCHOR  {item['file']} -> {item['target']}")
        failures = len(result["broken"]) + len(result["unresolved_anchors"])
        if not failures:
            print("OK every relative link in the shipped tree resolves")
            return 0
        print("FAIL the shipped tree contains relative links a reader cannot follow; "
              "every clone of this commit starts with a broken book")
        return 2
    except ValueError as exc:
        print(f"UNKNOWN — shipped-book link check unavailable: {exc}")
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
