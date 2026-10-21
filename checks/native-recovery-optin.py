#!/usr/bin/env python3
"""Detector: a recovery opt-in that names a wake the plant will not deliver.

A native resident's launcher can opt one wake into a fresh recovery attempt:

    export MISHE_NATIVE_RECOVERY_WAKE=75277
    export MISHE_NATIVE_RECOVERY_ATTEMPT=1

The rule both consumers of that opt-in follow is the resident's own: the
opt-in must name *the exact actual pending wake* of the role. The recovery
runner compares the opt-in with the wake the plant still owes the role
(``runner.py``: ``recovery = os.environ.get('MISHE_NATIVE_RECOVERY_WAKE') ==
str(wake)``, where ``wake`` is the role's pending wake), and the resident
refuses to start with ``recovery opt-in must name the exact actual pending
wake`` when the two differ. The comparison is a comparison of *strings*: the
opt-in's text and the wake's number as text.

Nothing in the plant reads that mismatch. The opt-in lives in a launcher file
the integration owner edits by hand, the plant owes the role a *different*
wake by then, and the recovery attempt the owner wanted does not happen: the
consumer's own comparison is false, so the wake is not run as a recovery.

Measured on 2026-10-21 on this plant: ``minds/docs`` named wake 70477 while
the plant owed docs wake 75277, and the plant's last yielded and last cleared
wake for docs was 75010.

This check reads the opt-in from the launcher's own bytes and the pending wake
from the reviewed tree's own state reader, and reports the mismatch. It
delivers, holds and releases nothing: a reported mismatch is a fact about the
plant's bytes, not a prediction that a delivery will happen.

What it reports:

  MISMATCH  the opt-in does not name a wake the plant will deliver — the
            opt-in names another wake, names no wake at all while naming an
            attempt, names a text that is not a wake number, names an attempt
            the consumer cannot read as a number, is written in an export the
            shell rejects, or the plant owes the role no wake.

What it deliberately does not report, and why: whether the recovery attempt
the opt-in names would collide with an attempt directory that already exists.
That question needs the directory the *consumer* creates its attempt in, and
that directory is the consumer's own business — it is a constant in a program
the operator installed, and no reviewed rule names it. Reading it out of that
program's bytes means guessing: a review of an earlier version of this check
reproduced four ways to read a directory the consumer does not use, and one
way to miss a collision that exists. A detector that can answer "agreement"
while a real collision sits on disk is worse than no detector, so the question
is left to a reader of the consumer's bytes, and this check reports only what
it can decide.

How it reads, and what it therefore claims:

- The plant's facts come from a clean export of HEAD, never from the working
  tree's uncommitted bytes, and are read through the plant's own readers: the
  role set from ``activity.roles``, the pending wake from ``seed._state``, the
  tape through the feed's byte parser with the plant's own record
  verification, so no checkpoint is written.
- The opt-in comes from the launcher's own text, one line at a time, read the
  way the shell reads it: a literal value is read, a value that expands is
  unknown, and an export the shell rejects is a finding, because the value
  the owner wrote cannot reach the consumer.
- The role the opt-in is compared with is the role the *consumer* declares
  (``ROLE = 'docs'`` in the recovery runner), not the launcher's file name:
  a launcher whose name is not the role's name would otherwise be compared
  with the wrong wake. A launcher the plant knows no role for reads UNKNOWN,
  never an agreement.
- The consumer's rule is cited, not decided: the check names the line where
  the consumer compares the opt-in with the pending wake, and it recognises
  that line only when the operands are exactly the environment value and a
  stringified wake. What an attempt number has to be is the consumer's own
  rule, so the check asks the consumer's bytes before it judges one.
- The consumer's rule about the attempt is read, not assumed. The conversion is
  found wherever the consumer writes it — including a read of the environment
  with a subscript and a read into a name that is converted afterwards — and
  the check then asks two more questions of the same bytes: whether the
  consumer survives a failed conversion, and whether the conversion sits on a
  path the module takes. A conversion the consumer survives is not a defect; a
  conversion on a path the module never takes is not a decision the check can
  make; a value the check cannot trace back to the attempt is not the attempt.
  One limit is named: a value the consumer receives as an argument is not
  traced, so a consumer that converts a passed-in attempt reads UNKNOWN rather
  than an agreement.

Exit status:
  0  every role's opt-in names the wake the plant will deliver
  2  at least one mismatch is reported
  3  the question cannot be decided: an unreadable launcher, an unreadable or
     semantically invalid tape, a missing reviewed tree, an unknown role. A
     reported finding outranks an unknown reading: the finding is actionable
     and the unknown readings are printed with it.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = "mishe_tauftauf"
RECOVERY_WAKE = "MISHE_NATIVE_RECOVERY_WAKE"
RECOVERY_ATTEMPT = "MISHE_NATIVE_RECOVERY_ATTEMPT"
# The opt-in is read one line at a time: a launcher is a shell script, and a
# value that is empty must stay empty instead of swallowing the next line.
OPT_IN_NAMES = (RECOVERY_WAKE, RECOVERY_ATTEMPT)
# A line that names one of the opt-in's variables.
MENTIONS_OPT_IN_RE = re.compile(r"\b(%s|%s)\b" % (RECOVERY_WAKE, RECOVERY_ATTEMPT))
# The shell's own forms: `export NAME=value` exports a name, `export NAME`
# exports the name's current value, and a plain `NAME=value` only sets the
# shell's variable. The shell accepts an identifier as a name and nothing else.
EXPORT_RE = re.compile(r"^[ \t]*export[ \t]+([A-Za-z_][A-Za-z0-9_]*)(.*)$")
ASSIGN_RE = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# The probe the check asks the launcher's own shell: does that shell end the
# script at an export it reports as an error? The probe uses one fixed name
# and one fixed word, so it runs the shell's own answer and nothing of the
# launcher's.
SHELL_PROBE = "export V=1 2; printf REACHED"
EXEC_RE = re.compile(r"^[ \t]*exec[ \t]+(.*)$", re.MULTILINE)
# A wake number, as the plant's own numbers are written: ASCII decimal digits.
# `str.isdigit` accepts other digits (`²`), and `int()` then raises, so the
# digits are matched explicitly and the number is read without raising.
WAKE_RE = re.compile(r"[0-9]+\Z")
# A launcher's name, as the plant's own slugs are written.
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")

# The child rebuilds its own import path: the given root plus the standard
# library. An ambient editable install would otherwise answer for the root
# under test, and the check would read agreement where there is none.
CHILD = r"""
import json
import os
import sys
import sysconfig
from pathlib import Path

