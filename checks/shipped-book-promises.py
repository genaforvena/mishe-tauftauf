#!/usr/bin/env python3
"""Detector: a shipped page's promise must be kept by the page it points to.

``checks/shipped-book-links.py`` refuses a Markdown *link* that leaves the
shipped tree, and ``checks/shipped-book-paths.py`` refuses a directive that
names a file the clone lacks.  Both ask whether the reader can *reach* what
the page points at.  Neither asks whether the pointed-at page *contains* it.

A page that says "see X for the commands that do Y" makes a claim about X's
contents.  A link that resolves is not that claim: the reader arrives and
finds nothing about Y.  The failure is silent, because every existing check
reads a link as a link.

Measured on this plant on 2026-11-01 (Docs wake 149342): ``docs/mesh.md``
closed with "See ... [Getting started](getting-started.md) for the commands
that write a wall, record an outcome and settle a wake."  The link resolved --
``docs/getting-started.md`` is tracked -- but the page carried one of the
three: ``wall outcome``.  The commands that write a wall (``wall write``) and
that settle a wake (``seed yield``) appeared nowhere in it.  The pointer was
introduced by commit ``31ee26a1`` as the repair of an earlier broken link, so
the class is not hypothetical: resolving a destination is not the same as
checking what it says.

The rule this check enforces:

* a **shipped book page** is a sentence that contains a Markdown link to
  another **shipped page** and **names a command** in that sentence;
* every command so named must also be named by the linked page.

Naming a command means the destination carries an invocation of it -- a
backticked span or a fenced command line -- not merely the words.  A prose
mention of "the wall write path" does not name the command.

Scope and limits:

* Only *relative* Markdown links between shipped pages are read.  Absolute
  URLs and anchors into other sites are out of scope.
* The rule is about **named commands**.  A promise that names a capability in
  prose only -- "for the commands that write a wall" -- carries no command to
  check, and this detector cannot see it.  The way out is the way in: a
  promise that names commands is checkable, so promises in this book name
  their commands.  A prose-only promise is a known limit, not a cleared
  question.
* A command named in a sentence that carries a link is required of that
  link's destination.  This is deliberately strict: if the sentence points at
  a page *for* the command, that page must carry it.
* The command vocabulary is the **shipped CLI's own** leaves, read from the
  committed tree, so a page cannot name a command the clone does not have.

Exit status:
  0  every named command is kept by the page the sentence points at
  2  a shipped page points at another shipped page for a command it lacks
  3  the question cannot be decided: no tree, no git, or the tree cannot be read
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT = "/usr/bin/git"

LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
FENCE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
SENTENCE = re.compile(r"(?<=[.;!?])\s+")
BLOCK = re.compile(r"\n\s*\n")
"""A blank-line-separated block of prose.

Blocks are the unit a promise is read in: a wrapped sentence stays whole, and
a table -- whose rows are separated by newlines but not by blank lines --
stays a single block whose rows are told apart by their own links.
"""

BOOK_ROOTS = ("docs/", ".agents/skills/", "skills/")
BOOK_FILES = ("README.md",)

# A promise phrased in prose: "the commands that write a wall", "the commands
# for a landing".  The named-command rule cannot see these, and they are the
# common case -- a page points at another page *for* something, and says what
# that something is in words rather than in a backticked invocation.
PROMISE = re.compile(
    r"\b(?:the\s+)?(?:commands?|ways?|steps?|procedures?|routes?)\s+"
    r"(?:that|for|to|which)\b",
    re.IGNORECASE,
)

# The capabilities a prose promise can name.  Each maps a prose phrase to the
# CLI leaf that carries it, so the destination page must name that leaf.  A
# phrase with no entry here is not a promise this detector can check.
CAPABILITY = {
    "write a wall": "wall write",
    "writes a wall": "wall write",
    "write the wall": "wall write",
    "writes the wall": "wall write",
    "writing a wall": "wall write",
    "record an outcome": "wall outcome",
    "records an outcome": "wall outcome",
    "record a contribution": "wall outcome",
    "records a contribution": "wall outcome",
    "recording an outcome": "wall outcome",
    "settle a wake": "yield",
    "settles a wake": "yield",
    "settle the wake": "yield",
    "settles the wake": "yield",
    "settle a turn": "yield",
    "settles a turn": "yield",
    "settling a wake": "yield",
    "write a message": "wall dm",
    "writes a message": "wall dm",
    "read a wall": "wall show",
    "reads a wall": "wall show",
}

WRAPPERS = {"python", "python3", "rtk", ".venv/bin/python"}
CLI_NAME_MARKS = ("mishe", "mishe-tauftauf", "mishe_tauftauf")
"""Command name endings that mark a site-CLI invocation.

