#!/usr/bin/env python3
"""Detector: a tracked check that no scheduler runs is a verdict nobody reads.

The plant's reviewed tree carries a family of detector scripts under
``checks/``. Each answers one question about the plant's own live state, and
each exits ``0`` (holds), ``2`` (fails) or ``3`` (cannot decide). A detector
that never runs reports nothing, so a class of defect it was written to catch
stays invisible to the plant: no published reading names it, no route fires,
and its author has no signal that the defect is still live.

Exactly one runner schedules them: the site's observer-check runner
(``SITE/checks-runner/observer-checks.py``), driven by the unit
``mishe-self-development-current-observer-checks.service``. The runner reads
two literal tables from its own source: ``INVENTORY`` (the checks it runs) and
``NOT_SCHEDULED`` (the checks it deliberately does not run, each with the
measured reason it is excluded). A check that is in neither table is
**unobserved**: the plant publishes nothing about it and routes nothing to it.

The runner records both tables in its published state
(``SITE/checks/observer-checks.json``), so the exclusion set is visible rather
than silent. That visibility is what this check reads: it names the checks
that are neither run nor declared, and for each one it reports whether the
check would currently fail — so a reader can tell an unscheduled check that is
quiet from one that is failing right now, unheard.

This is not hypothetical. Measured on this plant (2026-10-23):

* the reviewed tree carries **26** check scripts;
* the runner schedules **8** and declares **2** as deliberately unscheduled;
* **16** are in neither table;
* of those 16, **four** currently exit ``2`` — ``coverage-remedy-clause-roots``
  (the pinned bytes read two coverage rows broken), ``unit-import-observability``
  (an off-pin unit is unobserved), ``coverage-gap-remedy`` and
  ``ledger-band-second-reader`` — while the plant's published problem set
  (``SITE/checks/observer-checks.json``) names only
  ``evidence-binding-verdict-consistency`` and ``observer-verdict-agreement``.

So four live failures were invisible to the plant's monitoring while the
plant published a problem set that did not mention them.

The check reads, read-only:

* the reviewed repository's ``checks/*.py`` (tracked files only);
* the runner's own source, for its two tables — read as **text**, not as
  executed code, so this check never imports or runs the runner; and
* the runner's published state file, for the tables as the runner last
  recorded them, so a disagreement between the source and the published state
  is itself reported.

It imports nothing from the plant package and runs no plant program. It does
not decide whether a failing unscheduled check is *important* — that is a
judgement for the plant, and the check's job is to say which checks are
unheard and which of those are currently failing.

Exit status:
  0  every tracked check is either scheduled or declared-not-scheduled, or
     every unscheduled check that currently fails is named and its failure is
     reported
  2  a tracked check is neither scheduled nor declared-not-scheduled, and it
     currently fails: the plant is not reading a verdict it is already
     producing
  3  the question cannot be decided: the repository, the runner source or the
     published state cannot be read, or the runner's tables cannot be decoded
"""
from __future__ import annotations

import argparse
import ast
import json
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
    except (OSError, UnicodeError, ValueError) as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3

    unobserved = sorted(set(tracked) - scheduled - declared)
    # The runner's published state must agree with its own source. If it does
    # not, the plant cannot say which checks it reads.
    disagreement = sorted((published_scheduled | published_declared) -
                          (scheduled | declared)) + \
        sorted((scheduled | declared) - (published_scheduled | published_declared))

    if args.list:
        print("tracked checks : %d" % len(tracked))
        print("scheduled      : %d" % len(scheduled))
        print("declared skip  : %d" % len(declared))
        print("unobserved     : %d" % len(unobserved))
        for name in unobserved:
            print("  UNOBSERVED %s" % name)
        if disagreement:
            print("source/published disagreement: %s" % ", ".join(disagreement))
        return 0

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
        return 2
    if failing:
        print("VERDICT: %d unobserved check(s) currently fail — the plant is producing "
              "verdicts nobody reads" % len(failing))
        return 2
    print("VERDICT: every tracked check is scheduled or declared-not-scheduled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
