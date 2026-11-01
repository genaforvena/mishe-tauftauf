#!/usr/bin/env python3
"""Detector: a shipped page must not present unreachable code as a running runtime.

``checks/shipped-book-links.py`` asks whether the reader can *reach* what a
page points at.  ``checks/shipped-book-paths.py`` asks whether the path a page
names exists in the clone.  ``checks/shipped-book-promises.py`` asks whether the
page a promise points at *contains* what the promise names.

None of them asks whether the thing the page describes **runs**.  A page can
name a real file, in a real module, with a real function -- and the function
can be unreachable: no importer, no CLI leaf, no unit.  The page then reads as
a description of the running plant, and the reader is told about a runtime that
does not exist.

This is not hypothetical, and the way it fails is worse than a broken link: a
broken link is visibly broken, and an unwired runtime reads as a working
feature.  Measured on this plant on 2026-11-01 (Docs wake 150671, from Pub's
report in chat 150242):

* ``docs/model-changes.md`` carried a section headed "Engine watch
  (supervised)" that described a 60-second collector writing
  ``engine/watch-cache.json``, a stale guard, a Health outbox, a revert path
  and a 120-second detection interval -- all in the present tense, all as a
  running surface;
* the collector is ``src/mishe_tauftauf/engine_watch.py``.  No module in
  ``src/`` imports it, it registers no CLI leaf, no systemd unit runs it, and
  the site home holds no ``engine/watch-cache.json``;
* Health's disposition (chat 150177) is that the mechanism is dormant by
  design during LIMITED host capacity, deferred to a named trigger.

So the page described a runtime that did not exist, and the reason it was
dormant was a *decision* recorded in chat -- invisible to every reader of the
book, and invisible to every check.

The rule this check enforces:

* a **shipped book page** is a page under ``docs/``, ``skills/`` or
  ``.agents/skills/``, plus ``README.md``;
* a **runtime claim** is a backticked path under a plant-artifact prefix
  (``engine/``, ``health/``, ``walls/``, ``checks/``, ``records/``, ``logs/``,
  ``artifacts/``, ``coordination/``, ``discover/``) whose only writer in the
  committed tree is a module that no entry point can reach;
* such a claim must be **marked as dormant** -- the page must say so near the
  claim.

The reachability rule is the load-bearing part, so it is defined narrowly and
read from the committed tree, not from the live plant:

* the **entry points** are the package CLI (``cli.py``, ``__main__.py``) and
  every module the committed tree names as a subprocess target
  (``-m mishe_tauftauf.<module>``) or as a spawned module path;
* a module is **reachable** if it is an entry point, or if it is imported
  (statically, at any level) by a reachable module.

A module that is only ever named in a *string literal* -- a subprocess
argument assembled at runtime -- is treated as an entry point, because that is
how the plant actually starts things.

Scope and limits, named:

* The check reads the **committed** bytes through ``git archive``, so an
  untracked working-tree file cannot satisfy a claim.
* Reachability is **static import reachability** from the committed tree.  A
  module loaded through ``importlib`` from a path the tree does not contain, or
  reached only by a unit that is not committed to the tree, is reported as
  unreachable.  That is a false positive risk, and it is deliberate: the page
  is about a *runtime*, and a runtime the committed tree cannot reach is
  exactly what a reader must not be told exists.
* A module with a ``main()`` is **runnable by hand** but is still not
  *scheduled*.  Reachability does not distinguish "a person typed it" from "a
  unit runs it", so this check cannot tell a dormant-but-manual mechanism from
  a live one.  It can only tell "nothing in the tree starts this" from
  "something in the tree starts this".  A page may therefore describe a
  manual mechanism with a dormant marker; it may not present it as running.
* Only **backticked** paths are read.  A path named in prose without
  backticks is not a claim this check can see.
* The writer of a path is found by literal ``Path("...")`` constants and
  f-string path templates in the committed sources.  A path written by string
  concatenation in a way this cannot see has no writer, and is not a finding.
* A path with **no writer at all** in the committed tree is not a finding:
  the page may describe a file the operator creates by hand.

Exit status:
  0  every runtime claim in the shipped book names a reachable runtime
  2  a shipped page presents an unreachable runtime as running
  3  the question cannot be decided: no tree, no git, or the tree cannot be read
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT = "/usr/bin/git"

BOOK_ROOTS = ("docs/", ".agents/skills/", "skills/")
BOOK_FILES = ("README.md",)

# The plant-artifact prefixes a reader reads as "a file the plant writes".
ARTIFACT_PREFIXES = (
    "engine/", "health/", "walls/", "checks/", "records/", "logs/",
    "artifacts/", "coordination/", "discover/",
)

# The words that mark a claim as dormant.  A claim may sit in a section whose
# heading carries the marker, or in a paragraph near it; both are read here.
DORMANT_MARKERS = (
    "not wired", "does not run", "not running", "dormant", "designed, not running",
    "no longer runs", "is not scheduled", "not scheduled", "not wired and does not run",
    "absent from the runtime", "inert", "never started",
)
"""Words that mark a runtime claim as dormant.

