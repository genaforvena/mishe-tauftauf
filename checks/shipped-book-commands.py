#!/usr/bin/env python3
"""Detector: a command a shipped page hands the reader must run in a clone.

``checks/shipped-book-links.py`` asks whether the reader can *reach* what a
page points at.  ``checks/shipped-book-paths.py`` asks whether a path a page
names exists in the clone.  ``checks/shipped-book-promises.py`` asks whether
the page a promise points at *contains* what the promise names.  None of them
asks whether a command a page hands the reader **runs**.

A command can be spelled correctly, name a module that exists, and still be
unrunnable: the interpreter it names may not exist, and the package it
imports may not be importable without an install the book says is not
needed.  Every existing gate passes such a command, because the gate reads
the page, not the reader's shell.

Measured on this plant on 2026-11-01 (Docs wake 164192):

* ``docs/getting-started.md`` told a reader to read the host's admission
  state with ``python -m mishe_tauftauf.host_capacity --root ...``.  This
  host is Ubuntu 24.04, which ships no ``python`` binary at all --
  ``command -v python`` answers *not found* (exit 127) -- so the reader's
  own command fails before it reaches the plant.  ``docs/analysis-advisor.md``,
  ``docs/completions-judge.md`` and ``docs/publication-checks.md`` name the
  same absent interpreter.
* the same pages name ``python3 -m mishe_tauftauf.<module>`` with no path to
  ``src/``.  The package lives under ``src/`` and the book states that no
  package install is required, so a clone that has not installed it answers
  ``ModuleNotFoundError: No module named 'mishe_tauftauf'``.  Measured
  against the committed tree in a clean environment: the same command with
  ``env PYTHONPATH=src`` in front resolves and runs.
* ``docs/completions-judge.md`` named ``.venv/bin/python -m
  mishe_tauftauf.completions_judge``.  The book never tells the reader to
  create a venv, and ``.venv/`` is gitignored, so the clone the reader
  receives has no such file.

The rule this check enforces:

* a **plant-code command** is a command in the shipped book that runs the
  plant's own code -- ``-m mishe_tauftauf[.module]``, ``-m coordination
  [.module]``, or a tracked script under ``checks/`` or ``tools/``;
* such a command must **run in a clone that has not installed the package**,
  because the book states that a package install is not required;
* it therefore must name an interpreter the reader's host provides
  (``python3``), and the module it imports must resolve with only the
  environment the command itself states.

The defect is read by **executing the resolution**, not by reading the page:
the check builds the environment a reader's shell has (a standard system
``PATH``, no site directory, no user site, no editable install), puts the
tree under test at the working directory, and asks the interpreter whether
the module resolves.  A command that names no plant code is not this
check's business.

Scope and limits, named:

* The check reads the **committed** bytes through ``git archive``, so an
  untracked working-tree file cannot make a stale page look current, and a
  page gitignored out of the public tree is not a reader's problem.
* The reader's environment is modelled as a standard Linux ``PATH``
  (``/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin``) with no
  site directory and no user site.  A reader who installed the package, or
  who runs inside a venv, may succeed where this check reports a finding:
  the finding is that the **clone** the book ships does not run the command
  as written, which is the state the book promises.
* The interpreter is resolved through that ``PATH``.  A host without
  ``python3`` cannot decide the question, and the check answers UNKNOWN
  (exit 3) rather than passing.
* A command that states its import path through a **variable** the reader
  supplies is judged by substituting the tree under test for that variable,
  because that is the value the reader's own checkout supplies.  A
  substitution that still does not resolve is UNKNOWN for that statement,
  not a finding: the reader's value is not knowable here.
* A **script** is probed through the imports it declares.  A script the
  reader writes themselves is not in the clone, so it cannot be probed and
  is not a finding; a tracked script that imports the plant package is
  probed with the script's own directory as the import root, which is what
  Python does when it runs a file.
* Commands that need authority the reader does not have -- ``sudo``, a
  system-installed file outside the clone -- are not this check's question.
* A page that hands the reader no plant-code command is UNKNOWN, never a
  pass: a vacuous pass is not a pass.

Exit status:
  0  every plant-code command in the shipped book runs in a fresh clone
  2  a shipped page hands the reader a command that does not run
  3  the question cannot be decided: no tree, no interpreter, no commands
"""
from __future__ import annotations

import argparse
import ast
import json
import os
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

# The packages the plant owns.  A command that runs one of these is a
# plant-code command.
PLANT_PACKAGES = ("mishe_tauftauf", "coordination")

# The directories whose tracked scripts are the plant's own.
PLANT_SCRIPT_DIRS = ("checks/", "tools/")

# The PATH a reader's shell has before any site directory is prepended to
# it.  The plant's own site puts ``SITE/bin`` first, which is why this is
# the PATH that decides what a *clone* can run.
READER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# The interpreter spelling the book may use.  ``python`` is not one of them:
# a standard Linux host ships no such binary, and where one exists it may be
# a different language version.
PORTABLE_PYTHON = re.compile(r"^python3(\.\d+)?$")

