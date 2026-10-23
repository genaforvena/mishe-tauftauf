#!/usr/bin/env python3
"""Detector: a tracked check that no scheduler runs is a verdict nobody reads.

The plant's reviewed tree carries a family of detector scripts under
``checks/``. Each answers one question about the plant's own live state, and
each exits ``0`` (holds), ``2`` (fails) or ``3`` (cannot decide). A detector
that never runs reports nothing, so a class of defect it was written to catch
stays invisible to the plant: no published reading names it, no route fires,
and its author has no signal that the defect is still live.

Three kinds of scheduler make a check observed, and a check that none of
them runs is **unobserved**: the plant publishes nothing about it and routes
nothing to it.

1. The site's observer-check runner (``SITE/checks-runner/observer-checks.py``),
   driven by the unit ``mishe-self-development-current-observer-checks.service``.
   The runner reads two literal tables from its own source: ``INVENTORY`` (the
   checks it runs) and ``NOT_SCHEDULED`` (the checks it deliberately does not
   run, each with the measured reason it is excluded).
2. A systemd user unit that starts the check script. The unit runs it on a
   timer and its alert routes the verdict to health.
3. A CI workflow job that runs the check. The plant's CI resident reads the
   run's status and routes a failure to the role that owns it.

Reading only the runner's tables — the first kind — is what this check did
when it was first delivered, and it was wrong: it called
``exit-node-egress.py`` unobserved while a systemd timer ran it every two
minutes, and ``parsing-legacy-freeze.py`` unobserved while CI ran it on every
push. Those were false verdicts about the plant. The three schedulers are
therefore all read, and a check any of them runs is reported as observed
elsewhere rather than as unobserved.

The runner records its two tables in its published state
(``SITE/checks/observer-checks.json``), so the exclusion set is visible rather
than silent. That visibility is what this check reads: it names the checks
that are neither run nor declared, and for each one it reports whether the
check would currently fail — so a reader can tell an unscheduled check that is
quiet from one that is failing right now, unheard.

This is not hypothetical. Measured on this plant (2026-10-23):

* the reviewed tree carries **26** check scripts;
* the runner schedules **8** and declares **2** as deliberately unscheduled;
* **2** more are scheduled elsewhere — ``exit-node-egress.py`` by a systemd
  timer, ``parsing-legacy-freeze.py`` by CI;
* **14** are in no table and run by nothing;
* of those 14, **six** exited ``2`` at the time of measurement —
  ``coverage-gap-remedy``, ``coverage-remedy-clause-roots`` (the pinned bytes
  read two coverage rows broken), ``coverage-window-expiry``,
  ``ledger-band-second-reader``, ``unit-import-observability`` (an off-pin
  unit is unobserved) and ``writable-bound-evidence`` — while the plant's
  published problem set (``SITE/checks/observer-checks.json``) names only
  ``evidence-binding-verdict-consistency`` and ``observer-verdict-agreement``.

The count of failing unobserved checks moves with the plant, so the verdict
names them at the time it runs rather than carrying a number. So six live
failures were invisible to the plant's monitoring while the plant published a
problem set that did not mention them.

The runner's own bytes are a second, independent finding: the table that
decides which verdicts the plant reads is carried by no reviewed commit, so
it can change with no diff, no commit and no review.

The check reads, read-only:

* the reviewed repository's ``checks/*.py`` (tracked files only);
* the runner's own source, for its two tables — read as **text**, not as
  executed code, so this check never imports or runs the runner;
* the runner's published state file, for the tables as the runner last
  recorded them, so a disagreement between the source and the published state
  is itself reported;
* the plant's systemd user units, for the check scripts their command lines
  name, read from systemd itself rather than from a file in the site; and
* the reviewed repository's CI workflows, for the check scripts their
  commands name, read as text rather than by executing them.

It imports nothing from the plant package and runs no plant program. It does
not decide whether a failing unscheduled check is *important* — that is a
judgement for the plant, and the check's job is to say which checks are
unheard and which of those are currently failing.

Exit status:
  0  every tracked check is scheduled, declared-not-scheduled, or scheduled
     elsewhere, or every unscheduled check that currently fails is named and
     its failure is reported
  2  a tracked check is neither scheduled nor declared-not-scheduled, and it
     currently fails: the plant is not reading a verdict it is already
     producing; or the runner's own bytes are carried by no reviewed commit,
     so the table that decides what the plant reads is not reviewable
  3  the question cannot be decided: the repository, the runner source or the
     published state cannot be read, the runner's tables cannot be decoded, or
     the plant's systemd units cannot be read — a scheduler this check cannot
     see is a scheduler whose checks it must not call unobserved
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

#: The reviewed tree's detector scripts.
CHECKS_DIR = "checks"

#: The runner's own source, relative to the site home.
RUNNER_SOURCE = "checks-runner/observer-checks.py"

#: The runner's published state, relative to the site home.
RUNNER_STATE = "checks/observer-checks.json"

#: The runner's table of checks it runs.
INVENTORY_NAME = "INVENTORY"

#: The runner's table of checks it deliberately does not run.
NOT_SCHEDULED_NAME = "NOT_SCHEDULED"

#: This check's own file name. Once the tree carries it, it is itself an
#: unobserved check, and running it inside itself would be recursion.
SELF_NAME = "check-scheduler-coverage.py"

#: The reviewed tree's CI workflows. A check a CI job runs is read by the
#: plant's CI resident, so it is observed even though no local runner runs it.
CI_WORKFLOW_DIR = ".github/workflows"

#: The systemd unit properties that can start a check script.
UNIT_EXEC_PROPERTIES = ("ExecStart", "ExecStartPre", "ExecStartPost")

#: The command used to ask systemd about the plant's units. It is a variable
#: so that a test can ask a fixed question of a known plant; in the plant
#: itself it is systemd, and the answer is the one the running plant has.
SYSTEMCTL_COMMAND = "MISHE_CHECK_SYSTEMCTL"

#: The socket that stands for this machine's systemd user manager. It is a
#: variable for the same reason: a machine with no user manager cannot run a
#: systemd unit, and a test must be able to say so without changing the
#: machine it runs on.
SYSTEMD_SOCKET = "MISHE_CHECK_SYSTEMD_SOCKET"


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Undecidable("%s cannot be read: %s" % (path, exc)) from exc


def tracked_checks(repo: Path) -> list[str]:
    """The names of the detector scripts the reviewed tree tracks.

    Only tracked files count: an untracked script in a working tree is
    somebody's in-flight work, not a verdict the plant is expected to read.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD", CHECKS_DIR + "/"],
        capture_output=True, text=True)
    if result.returncode:
        raise Undecidable("cannot list tracked checks: " + result.stderr.strip())
    names = []
    for line in result.stdout.splitlines():
        name = line.rsplit("/", 1)[-1]
        if name.endswith(".py") and not name.startswith("_"):
            names.append(name)
    if not names:
        raise Undecidable("the reviewed tree tracks no check under " + CHECKS_DIR + "/")
    return sorted(set(names))