root = os.path.abspath(sys.argv[1])
home = Path(sys.argv[2])
paths = sysconfig.get_paths()
stdlib = {paths["stdlib"], paths["platstdlib"]}
ambient = {paths["purelib"], paths["platlib"]}
keep = []
for entry in sys.path:
    if not entry:
        continue
    resolved = os.path.abspath(entry)
    if any(resolved == base or resolved.startswith(base + os.sep) for base in ambient):
        continue
    if any(resolved == base or resolved.startswith(base + os.sep) for base in stdlib):
        keep.append(entry)
sys.path = [root] + keep

from mishe_tauftauf import activity, seed
from mishe_tauftauf.feed import Feed, parse_feed

roles = sorted(activity.roles(home))
tape = Feed(home)
entries = parse_feed(tape.read_bytes(), home=home)
# The reviewed reader verifies every record a tape entry references before it
# accepts the entry. This check reads the tape without the reviewed reader's
# checkpoint, so it applies that verification itself, through the reviewed
# reader's own rule: a record that cannot be resolved makes the reading
# UNKNOWN rather than clean.
Feed._verify_records(entries)
states = {}
for role in roles:
    digest, pending, last_yield, last_clear = seed._state(home, role, entries=entries)[:4]
    states[role] = {"pending": pending, "last_yield": last_yield, "last_clear": last_clear}