The short ``mishe`` entry point is the standard name for ordinary commands;
the historical long name still reaches the same CLI, and ``mishe_tauftauf`` is
the module name used by ``python -m`` forms.
"""


def _load_leaf_checker(repo: Path):
    """The leaf enumerator, loaded from the repository that owns this check.

    The enumerator is a tool of the repository running the check, not part of
    the shipped book: a synthetic tree under test has no ``tools/``.  What
    comes from the tree under test is the CLI itself -- ``--src`` -- and its
    leaves are the commands a reader of *that* tree could run.
    """
    path = repo / "tools" / "check_leaf_invocation_coverage.py"
    if not path.is_file():
        raise ImportError(f"cannot load leaf checker: {path}")
    spec = importlib.util.spec_from_file_location("leaf_checker", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load leaf checker: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-promises-")
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


def _spans(text: str) -> list[str]:
    """Every command-shaped candidate: backticked spans and fenced lines."""
    found = list(INLINE_CODE.findall(text))
    for fence in re.findall(r"```[^\n]*\n(.*?)```", text, re.S):
        for line in fence.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                found.append(line)
    return found


def _tokens(span: str) -> list[str]:
    """A span's tokens, unquoted, without shell continuations."""
    tokens = []
    for raw in span.split():
        token = raw.strip("\"'")
        if token.endswith("\\"):
            token = token[:-1]
        if token:
            tokens.append(token)
    return tokens


def named_commands(span: str, leaves: list[str]) -> set[str]:
    """The CLI commands a span names, if it names any.

    A span names a command when the command's own tokens appear adjacent in
    it, and the span either starts with the command or carries a command-name
    marker -- the CLI's own name or a wrapper -- so ordinary prose that
    happens to contain the words is not a naming.
    """
    tokens = _tokens(span)
    if not tokens:
        return set()
    marked = any(token in WRAPPERS or token.endswith(CLI_NAME_MARKS)
                 for token in tokens)
    found: set[str] = set()
    for leaf in leaves:
        parts = leaf.split()
        width = len(parts)
        for index in range(len(tokens) - width + 1):
            if tokens[index:index + width] != parts:
                continue
            if index == 0 or marked:
                found.add(leaf)
                break
    return found


def _resolve(source: Path, target: str, tree: Path) -> Path | None:
    """The shipped page a relative link points at, or None."""
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return None
    path, _, _anchor = target.partition("#")
    if not path:
        return None
    if path.startswith("/"):
        return None
    candidate = (source.parent / path).resolve()
    root = tree.resolve()
    if not candidate.is_relative_to(root):
        return None
    if not candidate.is_file() or candidate.suffix != ".md":
        return None
    return candidate