The marker must be *near* the claim: in the same block, or in the block that
introduces it.  A page that says "dormant" once at the top of a long chapter
does not thereby excuse every later claim.
"""

BLOCK = re.compile(r"\n\s*\n")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
HEADING = re.compile(r"^#{1,6}\s")

# A subprocess target or spawned module path in the committed tree.
MODULE_TARGET = re.compile(r"mishe_tauftauf\.([a-z_][a-z0-9_]*)")
"""A module named as a dotted path in the committed tree.

This covers both ``-m mishe_tauftauf.<module>`` and a module path assembled
into a subprocess argument.  A module that only ever appears this way is
started by the plant, so it is an entry point.
"""

FENCE = re.compile(r"```.*?```", re.DOTALL)


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-runtimes-")
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


def _imports_of(path: Path) -> set[str]:
    """The sibling modules a source file imports statically."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("mishe_tauftauf."):
                    found.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # A relative import inside the package names a sibling.
                if node.module:
                    found.add(node.module.split(".")[-1])
                for alias in node.names:
                    found.add(alias.name)
            elif node.module and node.module.startswith("mishe_tauftauf."):
                found.add(node.module.split(".")[-1])
    return found


def entry_points(package: Path, tree: Path) -> set[str]:
    """The modules the committed tree can start.

    Two kinds count:

    1. the package's own command-line entry points (``cli.py``,
       ``__main__.py``);
    2. any module the tree names as a dotted path -- a subprocess target such
       as ``-m mishe_tauftauf.<module>``, or a module path assembled into a
       command.  The plant starts things that way, so a module that only
       appears as a string is still started.

    A **test** is not an entry point.  A test names the module it exercises,
    usually as a dotted path, and reading that as a start would make every
    tested module look scheduled -- which is exactly the confusion this check
    exists to resolve.  The plant does not run tests as services.
    """
    roots: set[str] = set()
    for name in ("cli", "__main__"):
        if (package / f"{name}.py").is_file():
            roots.add(name)
    for path in sorted(tree.rglob("*.py")):
        relative = path.relative_to(tree)
        if relative.parts and relative.parts[0] in ("tests", "test"):
            continue
        if "tests" in relative.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for match in MODULE_TARGET.finditer(text):
            name = match.group(1)
            if (package / f"{name}.py").is_file():
                roots.add(name)
    return roots


def reachable_modules(package: Path, tree: Path) -> set[str]:
    """Every module reachable from the committed tree's entry points."""
    graph = {path.stem: _imports_of(path) for path in package.glob("*.py")}
    seen: set[str] = set()
    stack = list(entry_points(package, tree))
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for dependency in graph.get(name, ()):
            if dependency in graph and dependency not in seen:
                stack.append(dependency)
    return seen