print(json.dumps({"roles": roles, "states": states}))
"""


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def export_head(repo: Path, workdir: Path) -> tuple:
    """A clean export of the reviewed HEAD tree, and the commit it came from.

    The reference is the reviewed tree and not the working tree's uncommitted
    bytes, so an unreviewed change cannot make the check agree by itself.
    """
    try:
        archive = subprocess.run(["git", "-C", str(repo), "archive", "HEAD", "src"],
                                 capture_output=True)
    except OSError as exc:
        raise Undecidable("the reviewed tree cannot be exported: " + type(exc).__name__)
    if archive.returncode:
        raise Undecidable("cannot export HEAD from " + str(repo))
    try:
        extract = subprocess.run(["tar", "-x", "-C", str(workdir)], input=archive.stdout,
                                 capture_output=True)
    except OSError as exc:
        raise Undecidable("the reviewed tree cannot be unpacked: " + type(exc).__name__)
    if extract.returncode:
        raise Undecidable("cannot unpack the HEAD export")
    root = workdir / "src"
    if not (root / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the HEAD export carries no plant package")
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True)
    except OSError:
        head = None
    commit = head.stdout.strip() if head is not None and head.returncode == 0 else "unknown"
    return root, commit


def read_state(root: Path, home: Path, cwd: Path) -> dict:
    """The roles the plant knows and the wake each one is owed, read by the reviewed tree."""
    try:
        result = subprocess.run([sys.executable, "-I", "-c", CHILD, str(root), str(home)],
                                capture_output=True, text=True, cwd=str(cwd))
    except OSError as exc:
        raise Undecidable("the plant's state could not be read: " + type(exc).__name__)
    if result.returncode:
        # The child's own words, not its traceback: a reader of this plant
        # wants to know that the reading is unknown, not to read a stack.
        detail = [line for line in (result.stderr or "").splitlines() if line.strip()]
        raise Undecidable("the plant's state could not be read: "
                          + (detail[-1][-200:] if detail else "no reason given"))
    try:
        state = json.loads(result.stdout)
    except ValueError:
        raise Undecidable("the plant's state was printed unreadably")
    if not isinstance(state, dict) or not isinstance(state.get("states"), dict):
        raise Undecidable("the plant's state carried no role states")
    return state


def _strip_comment(line: str) -> str:
    """The line without its shell comment: an unquoted ``#`` that begins a word.

    ``75277 # why`` exports ``75277`` and ``75277#why`` exports the whole
    text, so the comment's start is where a ``#`` begins a word.
    """
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def _read_word(text: str):
    """The literal a shell word holds, the text after it, and what it is.

    A word is literal when the shell would read the same characters: a quoted
    part is literal, an unquoted ``$`` or backtick expands, and a backslash or
    a quote that is not closed is an error the shell reports. The check reads a
    literal and nothing else, because the consumer would see the expansion and
    not the text.
    """
    value = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t":
            break
        if char == "'":
            end = text.find("'", index + 1)
            if end == -1:
                return None, "", "unterminated"
            value.append(text[index + 1:end])
            index = end + 1
            continue
        if char == '"':
            end = index + 1
            segment = []
            while end < length and text[end] != '"':
                if text[end] in "$`\\":
                    return None, "", "expands"
                segment.append(text[end])
                end += 1
            if end == length:
                return None, "", "unterminated"
            value.append("".join(segment))
            index = end + 1
            continue
        if char in "$`":
            return None, "", "expands"
        if char in ";&|<>()\\":
            return None, "", "expands"
        value.append(char)
        index += 1
    return "".join(value), text[index:], "literal"


def _shell_command_end(text: str):
    """The text of one shell command, and the text that follows it.

    A metacharacter ends the command: ``export A=1 && export B=2`` runs two
    commands, and the shell does not read ``&&`` as a name. Only the text
    before the first metacharacter belongs to the export, so the export's own
    reading is made of that text alone. A metacharacter inside quotes is part
    of the value, and does not end the command.
    """
    quote = None
    for index, char in enumerate(text):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            continue
        if char in ";&|<>()":
            return text[:index], text[index:]
    return text, ""


def _export_words(text: str):
    """The words the shell reads as ``export`` arguments."""
    return text.split()


def _is_export_argument(word: str) -> bool:
    """Whether the shell accepts a word as an ``export`` argument.

    The shell accepts a name and a name with a value: ``export A`` and
    ``export A=1`` are both legal arguments, and a word that is neither is
    the shell's "bad variable name" error.
    """
    if IDENTIFIER_RE.fullmatch(word):
        return True
    name, separator, _ = word.partition("=")
    return bool(separator) and bool(IDENTIFIER_RE.fullmatch(name))


def _read_export(entry: dict, rest: str) -> None:
    """Apply one ``export NAME...`` statement to the name's state.

    The shell's export takes a list of names. ``export NAME=value`` exports
    that value, ``export NAME`` exports the name's current value, and a word
    the shell does not accept as an identifier (``=``, a number) is an error
    the shell reports. A metacharacter ends the command instead, so the text
    after it is another command and not a name.
    """
    if rest == "":
        # `export NAME`: the name's current value is exported.
        entry["exported"] = True
        return
    # One place decides where this command ends. The text after the
    # metacharacter is another command, not part of this export, so the
    # export's own value and words are read from the text before it.
    command, _ = _shell_command_end(rest)
    if command.startswith("="):
        value, remainder, kind = _read_word(command[1:])
        if kind == "expands":
            entry["value"] = None
            entry["expands"] = True
            entry["exported"] = True
            entry["rejected"] = None
            entry["errored"] = None
            return
        if kind == "unterminated":
            entry["rejected"] = "the quote is not closed"
            return
        if remainder.strip():
            words = _export_words(remainder)
            bad = [word for word in words
                   if not _is_export_argument(word)]
            if value and not bad:
                # `export A=1 B=2`: both names are exported and the shell
                # reports no error.
                entry["value"] = value
                entry["expands"] = False
                entry["exported"] = True
                entry["rejected"] = None
                entry["errored"] = None
                return
            if value:
                # The value is written, but the shell also reads the text
                # after it as another name and reports an error for it.
                entry["value"] = value
                entry["expands"] = False
                entry["exported"] = True
                entry["errored"] = ("the shell reads %r as another name to export"
                                    % remainder.strip())
                return
            entry["rejected"] = ("the shell reads %r as a name to export"
                                 % remainder.strip())
            return
        entry["value"] = value
        entry["expands"] = False
        entry["exported"] = True
        entry["rejected"] = None
        entry["errored"] = None
        return
    words = _export_words(command)
    if all(IDENTIFIER_RE.fullmatch(word) for word in words):
        # `export NAME other`: the name's current value is exported too.
        entry["exported"] = True
        return
    entry["rejected"] = "the shell reads %r as a name to export" % command.strip()


def _read_assignment(entry: dict, text: str) -> None:
    """Apply one ``NAME=value`` assignment to the name's state.

    An assignment sets the shell's variable and exports nothing. The consumer
    is another process, so a value the launcher only assigns does not reach it
    unless the launcher exports the name as well.
    """
    value, remainder, kind = _read_word(text)
    if kind == "literal" and not remainder.strip():
        entry["value"] = value
        entry["expands"] = False
        return
    entry["value"] = None
    entry["expands"] = True


def read_opt_in(text: str) -> dict:
    """The recovery opt-in a launcher carries, as the shell would carry it.

    A launcher is a shell script the integration owner edits by hand, so the
    opt-in is read from its own text, the way the shell reads it:

    * ``export NAME=value`` exports a literal the check can read;
    * a value that expands is not a literal the check may read — the consumer
      would see the expansion, and the check does not read the plant's
      environment to resolve it;
    * an export the shell rejects — a space around the ``=``, a name the shell
      does not accept — does not carry the value the owner wrote, so the
      recovery the owner wanted cannot happen;
    * a name that is assigned but never exported may still reach the consumer
      through the environment it inherits, so that reading stays unknown.

    The shell exports a variable's *current* value, so an assignment after the
    export changes what the consumer sees, and the last export of a name
    decides, as it does in the shell.
    """
    state = {name: {"value": None, "expands": False, "exported": False,
                     "rejected": None, "errored": None} for name in OPT_IN_NAMES}
    for line in text.splitlines():
        bare = _strip_comment(line)
        if not MENTIONS_OPT_IN_RE.search(bare):
            continue
        export = EXPORT_RE.match(bare)
        assign = ASSIGN_RE.match(bare)
        if export and export.group(1) in OPT_IN_NAMES:
            _read_export(state[export.group(1)], export.group(2))
        elif assign and assign.group(1) in OPT_IN_NAMES:
            _read_assignment(state[assign.group(1)], assign.group(2))
    readings = {}
    for name, entry in state.items():
        if entry["rejected"]:
            readings[name] = {"kind": "rejected", "value": None,
                              "reason": entry["rejected"]}
        elif entry["errored"]:
            readings[name] = {"kind": "errored", "value": entry["value"],
                              "reason": entry["errored"]}
        elif not entry["exported"]:
            if entry["value"] is None and not entry["expands"]:
                continue
            readings[name] = {"kind": "unknown", "value": None,
                              "reason": "the opt-in is assigned but not exported; the consumer "
                                        "may see a value the launcher inherits"}
        elif entry["expands"]:
            readings[name] = {"kind": "unknown", "value": None,
                              "reason": "the opt-in's value expands; the check does not read "
                                        "the plant's environment to resolve it"}
        elif entry["value"] is None:
            readings[name] = {"kind": "unknown", "value": None,
                              "reason": "the opt-in is exported without a value the launcher "
                                        "sets; the consumer sees the value it inherits"}
        else:
            readings[name] = {"kind": "value", "value": entry["value"], "reason": ""}
    return readings


def launcher_files(home: Path) -> list:
    """The launchers the site holds, by the plant's own naming of a slug.

    A launcher is a file under ``minds`` whose name is a slug. The plant's own
    rule for who is a *role* comes from the reviewed tree; this list is the
    scope in which a launcher's own bytes are read, and it does not depend on
    the reviewed tree, so a finding that needs only the launcher's bytes
    survives a plant whose state cannot be read.
    """
    try:
        entries = sorted((home / "minds").iterdir())
    except OSError:
        return []
    found = []
    for path in entries:
        try:
            if not path.exists():
                continue
        except OSError:
            continue
        if SLUG_RE.fullmatch(path.name):
            found.append(path)
    return found


def runner_path(text: str) -> Path | None:
    """The program the launcher runs, when the launcher names one by path.

    A relative path is not a path the plant can be asked about: the
    consumer's working directory is the resident's, not the check's, so a
    relative target is not cited.
    """
    for match in EXEC_RE.finditer(text):
        tokens = match.group(1).split()
        for token in reversed(tokens):
            candidate = Path(token)
            if not candidate.is_absolute() or candidate.suffix != ".py":
                continue
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


def _environment_read(node, name: str) -> bool:
    """Whether the node reads a variable out of the environment.

    Two forms read the same variable: ``os.environ.get(NAME)`` and
    ``os.environ[NAME]``.  A reader that knows only the first form reads a
    consumer that uses the second as one that reads nothing at all, and then
    agrees with a consumer that breaks on a value the owner wrote.
    """
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ" and len(node.args) >= 1
            and isinstance(node.args[0], ast.Constant) and node.args[0].value == name):
        return True
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ" and isinstance(node.slice, ast.Constant)
            and node.slice.value == name):
        return True
    return False


def _environment_value(node) -> bool:
    """Whether the node reads the recovery opt-in out of the environment."""
    return _environment_read(node, RECOVERY_WAKE)


def _stringified_wake(node) -> str | None:
    """The wake a node stringifies, when the node is ``str(<wake>)``."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "str" and len(node.args) == 1):
        return None
    argument = node.args[0]
    return argument.id if isinstance(argument, ast.Name) else None