def audit(tree: Path, leaves: list[str]) -> dict:
    """Find promises a linked page does not keep."""
    pages = _book_pages(tree)
    text_of = {}
    for page in pages:
        try:
            text_of[page] = page.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read {page}: {exc}") from exc

    def names_of(page: Path) -> set[str]:
        found: set[str] = set()
        for span in _spans(text_of[page]):
            found |= named_commands(span, leaves)
        return found

    findings: list[dict] = []
    checked = 0
    for page in pages:
        source = page.relative_to(tree).as_posix()
        # Fenced blocks carry commands, not promises: a link and a command in
        # one fenced example is an example, not a claim about the link's
        # destination.
        prose = FENCE.sub("", text_of[page])
        # Segments are blank-line-separated blocks, not sentences.  A sentence
        # split would cut a wrapped promise from its link, and a line split
        # would cut a table row from the phrase beside it.  A block keeps both:
        # "See the [README](../README.md) ... and [Getting\nstarted](...) for
        # the commands that write a wall" stays whole, and each table row
        # stays its own row because a table has no blank lines inside it.
        for segment in BLOCK.split(prose):
            segment = segment.strip()
            if not segment:
                continue
            links = [(match.start(), match.end(), match.group(2))
                     for match in LINK.finditer(segment)]
            if not links:
                continue
            # A *named command* belongs to the link it follows: "read the
            # running plant with `mishe view`, or read the [Mesh](mesh.md)
            # note" promises nothing of the Mesh page, because the command
            # names the other clause.
            #
            # A *capability phrase* binds to the nearest link in either
            # direction: "the commands that write a wall" qualifies the link
            # beside it, whether the phrase sits before the link or after it.
            for index, (start, end, target) in enumerate(links):
                resolved = _resolve(page, target, tree)
                if resolved is None:
                    continue
                stop = (links[index + 1][0] if index + 1 < len(links)
                        else len(segment))
                promised: set[str] = set()
                for span in INLINE_CODE.findall(segment[end:stop]):
                    promised |= named_commands(span, leaves)
                # The text before this link, back to the previous link or the
                # start of its own row, is the other half of the phrase: "| Write
                # a wall | [Getting started](...) |" puts the capability
                # before its link.  A row boundary stops the search, so one
                # table row's phrase cannot reach the next row's link.
                before = (links[index - 1][1] if index > 0 else 0)
                row_start = segment.rfind("\n", before, start) + 1
                before = max(before, row_start)
                for phrase, command in CAPABILITY.items():
                    pattern = r"\b" + re.escape(phrase) + r"\b"
                    if re.search(pattern, segment[end:stop], re.IGNORECASE):
                        promised.add(command)
                    elif re.search(pattern, segment[before:start], re.IGNORECASE):
                        promised.add(command)
                if not promised:
                    continue
                checked += 1
                kept = names_of(resolved)
                for command in sorted(promised - kept):
                    findings.append({
                        "file": source,
                        "target": resolved.relative_to(tree).as_posix(),
                        "command": command,
                        "sentence": " ".join(segment.split()),
                    })
    return {"book_pages": len(pages), "promise_sentences": checked,
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
    parser.add_argument("--src", type=Path, default=None,
                        help="where to read the CLI leaves from (default: the tree's src)")
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
        # The enumerator is a tool of the repository that owns this check;
        # the leaves come from the tree under test.
        checker = _load_leaf_checker(Path(__file__).resolve().parents[1])
        src = args.src.resolve() if args.src else tree / "src"
        if not (src / "mishe_tauftauf" / "cli.py").is_file():
            print(f"no CLI at {src}")
            return 3
        raw = checker.leaves(checker.load_parser(src))
        leaves = sorted({leaf.replace("permit ", "access ", 1) for leaf in raw})
        result = audit(tree, leaves)
        print(f"tree={tree}")
        print(f"book pages={result['book_pages']} "
              f"promise sentences={result['promise_sentences']} "
              f"cli leaves={len(leaves)}")
        for item in result["findings"]:
            print(f"  UNKEPT PROMISE  {item['file']} names `{item['command']}` "
                  f"and points at {item['target']}, which does not name it")
            print(f"    {item['sentence']}")
        if not result["findings"]:
            print("OK every command a shipped page promises is named by the page it points at")
            return 0
        print("FAIL a shipped page points at another shipped page for a command it "
              "does not name; the reader arrives and finds nothing")
        return 2
    except (ImportError, OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print(f"UNKNOWN — shipped-book promise check unavailable: {exc}")
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