# An interpreter written as a path, such as ``.venv/bin/python``.
PATH_PYTHON = re.compile(r"^[\w./+-]*/python3?(\.\d+)?$")

# A shell variable in a stated path, and the reader's own value for it.
VARIABLE = re.compile(r"\$(?P<name>[A-Z_][A-Z0-9_]*)")

# The probe.  It replaces ``sys.path`` with exactly what the command's own
# environment establishes -- the working directory (or the script's own
# directory), the ``PYTHONPATH`` the command states, and the standard
# library -- and then asks whether the module resolves.  Every question is
# asked in one process, so the gate costs one interpreter start per tree.
PROBE = r'''
import importlib.util, json, os, sys, sysconfig
stdlib = sysconfig.get_paths()["stdlib"]
questions = json.loads(sys.stdin.read())
answers = []
for question in questions:
    entries = [p for p in question["pythonpath"].split(os.pathsep) if p]
    sys.path = [question["path0"]] + entries + [stdlib, os.path.join(stdlib, "lib-dynload")]
    name = question["module"]
    try:
        spec = importlib.util.find_spec(name)
    except ModuleNotFoundError:
        spec = None
    except Exception as exc:
        answers.append({"key": question["key"], "verdict": "ERROR",
                        "detail": type(exc).__name__ + ": " + str(exc)})
        continue
    if spec is None:
        answers.append({"key": question["key"], "verdict": "MISSING", "detail": name})
    else:
        answers.append({"key": question["key"], "verdict": "FOUND", "detail": str(spec.origin)})
sys.stdout.write(json.dumps(answers))
'''


def _committed_tree(repo: Path) -> tuple[Path | None, str]:
    """Materialise the committed bytes of a repository into a temporary tree."""
    tmp = tempfile.mkdtemp(prefix="shipped-book-commands-")
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


def _stated_commands(page: Path) -> list[tuple[str, str]]:
    """The commands a page hands the reader, as (where, text) pairs."""
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
        if span.startswith(("python", "env ", "PYTHONPATH=")) or " python" in span:
            found.append((f"{page.name} inline", span))
    return found