def comparison_line(tree: ast.Module) -> int | None:
    """The line where a consumer compares the opt-in with a stringified wake.

    The operands must be exactly the environment value and ``str(<wake>)``: a
    consumer that compares the opt-in with anything else is not a consumer of
    this rule, and the check says so rather than citing a line that does not
    decide anything.
    """
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)):
            continue
        left, right = node.left, node.comparators[0]
        if _environment_value(left) and _stringified_wake(right) is not None:
            return node.lineno
        if _environment_value(right) and _stringified_wake(left) is not None:
            return node.lineno
    return None


def consumer_role(tree: ast.Module) -> str | None:
    """The role the consumer serves, from the consumer's own declaration.

    The consumer names the role it serves as a module constant (``ROLE =
    'docs'`` in the recovery runner). Reading it there is citing the consumer;
    the launcher's file name is only a name, and a launcher whose name is not
    the role's name would otherwise be compared with the wrong wake.
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "ROLE"
                   for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


#: The exceptions a handler can catch that let a failed conversion survive.
SURVIVABLE = ("ValueError", "TypeError", "Exception", "BaseException")


def _parents(tree: ast.Module) -> dict:
    """The parent of every node, so a node's scopes can be read."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _scope_nodes(scope) -> list:
    """The statements a scope owns, not counting a nested scope's own."""
    owned = []
    stack = list(getattr(scope, "body", []))
    while stack:
        node = stack.pop()
        owned.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                  ast.ClassDef)):
                continue
            stack.append(child)
    return owned


