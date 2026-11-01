#!/usr/bin/env python3
"""Detector: a command the book hands the reader must name a program it has.

``checks/shipped-book-commands.py`` asks whether the plant code a command
runs **resolves**.  It says nothing about the **program word** the command
starts with, so a command whose program is a file the clone does not contain
passes it.

Measured on this plant on 2026-11-01 (Docs wake 166420):

* the shipped ``README.md`` told a reader to run the checks with
  ``PYTHONPATH=src .venv/bin/pytest -q``;
* ``.venv/`` is excluded by the repository's own ignore rules
  (``.gitignore:6``), so the clone the reader receives has no such file, and
  the book never tells the reader to create one;
* measured against the committed bytes in a fresh ``git archive`` clone, the
  reader's own command answers::

      env: '.venv/bin/pytest': No such file or directory

  and exits **127** -- the command fails before it reaches the plant, which
  is the same class the command gate was written for;
* the sibling gates could not see it.  ``checks/shipped-book-paths.py``
  reads a **directive** in prose ("read", "use", "see") and a path-shaped
  span; the README says "run", and the span it names is a command, not a
  file reference.  ``checks/shipped-book-commands.py`` reads the *module* a
  command imports; ``pytest`` is not plant code, so the command was never
  this gate's question.

The rule this check enforces:

* a **command** is a shell command a shipped page hands the reader -- in a
  shell fence, or in an inline span that reads as a command;
* its **program word** -- the first word after any ``env`` prefix and any
  ``NAME=VALUE`` assignments -- may be a **path**;
* when that program word is a relative path, the clone must contain it,
  because the clone is what the reader receives;
* a program word that is an absolute path, or that carries a shell variable
  or a placeholder segment, is **UNKNOWN**: the reader's own value decides,
  and this check cannot know it.

The defect is read from the **committed bytes** through ``git archive``, so
an untracked working-tree file can never make a stale page look current, and
a page gitignored out of the public tree is not a reader's problem.

Scope and limits, named:

* The check reads the **shipped book**: ``README.md``, ``docs/*.md``, and the
  shipped ``SKILL.md`` files.  Historical records under ``research/`` are
  evidence of past reviews, not procedures a reader follows.
* A **program word that is not a path** -- ``git``, ``tmux``, ``pytest``,
  ``codex`` -- is not this check's question.  Whether a reader's host has
  the tool is a dependency question, and the book states its prerequisites
  where it states them.
* A path whose segments carry a **placeholder** (``SITE/bin/mishe``,
  ``$SITE/bin/mishe``, ``NAME.md``) is UNKNOWN: the reader supplies the
  value.  A placeholder that is also excluded from the tree is still
  UNKNOWN, because a reader who creates the directory has the program.
* A **relative path the repository never tracked** and never excluded is
  also UNKNOWN rather than a finding: the ignore rules are the repository's
  own statement about what a clone receives, and a path the repository never
  mentioned at all is usually a runtime artifact the reader creates.
* An **absolute path** (``/proc/loadavg``, ``/tmp/tmux-UID/...``) is out of
  scope: it names a place outside the clone on purpose.
* This check says nothing about whether the program *runs* once found; that
  is ``checks/shipped-book-commands.py``'s question for plant code, and the
  reader's own host's question for everything else.

Exit status:
  0  every command in the shipped book names a program the clone contains
  2  a shipped page hands the reader a command whose program the clone lacks
  3  the question cannot be decided: no tree, no git, or no command to read
"""
from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT = "/usr/bin/git"

BOOK_ROOTS = ("docs/", ".agents/skills/", "skills/")
BOOK_FILES = ("README.md",)

# The languages whose fences hold commands a reader would run.
SHELL_LANGUAGES = {"", "bash", "sh", "shell", "console", "zsh"}

FENCE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")

# A placeholder segment: the reader supplies the value.  ``SITE``,
# ``NAME`` and ``ROLE`` are how the book writes them; a segment that is only
# a shell variable is read the same way.
PLACEHOLDER_SEGMENT = re.compile(r"^[A-Z][A-Z0-9_]*$")
VARIABLE = re.compile(r"[$`]")


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-program-paths-")
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