def _tokens(command: str) -> list[str] | None:
    """The shell words of a command, or None when it cannot be read."""
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _split_env(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """The environment a command states, and the rest of it.

    Both forms are read: ``env NAME=VALUE cmd`` and the bare ``NAME=VALUE
    cmd`` prefix a shell accepts.
    """
    assignments: dict[str, str] = {}
    rest = list(tokens)
    if rest and rest[0] == "env":
        rest = rest[1:]
    while rest and "=" in rest[0] and not rest[0].startswith("-") and "/" not in rest[0].split("=")[0]:
        name, _, value = rest[0].partition("=")
        assignments[name] = value
        rest = rest[1:]
    return assignments, rest


def _plant_module(tokens: list[str]) -> str | None:
    """The plant module a command runs, if it runs one."""
    for index, token in enumerate(tokens):
        if token == "-m" and index + 1 < len(tokens):
            name = tokens[index + 1]
            if name.split(".")[0] in PLANT_PACKAGES:
                return name
    return None


def _script_token(tokens: list[str]) -> str | None:
    """The tracked script a command runs, if it runs one."""
    for token in tokens:
        if token.endswith(".py") and token.startswith(PLANT_SCRIPT_DIRS):
            return token
    return None


def _interpreter(tokens: list[str]) -> str | None:
    """The interpreter a command names, if it names one."""
    for token in tokens:
        base = token.rsplit("/", 1)[-1]
        if base == "python" or base.startswith("python3"):
            return token
    return None


def _local_imports(script: Path) -> list[str]:
    """The plant packages a tracked script imports."""
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return sorted(name for name in names if name in PLANT_PACKAGES)


def _resolve_all(python: str, questions: list[dict], cwd: Path) -> dict[str, dict]:
    """Ask the interpreter, once, whether each named module resolves."""
    if not questions:
        return {}
    environment = {
        "PATH": READER_PATH,
        "HOME": str(cwd),
    }
    try:
        result = subprocess.run([python, "-S", "-c", PROBE], env=environment,
                                cwd=str(cwd), input=json.dumps(questions),
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {question["key"]: {"verdict": "ERROR", "detail": f"probe failed: {exc}"}
                for question in questions}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        line = detail[-1] if detail else f"probe exit {result.returncode}"
        return {question["key"]: {"verdict": "ERROR", "detail": line} for question in questions}
    try:
        answers = json.loads(result.stdout)
    except ValueError:
        return {question["key"]: {"verdict": "ERROR", "detail": "probe produced no readable answer"}
                for question in questions}
    return {answer["key"]: answer for answer in answers}


def _import_paths(tree: Path, stated: str) -> tuple[list[Path], bool]:
    """The import roots a command states, and whether any was substituted.

    A path the reader names through a variable is judged with the tree under
    test in that variable's place, because that is the value the reader's
    own checkout supplies.
    """
    paths: list[Path] = []
    substituted = False
    for entry in stated.split(os.pathsep):
        if not entry:
            continue
        if VARIABLE.search(entry):
            substituted = True
            entry = VARIABLE.sub(str(tree), entry)
        path = Path(entry)
        paths.append(path if path.is_absolute() else tree / path)
    return paths, substituted


def _audit(tree: Path, python: str) -> dict:
    """Find the plant-code commands a shipped page cannot run."""
    findings: list[dict] = []
    unknown: list[dict] = []
    checked = 0
    questions: list[dict] = []
    # Each question is (key, module, path0, pythonpath); the answer is read
    # back after one probe run.
    pending: dict[str, dict] = {}
    for page in _book_pages(tree):
        relative = page.relative_to(tree).as_posix()
        for where, command in _stated_commands(page):
            tokens = _tokens(command)
            if not tokens:
                continue
            if tokens and tokens[0] == "sudo":
                # A system-installed file outside the clone, run with
                # authority the reader must supply separately.
                continue
            assignments, rest = _split_env(tokens)
            module = _plant_module(rest)
            script = _script_token(rest)
            if module is None and script is None:
                continue
            checked += 1
            interpreter = _interpreter(rest)
            if interpreter is None:
                findings.append({
                    "file": relative,
                    "command": command,
                    "reason": "names no interpreter the reader can run",
                    "detail": "the command runs plant code but starts with no python",
                })
                continue
            base = interpreter.rsplit("/", 1)[-1]
            if "/" not in interpreter:
                if not PORTABLE_PYTHON.match(base):
                    findings.append({
                        "file": relative,
                        "command": command,
                        "reason": f"names `{interpreter}`, which a standard Linux host does not provide",
                        "detail": "a reader's shell answers 'command not found' before the plant is reached",
                    })
                    continue
            elif not (tree / interpreter).is_file():
                findings.append({
                    "file": relative,
                    "command": command,
                    "reason": f"names the interpreter path `{interpreter}`, which the clone does not contain",
                    "detail": "the reader has no such file",
                })
                continue
            paths, substituted = _import_paths(tree, assignments.get("PYTHONPATH", ""))
            if module is not None:
                key = f"{relative}:{command}"
                pending[key] = {
                    "file": relative,
                    "command": command,
                    "module": module,
                    "path0": str(tree),
                    "pythonpath": os.pathsep.join(str(item) for item in paths),
                    "substituted": substituted,
                }
                questions.append({"key": key, "module": module, "path0": str(tree),
                                  "pythonpath": pending[key]["pythonpath"]})
                continue
            script_path = tree / script
            if not script_path.is_file():
                continue
            for name in _local_imports(script_path):
                key = f"{relative}:{command}:{name}"
                pending[key] = {
                    "file": relative,
                    "command": command,
                    "module": name,
                    "path0": str(script_path.parent),
                    "pythonpath": os.pathsep.join(str(item) for item in paths),
                    "substituted": substituted,
                }
                questions.append({"key": key, "module": name, "path0": str(script_path.parent),
                                  "pythonpath": pending[key]["pythonpath"]})
    answers = _resolve_all(python, questions, tree)
    for key, item in pending.items():
        answer = answers.get(key)
        if answer is None or answer["verdict"] == "FOUND":
            continue
        detail = answer["detail"] if answer else "no answer"
        if item["substituted"]:
            unknown.append({"file": item["file"], "command": item["command"], "detail": detail})
        else:
            findings.append({
                "file": item["file"],
                "command": item["command"],
                "reason": f"`{item['module']}` does not resolve in a clone that has not installed the package",
                "detail": detail,
            })
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
        elif args.repo:
            tree, error = _committed_tree(args.repo.resolve())
            if tree is None:
                print("cannot read committed bytes: " + error)
                return 3
            tmp = str(tree)
        else:
            print("no tree and no repo given")
            return 3
        python = shutil.which("python3", path=READER_PATH)
        if not python:
            print("UNKNOWN — no python3 on the reader's PATH, so the question cannot be decided")
            return 3
        pages = len(_book_pages(tree))
        result = _audit(tree, python)
    except (OSError, ValueError) as exc:
        print("UNKNOWN — shipped-book-commands unavailable: " + str(exc))
        return 3
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"tree={tree}")
    print(f"book pages={pages} plant-code commands checked={result['checked']}")
    for item in result["findings"]:
        print(f"  BOOK vs RUNNABLE COMMAND  {item['file']}: {item['command']}")
        print(f"    {item['reason']}: {item['detail']}")
    for item in result["unknown"]:
        print(f"  UNKNOWN  {item['file']}: {item['command']} — {item['detail']}")
    if result["checked"] == 0:
        print("UNKNOWN — the shipped book states no plant-code command, so there is "
              "nothing to run and a pass would be vacuous")
        return 3
    if not result["findings"]:
        print("OK every plant-code command in the shipped book runs in a fresh clone")
        return 0
    print("FAIL the shipped book hands the reader commands a clone cannot run")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
