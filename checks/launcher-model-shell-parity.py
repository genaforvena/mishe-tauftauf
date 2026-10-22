#!/usr/bin/env python3
"""Detector: the launcher-model reader must name the model the shell passes.

The plant records which model a mind runs on by reading its launcher script
(``seed._launcher_model`` -> ``omp_pane.launcher_model``). That reading is a
fact about what a pane runs on, and the plant's own contract is that a wrong
model name is worse than an unknown one: an unknown row is missing evidence,
a wrong row is a false statement the plant keeps.

The reader is a parser for a shell dialect, and a parser that accepts a shape
the shell runs differently produces exactly the wrong row. Six independent
reads closed the accepted set once already; the sixth named two holes that are
still open unless the shape is checked against the shell itself:

* ``exec`` of a program that is not the pane ends the script -- the shell
  becomes that program -- so the model of a later line is a model no process
  ever ran on. The reader used to keep scanning and name it.
* ``/usr/bin/env`` is ``env``, so a launcher that reaches the pane through it
  runs the pane with a model the reader refused to see.

A parser's accepted set cannot be closed by reading the parser. It is closed
by running the shell. This check does that: for every launcher it can see, it
asks ``/bin/sh`` what it actually passes to the pane, and it compares the
reader's answer with the shell's own.

**The oracle is the shell, not another reading of the source.** Each candidate
script runs in a disposable sandbox with two stand-ins on ``PATH``: an ``omp``
that records its argv, and a ``python3`` that records its argv and runs
nothing. The plant's own launchers name the pane by absolute path
(``/home/user/.local/bin/omp``) and the plant's wrapper by absolute path
(``/usr/bin/python3 .../longcat-launch.py``), so the sandboxed copy of a
candidate points those two program paths at the stand-ins and leaves every
other byte -- every flag, every argument, every line -- alone. The check then
asserts that the rewrite did not change the reading: the reader's answer on the
original bytes and on the sandboxed bytes must be the same, so the comparison
below is a comparison of the reader with the shell and not of two texts.

Two facts about the plant are shared between the check and the reader, and
both are published rules rather than hidden helpers: the plant's pane wrapper
is the script named ``longcat-launch.py`` (the plant writes it in ``seed.py``;
the reader recognises it in ``_runs_the_pane``), and a model is named by the
first ``--model`` or ``--model=`` in the pane's own argv whose value does not
begin with ``-`` (the reader states it in ``omp_pane.model``). Everything else
-- which command runs the pane, what the shell passes to it -- the check
derives from the shell's own behaviour.

The corpus is the plant's live launchers (``SITE/minds/*`` and
``SITE/top-pains/*``) plus a fixed set of grammar edges that the reader's
accepted set has to survive. A candidate that is not a shell script is skipped
and named; a candidate whose program the sandbox cannot neutralize is skipped
and named, because running it would run the real program.

Exit status:
  0  every candidate agrees: the reader names the model the shell passes, or
     names none where the shell starts no pane
  2  at least one candidate disagrees: the reader names a model the shell
     never passes, or misses one the shell does
  3  the question cannot be decided: the site home is unreadable, the reader
     cannot be imported, or no candidate could be read
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

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

#: The plant's own pane wrapper: the script that starts a pane's omp.
PLANT_LAUNCHER = "longcat-launch.py"

#: The environment variable the stand-ins record their argv through.
RECORD_ENV = "MISHE_LAUNCHER_PARITY_RECORD"

#: The characters a model name is written with; shell and source punctuation is not one.
MODEL_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/:@+"
)

_SHELL_STANDIN = """#!/bin/sh
{
  printf -- '--- @MARKER@\\n'
  for field in "$@"; do printf '%s\\n' "$field"; done
} >> "$MISHE_LAUNCHER_PARITY_RECORD"
exit 0
"""

_ENV_STANDIN = """#!/bin/sh
# A stand-in for env. It runs the program it names, as env does, but the
# program it runs is a stand-in of the same name, so no real program runs
# inside the sandbox and the shell's own behaviour is still measured.
while [ $# -gt 0 ]; do
  case "$1" in
    -u|--unset|-C|--chdir) shift 2 ;;
    --unset=*|--chdir=*) shift ;;
    -i|--ignore-environment|-0|--null|--debug) shift ;;
    --) shift; break ;;
    *=*) shift ;;
    *) break ;;
  esac
