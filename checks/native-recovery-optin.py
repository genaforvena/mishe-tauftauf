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
            attempt, names a text that is not a wake number, or the plant owes
            the role no wake.

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
  way the shell reads an assignment.
- The consumer's rule is cited, not decided: the check names the line where
  the consumer compares the opt-in with the pending wake, and it recognises
  that line only when the operands are exactly the environment value and a
  stringified wake.

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
OPT_IN_RE = re.compile(
    r"^[ \t]*export[ \t]+(%s|%s)[ \t]*=[ \t]*(.*)$" % (RECOVERY_WAKE, RECOVERY_ATTEMPT))
EXEC_RE = re.compile(r"^[ \t]*exec[ \t]+(.*)$", re.MULTILINE)
# A wake number, as the plant's own numbers are written: decimal digits.
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


def _shell_value(text: str) -> str:
    """The value a shell assignment exports, with a trailing comment removed.

    A shell comment starts at an unquoted ``#`` that begins a word, so
    ``75277 # why`` exports ``75277`` and ``75277#why`` exports the whole
    text. A quoted value is taken as written.
    """
    value = text.strip(" \t")
    if not value:
        return ""
    if value[0] in "\"'":
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    cut = value.find(" #")
    if cut != -1:
        value = value[:cut]
    return value.strip(" \t")


def read_opt_in(text: str) -> dict:
    """The recovery opt-in a launcher carries, as the launcher states it.

    A launcher is a shell script the integration owner edits by hand, so the
    opt-in is read from its own text: the ``export`` lines it carries. The
    last export of a name wins, as it does in the shell.
    """
    found = {}
    for line in text.splitlines():
        match = OPT_IN_RE.match(line)
        if not match:
            continue
        found[match.group(1)] = _shell_value(match.group(2))
    return found


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


def _environment_value(node) -> bool:
    """Whether the node reads the recovery opt-in out of the environment."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ" and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant) and node.args[0].value == RECOVERY_WAKE)


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


def consumer_comparison(path: Path | None) -> dict:
    """Where the consumer compares the opt-in, read from its own bytes."""
    facts = {"path": str(path) if path else None, "line": None}
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
    return facts


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
        launchers.append((path.name, path, text, opt_in))
        if opt_in.get(RECOVERY_ATTEMPT) is not None and opt_in.get(RECOVERY_WAKE) is None:
            findings.append({
                "role": path.name, "verdict": "MISMATCH",
                "reason": "the launcher names a recovery attempt (%s=%s) without naming the "
                          "wake it recovers"
                          % (RECOVERY_ATTEMPT, opt_in.get(RECOVERY_ATTEMPT)),
                "evidence": [str(path)],
            })

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
    for role, path, text, opt_in in launchers:
        if args.role and role != args.role:
            continue
        if role not in states:
            print("note      : %s is not a role the reviewed tree knows; the plant owes it "
                  "no wake" % role)
            continue
        named = opt_in.get(RECOVERY_WAKE)
        number = opt_in.get(RECOVERY_ATTEMPT)
        role_state = states.get(role) or {}
        pending = role_state.get("pending")
        consumer = consumer_comparison(runner_path(text))
        print("launcher  :", path)
        print("role      :", role)
        print("opt-in    : wake=%s attempt=%s" % (named, number))
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
        if named is None:
            # The launcher names an attempt without naming a wake: the finding
            # is already recorded, from the launcher's own bytes.
            continue
        if not WAKE_RE.fullmatch(named) or int(named) <= 0:
            findings.append({
                "role": role, "verdict": "MISMATCH",
                "reason": "the opt-in names a wake that is not a wake number (%r)" % named,
                "evidence": [str(path)],
            })
            continue
        if pending is None:
            findings.append({
                "role": role, "verdict": "MISMATCH",
                "reason": "the opt-in names wake %s; the plant owes %s no wake" % (named, role),
                "evidence": [str(path)],
            })
            continue
        if named != str(pending):
            findings.append({
                "role": role, "verdict": "MISMATCH",
                "reason": "the opt-in names wake %s; the plant owes %s wake %s"
                          % (named, role, pending),
                "evidence": [str(path), consumer["path"] and str(consumer["path"])],
            })
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