def _scope_chain(node, parents: dict) -> list:
    """The scopes that enclose a node, innermost first."""
    chain = []
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            chain.append(current)
    return chain


def _assignments(node):
    """The (name, value) pairs a statement binds."""
    if isinstance(node, ast.Assign):
        return [(target, node.value) for target in node.targets
                if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [(node.target, node.value)]
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return [(node.target, node.value)]
    return []


def _bindings(scope) -> dict:
    """The names a scope binds, and how each binding reads the attempt.

    ``env`` is an unconditional binding of the attempt out of the environment,
    ``env?`` a binding that only happens on one path, and ``other`` a binding of
    anything else.  The three are kept apart because they decide different
    things: a name bound to the attempt on one path only is a value the check
    cannot trace, so it cannot say what the consumer converts.
    """
    direct = set(id(statement) for statement in getattr(scope, "body", []))
    bindings = {}
    for node in _scope_nodes(scope):
        for target, value in _assignments(node):
            if value is None:
                continue
            if _environment_read(value, RECOVERY_ATTEMPT):
                kind = "env" if id(node) in direct else "env?"
            else:
                kind = "other"
            bindings[target.id] = kind
    return bindings


def _binding_kind(name: str, tree: ast.Module, parents: dict, node, cache: dict) -> str | None:
    """How the innermost visible scope that binds a name binds it."""
    for scope in _scope_chain(node, parents):
        bindings = cache.get(id(scope))
        if bindings is None:
            bindings = _bindings(scope)
            cache[id(scope)] = bindings
        if name in bindings:
            return bindings[name]
    bindings = cache.get(id(tree))
    if bindings is None:
        bindings = _bindings(tree)
        cache[id(tree)] = bindings
    return bindings.get(name)


def _handler_catches(handler) -> bool:
    """Whether the handler catches the error a failed conversion raises."""
    kind = handler.type
    if kind is None:
        return True
    names = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    for entry in names:
        if isinstance(entry, ast.Name) and entry.id in SURVIVABLE:
            return True
    return False


def _reraises(node) -> bool:
    """Whether the handler hands the error on instead of surviving it."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Raise):
            return True
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)
    return False


def _is_guarded(node, parents: dict) -> bool:
    """Whether a failed conversion where the call sits is survived."""
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # The try is in an enclosing scope, so it cannot catch this call.
            return False
        if isinstance(current, ast.Try):
            for handler in current.handlers:
                if _handler_catches(handler) and not _reraises(handler):
                    return True
    return False


def _called_names(tree: ast.Module) -> set:
    """The names the module calls."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _is_reached(node, parents: dict, called: set) -> bool:
    """Whether the module ever calls the function the call sits in.

    A function the module never calls may still be called from outside it, so
    the check does not decide that such a path is dead; it says the path is not
    one the module takes.
    """
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name in called
    return True


def attempt_rule(tree: ast.Module) -> dict:
    """How the consumer reads the attempt value, from the consumer's bytes.

    What an attempt value has to be is the consumer's own rule, so the check
    asks the consumer's bytes instead of assuming one.  Three things are read,
    because each of them can turn a finding into no finding:

    * the conversion is found wherever it is written, including the form that
      reads the environment with a subscript and the form that reads it into a
      name first and converts the name afterwards;
    * the guard around the conversion is read, so a conversion the consumer
      survives is not a defect;
    * the path the conversion sits on is read, so a conversion on a path the
      module never takes is not a decision the check can make.

    The states are ``int`` (the consumer converts the attempt on a path it
    takes and does not survive a failure), ``guarded`` (the consumer survives
    the failure), ``unreached`` (the conversion sits in a function the module
    never calls, so the module's run does not take that path), ``untraced``
    (the value the conversion reads cannot be traced back to the attempt) and
    ``none`` (the consumer does not convert the attempt).
    """
    parents = _parents(tree)
    called = _called_names(tree)
    cache: dict = {}
    states = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "int" and node.args):
            continue
        argument = node.args[0]
        if _environment_read(argument, RECOVERY_ATTEMPT):
            source = "env"
        elif isinstance(argument, ast.Name):
            kind = _binding_kind(argument.id, tree, parents, node, cache)
            source = {"env": "env", "env?": "maybe"}.get(kind, "other")
        else:
            source = "other"
        if source == "other":
            continue
        if _is_guarded(node, parents):
            states.append(("guarded", node.lineno, ""))
        elif source == "maybe":
            states.append(("untraced", node.lineno,
                           "the value the conversion reads cannot be traced back to the attempt"))
        elif not _is_reached(node, parents, called):
            states.append(("unreached", node.lineno,
                           "the conversion sits in a function the module never calls"))
        else:
            states.append(("int", node.lineno, ""))
    for state in ("int", "untraced", "guarded", "unreached"):
        for name, line, reason in states:
            if name == state:
                return {"state": state, "line": line, "reason": reason}
    return {"state": "none", "line": None, "reason": ""}