def _table_names(source: str, table: str) -> list[str]:
    """The check names one literal table of the runner's source declares.

    The table is read as source text and parsed, not imported and not
    executed: a check that ran the runner to learn its schedule would be
    deciding the question with the thing under question.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise Undecidable("runner source is unparseable: %s" % exc) from exc
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = node.targets
        if not targets or not isinstance(targets[0], ast.Name):
            continue
        if targets[0].id != table:
            continue
        names = []
        for element in node.value.elts:
            if not isinstance(element, ast.Tuple) or not element.elts:
                continue
            first = element.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.append(_check_name(first.value))
        return names
    raise Undecidable("runner source declares no " + table)


def runner_tables(source: str) -> tuple[set[str], set[str]]:
    """The checks the runner runs, and the checks it declares it does not run."""
    scheduled = set(_table_names(source, INVENTORY_NAME))
    declared = set(_table_names(source, NOT_SCHEDULED_NAME))
    if not scheduled:
        raise Undecidable("runner schedules no check")
    return scheduled, declared


def _check_name(identifier: str) -> str:
    """The check a published entry id names.

    The runner publishes one entry per (check, target) pair, so an id may
    carry a target suffix (``observer-view-agreement:body-research``) and a
    check name may be published with or without its ``.py`` suffix. Both are
    naming conventions of the id, not of the check, so the id is reduced to
    the check it names before it is compared with a script name.
    """
    name = identifier.split(":", 1)[0].strip()
    return name if name.endswith(".py") else name + ".py"


def published_tables(state: dict) -> tuple[set[str], set[str]]:
    """The tables as the runner last recorded them in its published state.

    The published state names the scheduled entries by id and the excluded
    ones by id. A runner whose source and published state disagree is a
    runner the plant cannot say what it reads, so the disagreement is
    reported rather than resolved here.
    """
    entries = state.get("entries")
    skipped = state.get("not_scheduled")
    if not isinstance(entries, list) or not isinstance(skipped, list):
        raise Undecidable("published state names no entries and excluded checks")
    scheduled = set()
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            scheduled.add(_check_name(entry["id"]))
    declared = set()
    for item in skipped:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            declared.add(_check_name(item["id"]))
    return scheduled, declared


def _systemctl(arguments: list[str]) -> subprocess.CompletedProcess:
    """Ask the plant's systemd about its units.

    The command is read from the environment so that a test can substitute a
    known plant. A substituted command that cannot be run is a broken test
    fixture, and is reported as such rather than as an empty plant.
    """
    command = os.environ.get(SYSTEMCTL_COMMAND, "systemctl")
    parts = command.split() if command else ["systemctl"]
    return subprocess.run([*parts, "--user", *arguments], capture_output=True, text=True)


def systemd_scheduled(repo: Path) -> tuple[dict[str, list[str]], str]:
    """The checks a systemd unit runs, and the units that run them.

    A systemd unit is a scheduler: it names the check script it starts, and
    the plant reads that unit's verdict. A check that a unit runs is
    observed even though the observer-check runner does not run it, so
    reporting it as unobserved would be a false verdict about the plant.

    The units are read from systemd itself, not from a file in the site, so
    the answer is the one the running plant has. Units that cannot be read
    leave the question undecidable rather than silently empty: a scheduler
    this check cannot see is a scheduler whose checks it must not call
    unobserved.

    A machine with no systemd user manager at all is not that case: there
    is no unit that could run a check, which is a fact about the machine
    and not a gap in the evidence. That is reported as its own note, so a
    reader knows the systemd answer was empty rather than unread.
    """
    result = _systemctl(["list-unit-files", "--no-legend", "--no-pager"])
    if result.returncode:
        if not _systemd_user_manager_present():
            return {}, "no systemd user manager on this machine"
        # A manager exists but cannot be reached: the answer is missing, and
        # a check this cannot see must not be called unobserved.
        raise Undecidable("cannot list systemd user units: " + result.stderr.strip())
    units = []
    for line in result.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        # A template unit ("mesh-pane-consume@.service") is not a unit name
        # systemd can show; only its instantiated names are readable.
        if name.endswith(".service") and "@" not in name:
            units.append(name)
    if not units:
        return {}, "systemd lists no user service units"
    shown = _systemctl(["show", *units,
                        "--property=Id," + ",".join(UNIT_EXEC_PROPERTIES), "--no-pager"])
    if shown.returncode:
        raise Undecidable("cannot read systemd unit properties: " + shown.stderr.strip())
    scheduled: dict[str, list[str]] = {}
    for unit, exec_lines in _unit_exec_lines(shown.stdout).items():
        if unit not in units:
            continue
        for check in _checks_named_in_exec(exec_lines, repo):
            scheduled.setdefault(check, []).append(unit)
    return scheduled, "read from systemd"


def _systemd_user_manager_present() -> bool:
    """Whether this machine has a systemd user manager to ask.

    The manager's socket is the evidence. A machine without one cannot run
    a systemd unit, so no check is systemd-scheduled there and the empty
    answer is complete rather than missing.
    """
    override = os.environ.get(SYSTEMD_SOCKET)
    if override is not None:
        return Path(override).exists()
    candidates = []
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidates.append(Path(runtime) / "systemd" / "private")
    try:
        candidates.append(Path("/run/user") / str(os.getuid()) / "systemd" / "private")
    except (AttributeError, OSError):
        pass
    return any(candidate.exists() for candidate in candidates)


def _unit_exec_lines(show_output: str) -> dict[str, list[str]]:
    """Each unit's command lines, read from systemd's own output.

    ``systemctl show`` prints one block per unit: the unit's command lines
    first and its ``Id=`` line last, with a blank line between blocks. The
    ``Id=`` line is what names the block, so it is read together with the
    command lines rather than line by line.
    """
    blocks: dict[str, list[str]] = {}
    current: list[str] = []
    for line in show_output.splitlines():
        if not line.strip():
            if current:
                for name in current:
                    if name.startswith("Id="):
                        blocks.setdefault(name[len("Id="):].strip(), []).extend(
                            line for line in current if not line.startswith("Id="))
            current = []
            continue
        current.append(line)
    if current:
        for name in current:
            if name.startswith("Id="):
                blocks.setdefault(name[len("Id="):].strip(), []).extend(
                    line for line in current if not line.startswith("Id="))
    return blocks


def _checks_named_in_exec(exec_lines: list[str], repo: Path) -> list[str]:
    """The check scripts a unit's command lines name.

    A unit's command line names a check script by path. Only a path under
    the reviewed tree's own ``checks/`` directory counts, so a unit that
    runs another site's copy of a script does not make the reviewed tree's
    copy observed: the two are different bytes, and the plant's verdict is
    about the one it runs.
    """
    names = []
    for line in exec_lines:
        if not line.startswith(("ExecStart", "ExecStartPre", "ExecStartPost")):
            continue
        for token in line.split(";"):
            token = token.strip()
            if not token.startswith("argv[]="):
                continue
            argv = token[len("argv[]="):].split()
            for argument in argv:
                candidate = Path(argument)
                if candidate.parent != repo / CHECKS_DIR:
                    continue
                if candidate.suffix != ".py":
                    continue
                names.append(candidate.name)
    return sorted(set(names))


def ci_scheduled(repo: Path) -> dict[str, list[str]]:
    """The checks a CI workflow runs, and the workflows that run them.

    A CI job that runs a check publishes the check's exit status, and the
    plant's CI resident reads that status and routes a failure to the role
    that owns it. A check a workflow runs is therefore observed, even though
    no local runner runs it.

    The workflows are read as text, by the literal command they contain, so
    this check neither imports a YAML library nor executes a workflow.
    """
    directory = repo / CI_WORKFLOW_DIR
    if not directory.is_dir():
        return {}
    scheduled: dict[str, list[str]] = {}
    for workflow in sorted(directory.glob("*.y*ml")):
        try:
            text = workflow.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in sorted(set(re.findall(r"checks/([A-Za-z0-9._-]+\.py)", text))):
            scheduled.setdefault(name, []).append(workflow.name)
    return scheduled


def runner_provenance(repo: Path, home: Path) -> tuple[str, str]:
    """Whether the runner's own bytes are carried by a reviewed commit.

    The runner's tables decide which verdicts the plant reads, so the bytes
    that carry those tables must be reviewable: a reader must be able to see
    a diff that changes what the plant reads, and a revert must be a commit.

    The runner lives under the site home, which is gitignored, so its bytes
    are not in the tree by default. This returns the state and the detail:

    * ``"reviewed"`` — some commit in the repository carries exactly these
      bytes, so the schedule is reviewable and revertible;
    * ``"unreviewable"`` — no commit carries them. The table can change with
      no diff, no commit and no review, and ``git status`` reports no change
      when it does, so nothing observes the change.
    * ``"untracked"`` — the repository itself cannot be read, which is
      undecidable rather than a verdict.
    """
    source = home / RUNNER_SOURCE
    try:
        blob = subprocess.run(["git", "-C", str(repo), "hash-object", str(source)],
                              capture_output=True, text=True)
    except OSError as exc:
        raise Undecidable("cannot hash the runner source: " + str(exc)) from exc
    if blob.returncode:
        raise Undecidable("cannot hash the runner source: " + blob.stderr.strip())
    object_id = blob.stdout.strip()
    if not object_id:
        raise Undecidable("git returned no object id for the runner source")
    listing = subprocess.run(["git", "-C", str(repo), "rev-list", "--objects", "--all"],
                             capture_output=True, text=True)
    if listing.returncode:
        raise Undecidable("cannot list repository objects: " + listing.stderr.strip())
    for line in listing.stdout.splitlines():
        if line.split(" ", 1)[0] == object_id:
            return "reviewed", object_id
    return "unreviewable", object_id


def run_check(repo: Path, name: str, home: Path) -> tuple[int, str]:
    """Run one tracked check against the live site and return its exit and tail.

    The check runs from the reviewed tree's bytes, never from the working
    tree, so an uncommitted edit cannot change what this measurement reads.

    A check that cannot accept the arguments it is given exits ``2`` from
    argparse, which is the same code a failing verdict uses. The two are told
    apart by the message: argparse's usage error names the arguments it
    rejected, and a check that reports a verdict does not. Without that
    distinction a check that is merely *not runnable* would be counted as a
    failing verdict, and the verdict this check publishes would be wrong.
    """
    blob = subprocess.run(["git", "-C", str(repo), "show", "HEAD:" + CHECKS_DIR + "/" + name],
                          capture_output=True)
    if blob.returncode:
        raise Undecidable("cannot read HEAD:" + CHECKS_DIR + "/" + name)
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / name
        script.write_bytes(blob.stdout)
        result = subprocess.run([sys.executable, str(script), "--home", str(home)],
                                capture_output=True, text=True, timeout=300)
    output = (result.stdout + result.stderr).strip()
    tail = output.splitlines()
    verdict = tail[-1] if tail else ""
    if result.returncode == 2 and _is_usage_error(output):
        return 2, "NOT RUNNABLE — " + verdict[:160]
    return result.returncode, verdict


def _is_usage_error(output: str) -> bool:
    """Whether a failing check's output is argparse rejecting its arguments.

    argparse writes ``usage:`` and ``error:`` to stderr and exits ``2``. A
    check that reports a verdict writes the verdict, and the verdict names the
    defect it found rather than the arguments it was given.
    """
    return "usage:" in output and "error:" in output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--list", action="store_true",
                        help="list every unobserved check and whether it currently fails")
    args = parser.parse_args()
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    try:
        if not (repo / ".git").exists():
            raise Undecidable("no repository at " + str(repo))
        tracked = tracked_checks(repo)
        source = _read_bytes(home / RUNNER_SOURCE).decode("utf-8", errors="strict")
        scheduled, declared = runner_tables(source)
        state = json.loads(_read_bytes(home / RUNNER_STATE).decode("utf-8"))
        if not isinstance(state, dict):
            raise Undecidable("published state is not a JSON object")
        published_scheduled, published_declared = published_tables(state)
        # A scheduler other than the observer-check runner also makes a check
        # observed. A systemd unit that runs the check, and a CI job that runs
        # it, both publish its verdict to the plant. Reading only the
        # runner's tables would call those checks unobserved, which is a
        # false verdict about the plant.
        units, systemd_note = systemd_scheduled(repo)
        workflows = ci_scheduled(repo)
        provenance, object_id = runner_provenance(repo, home)
    except (OSError, UnicodeError, ValueError) as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3

    elsewhere = set(units) | set(workflows)
    observed_elsewhere = sorted(set(tracked) & elsewhere)
    unobserved = sorted(set(tracked) - scheduled - declared - elsewhere)
    # The runner's published state must agree with its own source. If it does
    # not, the plant cannot say which checks it reads.
    disagreement = sorted((published_scheduled | published_declared) -
                          (scheduled | declared)) + \
        sorted((scheduled | declared) - (published_scheduled | published_declared))

    if args.list:
        print("tracked checks : %d" % len(tracked))
        print("scheduled      : %d" % len(scheduled))
        print("declared skip  : %d" % len(declared))
        print("other schedule : %d" % len(observed_elsewhere))
        print("unobserved     : %d" % len(unobserved))
        print("runner bytes   : %s (%s)" % (provenance, object_id))
        print("systemd units  : %s" % systemd_note)
        for name in observed_elsewhere:
            where = []
            if name in units:
                where.append("systemd:" + ",".join(units[name]))
            if name in workflows:
                where.append("ci:" + ",".join(workflows[name]))
            print("  SCHEDULED-ELSEWHERE %s (%s)" % (name, "; ".join(where)))
        for name in unobserved:
            print("  UNOBSERVED %s" % name)
        if disagreement:
            print("source/published disagreement: %s" % ", ".join(disagreement))
        return 0

    for name in observed_elsewhere:
        where = []
        if name in units:
            where.append("systemd:" + ",".join(units[name]))
        if name in workflows:
            where.append("ci:" + ",".join(workflows[name]))
        print("SCHEDULED-ELSEWHERE %s (%s)" % (name, "; ".join(where)))

    failing = []
    for name in unobserved:
        if name == SELF_NAME:
            # This check is itself an unobserved check once the tree carries
            # it, and running it here would run it inside itself. Its own
            # verdict is this one, so it is reported and not re-run.
            print("unobserved     %s — this check; its verdict is this one" % name)
            continue
        code, tail = run_check(repo, name, home)
        if tail.startswith("NOT RUNNABLE"):
            # The check could not be run with the arguments it is given, so it
            # has no verdict to be unheard. It is still unobserved, and that
            # is a fact about the plant, not a failing verdict.
            print("unobserved     %s — %s" % (name, tail[:160]))
        elif code == 2:
            failing.append((name, tail))
            print("FAILING-UNOBSERVED %s — %s" % (name, tail[:160]))
        else:
            print("unobserved     %s — exit=%d %s" % (name, code, tail[:100]))

    if disagreement:
        print("VERDICT: the runner's source and its published state disagree about which "
              "checks it reads: %s" % ", ".join(disagreement))
    if provenance == "unreviewable":
        # The table that decides which verdicts the plant reads is carried by
        # no commit, so it can change with no diff, no commit and no review,
        # and nothing observes the change. That is a defect in the plant's
        # reading: a schedule nobody can review cannot be the one the plant
        # relies on.
        print("VERDICT: the runner's own bytes (%s) are carried by no reviewed commit — "
              "the table that decides which verdicts the plant reads can change with no "
              "diff, no commit and no review" % object_id)
    if failing:
        print("VERDICT: %d unobserved check(s) currently fail — the plant is producing "
              "verdicts nobody reads" % len(failing))
    if not (disagreement or provenance == "unreviewable" or failing):
        print("VERDICT: every tracked check is scheduled or declared-not-scheduled")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