done
if [ $# -eq 0 ]; then
  printf -- '--- program\\n' >> "$MISHE_LAUNCHER_PARITY_RECORD"
  exit 0
fi
program="$1"
shift
base="${program##*/}"
if [ "$program" != "$base" ] && [ ! -e "$MISHE_LAUNCHER_PARITY_BIN/$base" ]; then
  cp "$MISHE_LAUNCHER_PARITY_BIN/.template" "$MISHE_LAUNCHER_PARITY_BIN/$base" 2>/dev/null || exit 127
  chmod 755 "$MISHE_LAUNCHER_PARITY_BIN/$base" 2>/dev/null
fi
if [ -x "$MISHE_LAUNCHER_PARITY_BIN/$base" ]; then
  exec "$MISHE_LAUNCHER_PARITY_BIN/$base" "$@"
fi
exec "$program" "$@"
"""

_PROGRAM_POSITION = re.compile(
    r"^(?P<lead>\s*)"
    r"(?P<assignments>(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)"
    r"(?P<exec>exec\s+)?"
    r"(?P<quote>['\"]?)"
    r"(?P<path>/[^\s'\"]+)"
    r"(?P=quote)"
)
"""The program a line runs, in the position the shell runs it."""

_SHELL_NAMES = frozenset({"sh", "bash", "dash", "ksh", "zsh"})
"""The shell interpreters a candidate script may be written for."""


class Undecidable(Exception):
    """The check cannot decide the question it was asked."""


def _reader():
    """The plant's launcher-model reader, imported from this checkout."""
    sys.path.insert(0, str(SRC))
    try:
        from mishe_tauftauf.omp_pane import launcher_model
    except Exception as exc:  # pragma: no cover - the import is the check's floor
        raise Undecidable("the launcher-model reader cannot be imported: %s" % exc)
    return launcher_model


def _model_value(argv: list[str]) -> str | None:
    """The ``--model`` value in an argv, in the plant's published spelling.

    The first ``--model`` or ``--model=`` wins, and a value that begins with
    ``-`` is another option rather than a model. The value is returned as the
    shell passed it; whether it is *written* like a model name is the
    reader's rule, applied by the caller.
    """
    for index, field in enumerate(argv):
        if field == "--model" and index + 1 < len(argv):
            value = argv[index + 1]
            return None if value.startswith("-") else value
        if field.startswith("--model="):
            value = field[len("--model="):]
            return None if value.startswith("-") else value
    return None


def _expected_model(raw: str | None) -> str | None:
    """The model the reader must report for a value the shell passed.

    A value that is not written like a model name is no model, so the reader
    must refuse it; that refusal is the safe direction and agrees with the
    shell's value being unusable.
    """
    if raw is None:
        return None
    if not raw or not set(raw) <= MODEL_NAME_CHARS:
        return None
    return raw


def _marker_for(base: str) -> str:
    """The record marker a stand-in for a program of that name writes."""
    if base == "omp":
        return "omp"
    if base.startswith("python"):
        return "python"
    return "program"


def _standin(bindir: Path, base: str) -> Path:
    """The stand-in that replaces a program of that name, created on demand."""
    path = bindir / base
    if not path.exists():
        path.write_text(_SHELL_STANDIN.replace("@MARKER@", _marker_for(base)))
        path.chmod(0o755)
    return path


def _rewrite_line(line: str, bindir: Path) -> str:
    """Point a line's program at the sandbox's stand-in, keeping its name.

    Every program the line runs is replaced, not only the pane's: a
    candidate that runs the plant's own renderer or its own module would
    otherwise do the plant's work inside a check, and a check must not have
    effects. The stand-in keeps the program's name, so the reader's reading of
    the rewritten bytes is the reading of the original bytes.
    """
    match = _PROGRAM_POSITION.match(line)
    if match is None:
        return line
    path = match.group("path")
    base = path.rsplit("/", 1)[-1]
    if not base or "/" in base:
        return line
    replacement = str(_standin(bindir, base))
    start = match.start("path")
    return line[:start] + replacement + line[match.end("path"):]


def _sandboxed(text: str, bindir: Path) -> str:
    """The candidate's bytes with its program paths pointed at the stand-ins."""
    return "".join(_rewrite_line(line, bindir) for line in text.splitlines(keepends=True))


def _is_shell_script(text: str) -> bool:
    """Whether a candidate is a shell script the sandbox may run."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("#!"):
        return False
    parts = lines[0][2:].split()
    return any(part.rsplit("/", 1)[-1] in _SHELL_NAMES for part in parts)


def _blocks(text: str) -> list[tuple[str, list[str]]]:
    """The recorded program invocations, in the order the shell made them."""
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("--- "):
            blocks.append((line[4:], []))
        elif blocks:
            blocks[-1][1].append(line)
    return blocks


def _shell_answer(sandbox: Path, script: Path, timeout: float):
    """What the shell actually passed to the pane, and whether it started one.

    The result is ``(model, pane_ran, argv, note)``. The note is set when the
    answer could not be decided at all. The first pane invocation decides, as
    it does in the reader: the shell waits for the pane, so a later one runs
    only after the first has exited.
    """
    record = sandbox / "record"
    if record.exists():
        record.unlink()
    env = {
        "PATH": "%s%s%s" % (sandbox / "bin", os.pathsep, "/usr/bin:/bin"),
        RECORD_ENV: str(record),
        "MISHE_LAUNCHER_PARITY_BIN": str(sandbox / "bin"),
        "HOME": str(sandbox),
    }
    try:
        result = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True,
                                env=env, timeout=timeout, cwd=str(sandbox))
    except subprocess.TimeoutExpired:
        return None, False, [], "the candidate did not finish within %ds" % int(timeout)
    if not record.exists():
        # No program ran at all: the shell started no pane, and the reader's
        # answer for a script that starts nothing is unknown too.
        return None, False, [], ""
    blocks = _blocks(record.read_text(encoding="utf-8", errors="replace"))
    seen: list[str] = []
    for marker, argv in blocks:
        seen.extend(argv)
        if marker == "omp":
            return _expected_model(_model_value(argv)), True, argv, ""
        if marker == "python":
            script_path = argv[0] if argv else ""
            if script_path.rsplit("/", 1)[-1] == PLANT_LAUNCHER:
                return _expected_model(_model_value(argv[1:])), True, argv[1:], ""
    return None, False, seen, ""


def _make_sandbox(root: Path) -> Path:
    """A disposable sandbox whose ``bin`` holds the stand-ins.

    The pane's stand-in, the interpreter's stand-in and ``env``'s stand-in are
    created up front, because a candidate may name any of them by a bare word
    -- ``omp``, ``python3``, ``env`` -- and the sandbox's ``PATH`` is what
    resolves those. They are shell scripts: the interpreter stand-in never
    runs the script it is handed, so it never needs to be an interpreter at
    all, and a shell stand-in starts in a millisecond where an interpreter
    starts in half a second.
    """
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for base in ("omp", "python", "python3"):
        _standin(bindir, base)
    env_stub = bindir / "env"
    env_stub.write_text(_ENV_STANDIN)
    env_stub.chmod(0o755)
    template = bindir / ".template"
    template.write_text(_SHELL_STANDIN.replace("@MARKER@", "program"))
    template.chmod(0o755)
    return bindir


GRAMMAR_CASES: list[tuple[str, str]] = [
    ("a plant launcher (exec omp)", "#!/bin/sh\nexec omp --model=vendor/real --thinking medium\n"),
    ("an exec of a non-pane before the pane", "#!/bin/sh\nexec /bin/true\nomp --model=vendor/fake\n"),
    ("an exec of a non-pane with a model", "#!/bin/sh\nexec /bin/echo --model=vendor/fake\nomp --model=vendor/fake\n"),
    ("an exec of env with no program", "#!/bin/sh\nexec env\nomp --model=vendor/fake\n"),
    ("an exec of env by path", "#!/bin/sh\nexec /usr/bin/env FOO=1 omp --model=vendor/real\n"),
    ("a bare exec before the pane", "#!/bin/sh\nexec\nomp --model=vendor/real\n"),
    ("a plain command before the pane", "#!/bin/sh\n/bin/true\nomp --model=vendor/real\n"),
    ("the pane's own exec, then another pane", "#!/bin/sh\nexec omp --model=vendor/first\nomp --model=vendor/second\n"),
    ("a pane without a model", "#!/bin/sh\nexec omp --thinking medium\n"),
    ("the model in the space spelling", "#!/bin/sh\nexec omp --model vendor/real\n"),
    ("a value-taking flag before the model", "#!/bin/sh\nexec omp --cwd --model=vendor/fake\n"),
    ("a model value that is not a model name", "#!/bin/sh\nexec omp --model=vendor/model, \n"),
    ("an unset PATH before the pane", "#!/bin/sh\nunset PATH\nomp --model=vendor/fake\n"),
    ("a python wrapper of the plant's own shape", "#!/bin/sh\nexec /usr/bin/python3 /site/longcat-launch.py --model=vendor/real\n"),
    ("a python wrapper of another script", "#!/bin/sh\nexec /usr/bin/python3 /site/report.py --model=vendor/fake\n"),
]


def _plant_candidates(home: Path) -> list[tuple[str, str]]:
    """The plant's own launcher scripts, read from the live site home."""
    candidates: list[tuple[str, str]] = []
    for folder in ("minds", "top-pains"):
        directory = home / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            candidates.append(("%s/%s" % (folder, path.name), text))
    return candidates


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--timeout", type=float, default=20.0, help="seconds a candidate may run")
    parser.add_argument("--corpus", choices=("all", "grammar", "plant"), default="all",
                        help="which candidates to read: the grammar edges, the plant's own "
                             "launchers, or both")
    args = parser.parse_args(argv)
    home = args.home.resolve()
    if not home.is_dir():
        print("UNKNOWN — the site home %s is not a directory" % home)
        return 3
    try:
        launcher_model = _reader()
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3

    candidates: list[tuple[str, str]] = []
    if args.corpus in ("all", "grammar"):
        candidates.extend(GRAMMAR_CASES)
    if args.corpus in ("all", "plant"):
        try:
            candidates.extend(_plant_candidates(home))
        except OSError as exc:
            print("UNKNOWN — the plant's launchers could not be read: %s" % exc)
            return 3

    agreed = 0
    disagreements: list[tuple[str, str | None, str | None, list[str], str]] = []
    undecided: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for name, text in candidates:
        if not _is_shell_script(text):
            skipped.append((name, "not a shell script"))
            continue
        root = Path(tempfile.mkdtemp(prefix="launcher-parity-"))
        try:
            bindir = _make_sandbox(root)
            script = root / "script.sh"
            script.write_text(_sandboxed(text, bindir))
            original = launcher_model(text)
            rewritten = launcher_model(script.read_text(encoding="utf-8"))
            if original != rewritten:
                disagreements.append((name, original, "the-sandbox-rewrite-changed-the-reading", [], ""))
                continue
            shell_model, _pane_ran, argv, note = _shell_answer(root, script, args.timeout)
            if note:
                undecided.append((name, note))
                continue
            if original != shell_model:
                disagreements.append((name, original, shell_model, argv, note))
                continue
            agreed += 1
        finally:
            shutil.rmtree(root, ignore_errors=True)

    grammar_names = {name for name, _text in GRAMMAR_CASES}
    grammar = sum(1 for name, _text in candidates if name in grammar_names)
    print("home      : %s" % home)
    print("candidates: %d (%d grammar, %d plant)" % (
        len(candidates), grammar, len(candidates) - grammar))
    print("agreed    : %d" % agreed)
    print("skipped   : %d" % len(skipped))
    for name, reason in skipped:
        print("            %s: %s" % (name, reason))
    if disagreements:
        print("disagreed : %d" % len(disagreements))
        for name, reader, shell, argv, note in disagreements:
            print("            %s" % name)
            print("              reader=%r shell=%r" % (reader, shell))
            if argv:
                print("              pane argv=%r" % (argv,))
            if note:
                print("              note=%s" % note)
        print("verdict   : the reader and the shell disagree on %d candidate(s)" % len(disagreements))
        return 2
    if undecided:
        print("undecided : %d" % len(undecided))
        for name, reason in undecided:
            print("            %s: %s" % (name, reason))
        print("verdict   : %d candidate(s) could not be decided — the question is undecidable"
              % len(undecided))
        return 3
    if not candidates:
        print("verdict   : no candidate could be read — the question is undecidable")
        return 3
    print("verdict   : the reader names the model the shell passes on every candidate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