def path_writers(package: Path) -> dict[str, set[str]]:
    """Which modules write which plant-relative paths.

    A writer is a module that names the path as a ``Path`` constant, or that
    builds it from a template.  The literal prefix of a template is the
    directory it writes into.
    """
    writers: dict[str, set[str]] = {}
    for path in sorted(package.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for match in re.finditer(r'Path\(f?"([^"{]*)"', text):
            writers.setdefault(match.group(1), set()).add(path.stem)
        for match in re.finditer(r'=\s*f?"([^"\n]*[/"][^"\n]*)"', text):
            literal = match.group(1)
            if literal.endswith((".json", ".jsonl", ".md", ".txt", ".csv")) or "/" in literal:
                writers.setdefault(literal, set()).add(path.stem)
    return writers


def _book_pages(tree: Path) -> list[Path]:
    """The pages of the shipped book, in a stable order."""
    pages = []
    for path in sorted(tree.rglob("*.md")):
        name = path.relative_to(tree).as_posix()
        if name in BOOK_FILES or name.startswith(BOOK_ROOTS):
            pages.append(path)
    return pages


def _writers_of(artifact: str, writers: dict[str, set[str]]) -> set[str]:
    """The modules that write an artifact path.

    A path matches a writer's literal when the writer's literal is the path, a
    directory prefix of the path, or a template whose literal prefix the path
    starts with.
    """
    found: set[str] = set()
    for literal, modules in writers.items():
        prefix = literal.split("{")[0].rstrip("/")
        if not prefix:
            continue
        if artifact == literal or artifact == prefix:
            found |= modules
        elif artifact.startswith(prefix + "/"):
            found |= modules
    return found


def _marked_dormant(context: str) -> bool:
    """Whether a claim is marked as dormant in the context that governs it."""
    haystack = context.lower()
    return any(marker in haystack for marker in DORMANT_MARKERS)


def _sections(prose: str) -> list[tuple[str, str, list[str]]]:
    """The page's sections: each heading, its own text, and its blocks.

    A marker binds to the **section** it appears in, not to one paragraph.
    A page that heads a section "Engine watch (designed, not running)" and
    then describes that mechanism in three paragraphs is making one claim
    about a dormant thing, not three claims about a running thing.  Reading
    the marker per paragraph would call that page defective for being
    explicit about what it is.
    """
    sections: list[tuple[str, str, list[str]]] = []
    heading = ""
    heading_text: list[str] = []
    blocks: list[str] = []
    for block in BLOCK.split(prose):
        if not block.strip():
            continue
        lines = block.splitlines()
        if lines and HEADING.match(lines[0]):
            if heading or heading_text or blocks:
                sections.append((heading, "\n".join(heading_text), blocks))
            heading = lines[0]
            heading_text = [block]
            blocks = []
            continue
        heading_text.append(block)
        blocks.append(block)
    if heading or heading_text or blocks:
        sections.append((heading, "\n".join(heading_text), blocks))
    return sections


def audit(tree: Path) -> dict:
    """Find runtime claims the committed tree cannot reach."""
    package = _package_dir(tree)
    if package is None:
        raise ValueError("no plant package in tree")
    reachable = reachable_modules(package, tree)
    writers = path_writers(package)

    pages = _book_pages(tree)
    findings: list[dict] = []
    checked = 0
    for page in pages:
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read {page}: {exc}") from exc
        source = page.relative_to(tree).as_posix()
        # Fenced blocks are examples.  A path inside one is an illustration,
        # not a claim that the plant writes it.
        prose = FENCE.sub("", text)
        for heading, heading_text, blocks in _sections(prose):
            for index, block in enumerate(blocks):
                artifacts = []
                for span in INLINE_CODE.findall(block):
                    span = span.strip()
                    if not span.startswith(ARTIFACT_PREFIXES):
                        continue
                    # A path with a placeholder is a template; the literal
                    # part is enough to find its writer.
                    artifacts.append(span)
                if not artifacts:
                    continue
                owners: set[str] = set()
                for artifact in artifacts:
                    owners |= _writers_of(artifact, writers)
                if not owners:
                    # Nothing in the tree writes this path: the operator may
                    # create it by hand.  Not a finding.
                    continue
                unwired = sorted(name for name in owners if name not in reachable)
                if not unwired:
                    continue
                checked += 1
                # The context that governs the claim is its heading and every
                # block of its section up to and including the claim.
                context = heading_text + "\n" + "\n".join(blocks[:index + 1])
                if _marked_dormant(context):
                    continue
                findings.append({
                    "file": source,
                    "artifacts": sorted(set(artifacts)),
                    "writers": sorted(owners),
                    "unwired": unwired,
                    "claim": " ".join(block.split())[:300],
                })
    return {"book_pages": len(pages), "runtime_claims": checked,
            "findings": findings}


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
        print(f"book pages={result['book_pages']} "
              f"runtime claims={result['runtime_claims']}")
        for item in result["findings"]:
            print(f"  UNREACHABLE RUNTIME  {item['file']} names "
                  f"{', '.join('`' + a + '`' for a in item['artifacts'])}, "
                  f"written only by {', '.join(item['unwired'])}, "
                  f"which no entry point reaches")
            print(f"    {item['claim']}")
        if not result["findings"]:
            print("OK every runtime claim in the shipped book names a reachable runtime")
            return 0
        print("FAIL a shipped page presents a runtime as running that the "
              "committed tree cannot start")
        return 2
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print(f"UNKNOWN — shipped-book runtime check unavailable: {exc}")
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