def consumer_comparison(path: Path | None) -> dict:
    """What the consumer's own bytes say: where it compares, for which role."""
    facts = {"path": str(path) if path else None, "line": None, "role": None,
             "attempt_state": "none", "attempt_line": None, "attempt_reason": ""}
    if path is None:
        return facts
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return facts
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return facts
    facts["line"] = comparison_line(tree)
    facts["role"] = consumer_role(tree)
    rule = attempt_rule(tree)
    facts["attempt_state"] = rule["state"]
    facts["attempt_line"] = rule["line"]
    facts["attempt_reason"] = rule["reason"]
    return facts


def _interpreter(text: str) -> str:
    """The shell that runs a launcher, from the launcher's own shebang.

    A shebang is written with or without a space after the ``#!``, and the
    interpreter is the first word after it either way.
    """
    lines = text.splitlines()
    first = lines[0] if lines else ""
    if first.startswith("#!"):
        words = first[2:].strip().split()
        if words:
            return words[0]
    return "/bin/sh"


def shell_ends_at_a_rejected_export(interpreter: str) -> bool:
    """Whether the launcher's own shell ends the script at a rejected export.

    The shell is asked, not assumed: the probe exports a name with a word the
    shell does not accept and prints a marker after it. A shell that reports
    the error and stops never prints the marker. A shell that cannot be run
    answers nothing, and the reading stays unknown.
    """
    try:
        result = subprocess.run([interpreter, "-c", SHELL_PROBE],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return "REACHED" not in result.stdout


def _is_wake_number(value: str) -> bool:
    """Whether a value is a wake number the plant could owe.

    The plant's wakes are ASCII decimal numbers above zero. ``str.isdigit``
    accepts other digits (``²``) and ``int()`` then raises, so the digits are
    matched explicitly and the number is read without raising.
    """
    return bool(WAKE_RE.fullmatch(value)) and int(value) > 0


def launcher_findings(role_name: str, path: Path, text: str, opt_in: dict, consumer: dict):
    """The findings and unknown readings that need only the launcher's bytes.

    These are decided before the plant's state is read, because a finding that
    needs only the launcher must not be lost because the plant's state could
    not be read.
    """
    findings = []
    unknowns = []
    interpreter = _interpreter(text)
    for name, reading in sorted(opt_in.items()):
        if reading["kind"] == "rejected":
            findings.append({
                "role": role_name, "verdict": "MISMATCH",
                "reason": "the shell rejects the launcher's export of %s (%s), so the opt-in "
                          "the owner wrote does not reach the consumer"
                          % (name, reading["reason"]),
                "evidence": [str(path)],
            })
        elif reading["kind"] == "errored":
            if shell_ends_at_a_rejected_export(interpreter):
                findings.append({
                    "role": role_name, "verdict": "MISMATCH",
                    "reason": "the shell reports an error for the launcher's export of %s (%s) "
                              "and ends the launcher at that line, so the resident never starts"
                              % (name, reading["reason"]),
                    "evidence": [str(path)],
                })
            else:
                unknowns.append("%s: the shell reports an error for the export of %s (%s); the "
                                "value reaches the consumer only if the shell continues"
                                % (role_name, name, reading["reason"]))
        elif reading["kind"] == "unknown":
            unknowns.append("%s: %s" % (role_name, reading["reason"]))
    attempt = opt_in.get(RECOVERY_ATTEMPT)
    wake = opt_in.get(RECOVERY_WAKE)
    if attempt and attempt["kind"] == "value" and wake is None:
        findings.append({
            "role": role_name, "verdict": "MISMATCH",
            "reason": "the launcher names a recovery attempt (%s=%s) without naming the "
                      "wake it recovers" % (RECOVERY_ATTEMPT, attempt["value"]),
            "evidence": [str(path)],
        })
    if wake and wake["kind"] == "value" and not _is_wake_number(wake["value"]):
        findings.append({
            "role": role_name, "verdict": "MISMATCH",
            "reason": "the opt-in names a wake that is not a wake number (%r)" % wake["value"],
            "evidence": [str(path)],
        })
    if attempt and attempt["kind"] == "value" and consumer["attempt_state"] == "int":
        try:
            int(attempt["value"])
        except ValueError:
            findings.append({
                "role": role_name, "verdict": "MISMATCH",
                "reason": "the consumer reads the attempt with int(); %r is not a number it "
                          "can read" % attempt["value"],
                "evidence": [str(path), consumer["path"] and str(consumer["path"])],
            })
    if attempt and attempt["kind"] == "value" and consumer["attempt_state"] == "untraced":
        unknowns.append("%s: the consumer converts the attempt with int() (line %s) but %s, "
                        "so whether %r is a number it can read is not decidable here"
                        % (role_name, consumer["attempt_line"], consumer["attempt_reason"],
                           attempt["value"]))
    return findings, unknowns


def launcher_mismatch(role_name: str, path: Path, opt_in: dict, consumer: dict, states: dict):
    """The mismatch between the opt-in and the wake the plant still owes.

    A launcher whose role the plant does not know is not an agreement: there is
    no pending wake to compare the opt-in with, so the reading is unknown.
    """
    findings = []
    unknowns = []
    wake = opt_in.get(RECOVERY_WAKE)
    if not (wake and wake["kind"] == "value"):
        return findings, unknowns
    named = wake["value"]
    if not _is_wake_number(named):
        # The wake is not a wake number; that finding is already reported
        # from the launcher's own bytes.
        return findings, unknowns
    if role_name not in states:
        unknowns.append("%s carries the opt-in, but the plant knows no role %s to compare it "
                        "with" % (role_name, role_name))
        return findings, unknowns
    pending = (states.get(role_name) or {}).get("pending")
    if pending is None:
        findings.append({
            "role": role_name, "verdict": "MISMATCH",
            "reason": "the opt-in names wake %s; the plant owes %s no wake" % (named, role_name),
            "evidence": [str(path)],
        })
        return findings, unknowns
    if named != str(pending):
        findings.append({
            "role": role_name, "verdict": "MISMATCH",
            "reason": "the opt-in names wake %s; the plant owes %s wake %s"
                      % (named, role_name, pending),
            "evidence": [str(path), consumer["path"] and str(consumer["path"])],
        })
    return findings, unknowns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--role", default=None, help="only this role's launcher")
    args = parser.parse_args()
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    findings = []
    unknowns = []

    # The launchers' own bytes first: a finding that needs only the launcher
    # must not be lost because the plant's state could not be read.
    launchers = []
    for path in launcher_files(home):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            # A launcher that cannot be read may hide an opt-in, so the
            # reading for that role is unknown, not clean.
            unknowns.append("%s: the launcher is unreadable (%s)" % (path.name, type(exc).__name__))
            continue
        opt_in = read_opt_in(text)
        if not opt_in:
            continue
        consumer = consumer_comparison(runner_path(text))
        # The role the consumer serves decides whose wake the opt-in is
        # compared with; the launcher's file name is only a fallback.
        role = consumer["role"] or path.name
        launchers.append((role, path, text, opt_in, consumer))
        found, unknown = launcher_findings(role, path, text, opt_in, consumer)
        findings.extend(found)
        unknowns.extend(unknown)

    # The export is written to a temporary directory. It must not be inside the
    # site home, or the check would write where it promised to only read, so
    # the place is chosen before anything is created.
    root = Path(tempfile.gettempdir()).resolve()
    if root == home or home in root.parents:
        print("UNKNOWN — the temporary directory is inside the site home")
        for finding in findings:
            print("finding   : %s — %s" % (finding["role"], finding["verdict"]))
            print("            %s" % finding["reason"])
        for unknown in unknowns:
            print("UNKNOWN   :", unknown)
        if findings:
            print("verdict   : %d finding(s) — the opt-in does not name the wake the plant "
                  "will deliver" % len(findings))
            return 2
        return 3
    workdir = Path(tempfile.mkdtemp(prefix="native-recovery-optin-", dir=str(root)))
    try:
        reference, commit = export_head(repo, workdir)
        state = read_state(reference, home, workdir)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        for finding in findings:
            print("finding   : %s — %s" % (finding["role"], finding["verdict"]))
            print("            %s" % finding["reason"])
        for unknown in unknowns:
            print("UNKNOWN   :", unknown)
        if findings:
            print("verdict   : %d finding(s) — the opt-in does not name the wake the plant "
                  "will deliver" % len(findings))
            return 2
        print("verdict   : UNKNOWN — the plant's state could not be read")
        return 3
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    roles = state.get("roles") or []
    states = state.get("states") or {}
    print("home      :", home)
    print("reference :", commit, "exported to a temporary tree")
    print("roles     : %d read by the reviewed tree" % len(roles))
    if args.role and args.role not in roles:
        print("UNKNOWN   : %s is not a role the reviewed tree knows" % args.role)
        unknowns.append("%s is not a role the reviewed tree knows" % args.role)
    for role, path, text, opt_in, consumer in launchers:
        if args.role and role != args.role:
            continue
        found, unknown = launcher_mismatch(role, path, opt_in, consumer, states)
        findings.extend(found)
        unknowns.extend(unknown)
        wake = opt_in.get(RECOVERY_WAKE)
        attempt = opt_in.get(RECOVERY_ATTEMPT)
        role_state = states.get(role) or {}
        pending = role_state.get("pending")
        print("launcher  :", path)
        print("role      :", role)
        print("opt-in    : wake=%s attempt=%s" % (
            wake["value"] if wake and wake["kind"] == "value" else None,
            attempt["value"] if attempt and attempt["kind"] == "value" else None))
        print("pending   :", pending)
        print("history   : last yield=%s last clear=%s"
              % (role_state.get("last_yield"), role_state.get("last_clear")))
        if consumer["path"]:
            if consumer["line"]:
                print("consumer  : %s (compares the opt-in with the pending wake at line %d)"
                      % (consumer["path"], consumer["line"]))
            else:
                print("consumer  : %s (no comparison of the opt-in with a wake found)"
                      % consumer["path"])
            if consumer["attempt_state"] == "int":
                print("attempt   : the consumer reads the attempt with int() at line %d"
                      % consumer["attempt_line"])
            elif consumer["attempt_state"] == "guarded":
                print("attempt   : the consumer reads the attempt with int() at line %d and "
                      "survives the failure" % consumer["attempt_line"])
            elif consumer["attempt_state"] == "unreached":
                print("attempt   : the consumer reads the attempt with int() at line %d, but %s"
                      % (consumer["attempt_line"], consumer["attempt_reason"]))
            elif consumer["attempt_state"] == "untraced":
                print("attempt   : the consumer reads the attempt with int() at line %d, but %s"
                      % (consumer["attempt_line"], consumer["attempt_reason"]))
    for finding in findings:
        print("finding   : %s — %s" % (finding["role"], finding["verdict"]))
        print("            %s" % finding["reason"])
        for path in finding["evidence"]:
            if path:
                print("            evidence: %s" % path)
    for unknown in unknowns:
        print("UNKNOWN   :", unknown)
    if findings:
        print("verdict   : %d finding(s) — the opt-in does not name the wake the plant "
              "will deliver" % len(findings))
        return 2
    if unknowns:
        print("verdict   : UNKNOWN — %d role(s) could not be read" % len(unknowns))
        return 3
    print("verdict   : AGREE — every role's opt-in names the wake the plant will deliver")
    return 0


if __name__ == "__main__":
    sys.exit(main())