def _book_pages(tree: Path) -> list[Path]:
    """The pages of the shipped book, in a stable order."""
    pages = []
    for path in sorted(tree.rglob("*.md")):
        name = path.relative_to(tree).as_posix()
        if name in BOOK_FILES or name.startswith(BOOK_ROOTS):
            pages.append(path)
    return pages


def _logical_commands(block: str) -> list[str]:
    """The commands in a shell block, with continuations joined."""
    commands, current = [], ""
    for line in block.splitlines():
        stripped = line.strip()
        if not current:
            if not stripped or stripped.startswith("#"):
                continue
            current = stripped
        else:
            current += " " + stripped
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        commands.append(current)
        current = ""
    if current:
        commands.append(current)
    return commands


def _split_env(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """The environment a command states, and the rest of it."""
    assignments: dict[str, str] = {}
    rest = list(tokens)
    if rest and rest[0] == "env":
        rest = rest[1:]
    while rest and "=" in rest[0] and not rest[0].startswith("-") and "/" not in rest[0].split("=")[0]:
        name, _, value = rest[0].partition("=")
        assignments[name] = value
        rest = rest[1:]
    return assignments, rest


def _stated_commands(page: Path) -> list[tuple[str, str]]:
    """The commands a page hands the reader, as (where, text) pairs.

    A shell fence is a command.  An inline span is a command when it reads
    like one: it carries the words of a command rather than a single path
    reference.  The program it names is read the same way a shell reads
    it -- past an ``env`` prefix and past ``NAME=VALUE`` assignments -- so
    ``PYTHONPATH=src .venv/bin/pytest -q`` names ``.venv/bin/pytest``.
    """
    try:
        text = page.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    found: list[tuple[str, str]] = []
    for match in FENCE.finditer(text):
        language = match.group(1).strip().split()[0] if match.group(1).strip() else ""
        if language.lower() not in SHELL_LANGUAGES:
            continue
        for command in _logical_commands(match.group(2)):
            found.append((f"{page.name} fence", command))
    for match in INLINE_CODE.finditer(text):
        span = match.group(1).strip()
        if len(span.split()) < 2:
            continue
        tokens = _tokens(span)
        if not tokens:
            continue
        program = _program_word(tokens)
        if program is None or "/" not in program:
            continue
        found.append((f"{page.name} inline", span))
    return found


def _tokens(command: str) -> list[str] | None:
    """The shell words of a command, or None when it cannot be read."""
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _program_word(tokens: list[str]) -> str | None:
    """The program a command runs, or None when it names none."""
    if not tokens:
        return None
    if tokens[0] == "sudo":
        return None
    _, rest = _split_env(tokens)
    if not rest:
        return None
    word = rest[0]
    if word.startswith("-"):
        # A flag, not a program: ``-u NAME`` is an option to the previous
        # word of a multi-word span.
        return None
    return word


def _verdict(program: str, tree: Path, excluded: set[str]) -> tuple[str, str]:
    """What the clone says about a program word.

    Returns (verdict, detail) where verdict is "present", "finding" or
    "unknown".
    """
    if program.startswith("/"):
        return "unknown", "an absolute path names a place outside the clone"
    if VARIABLE.search(program):
        return "unknown", "the program comes from a value the reader supplies"
    segments = [segment for segment in program.split("/") if segment]
    if any(PLACEHOLDER_SEGMENT.match(segment) for segment in segments):
        return "unknown", "a placeholder segment carries the reader's own value"
    if (tree / program).exists():
        return "present", "the clone contains it"
    if program in excluded:
        return "finding", "the repository's own ignore rules exclude it"
    return "unknown", "the repository never tracked it, so the reader supplies it"


def _excluded(repo: Path, candidates: list[str]) -> set[str] | None:
    """Which candidate paths the repository's own ignore rules exclude.

    The rules are read with ``git check-ignore``, the repository's own
    matcher, rather than a re-implementation of it.  ``--no-index`` keeps
    the question about the ignore rules alone: a path the repository still
    tracks is not excluded by them.

    ``None`` means the question cannot be decided: the tree is not a work
    tree of any repository, so it carries no ignore rules and a path the
    reader lacks cannot be told apart from a path the reader creates.
    """
    if not candidates:
        return set()
    probe = subprocess.run([GIT, "-C", str(repo), "rev-parse", "--git-dir"],
                           capture_output=True)
    if probe.returncode:
        return None
    argv = [GIT, "-C", str(repo), "check-ignore", "--no-index", "--stdin"]
    result = subprocess.run(argv, input="\n".join(candidates).encode("utf-8"),
                            capture_output=True)
    if result.returncode not in (0, 1):
        return None
    return {line for line in result.stdout.decode("utf-8", "replace").splitlines()
            if line.strip()}


def _audit(tree: Path, repo: Path) -> dict:
    """Find the commands a shipped page cannot run in a clone."""
    findings: list[dict] = []
    unknown: list[dict] = []
    checked = 0
    # The programs the book names, so the ignore rules can be read once.
    programs: dict[tuple[str, str], str] = {}
    candidates: list[str] = []
    for page in _book_pages(tree):
        relative = page.relative_to(tree).as_posix()
        for where, command in _stated_commands(page):
            tokens = _tokens(command)
            if not tokens:
                continue
            program = _program_word(tokens)
            if program is None or "/" not in program:
                continue
            if (relative, program) in programs:
                continue
            programs[(relative, program)] = command
            if program not in candidates:
                candidates.append(program)
    excluded = _excluded(repo, candidates)
    for (relative, program), command in programs.items():
        checked += 1
        if excluded is None:
            # The tree carries no ignore rules, so a path the reader lacks
            # cannot be told apart from a path the reader creates.
            verdict, detail = "unknown", "the tree carries no ignore rules to read"
        else:
            verdict, detail = _verdict(program, tree, excluded)
        if verdict == "present":
            continue
        record = {"file": relative, "command": command, "program": program,
                  "detail": detail}
        if verdict == "finding":
            findings.append(record)
        else:
            unknown.append(record)
    return {"checked": checked, "findings": findings, "unknown": unknown}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, default=None,
                        help="site home (kept for the observer-checks runner contract)")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository; the committed bytes of HEAD are tested")
    parser.add_argument("--tree", type=Path, default=None,
                        help="an explicit tree to test instead of the committed bytes")
    args = parser.parse_args(argv)

    tmp: str | None = None
    try:
        if args.tree:
            tree = args.tree.resolve()
            # The ignore rules come from the repository, so a tree tested on
            # its own still answers the question when the repository is
            # given beside it.
            repo = args.repo.resolve() if args.repo else tree
        elif args.repo:
            tree, error = _committed_tree(args.repo.resolve())
            if tree is None:
                print("cannot read committed bytes: " + error)
                return 3
            tmp = str(tree)
            repo = args.repo.resolve()
        else:
            print("no tree and no repo given")
            return 3
        result = _audit(tree, repo)
        pages = len(_book_pages(tree))
    except (OSError, ValueError) as exc:
        print("UNKNOWN — shipped-book-program-paths unavailable: " + str(exc))
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"tree={tree}")
    print(f"book pages={pages} path programs checked={result['checked']}")
    for item in result["findings"]:
        print(f"  BOOK vs PROGRAM PATH  {item['file']}: {item['command']}")
        print(f"    the clone does not contain `{item['program']}`: {item['detail']}")
    for item in result["unknown"]:
        print(f"  UNKNOWN  {item['file']}: {item['command']} — {item['detail']}")
    if result["checked"] == 0:
        print("UNKNOWN — the shipped book states no command with a path program, so "
              "there is nothing to check and a pass would be vacuous")
        return 3
    if not result["findings"]:
        print("OK every command in the shipped book names a program the clone contains")
        return 0
    print("FAIL the shipped book hands the reader a command whose program the clone lacks")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
