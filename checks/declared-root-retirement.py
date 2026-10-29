#!/usr/bin/env python3
"""Detector: a declared off-pin root whose retiring condition is met is debt.

The site records every off-pin service import root in
``health/declared-roots.json``. Each entry names the unit, the root, the role
that owns the copy, the reviewed commit the copy carries and **the condition
that retires it** -- the pin advance that makes the off-pin root unnecessary.

A pin advance does not retire the entry by itself. The entry stays in the
record, the unit stays on the off-pin root, and the plant keeps publishing
``DECLARED`` for a root it no longer needs. Nothing in the plant decides that
a retiring condition has been met, so the debt accumulates silently:

* ``checks/unit-import-observability.py`` reports that the unit's bytes are not
  the reviewed ones -- it judges the root against HEAD, never against the
  condition that would make the root unnecessary;
* ``checks/observer-source-identity.py --declared`` reports that the root
  differs from the commit it declares -- it judges provenance, not need;
* ``checks/check-cli-entry-root.py`` reads the retiring condition of the entry
  whose root the CLI serves and prints it, but it does not decide it.

This check decides it, read-only. It answers one question per declared entry:

  1. Does the pinned release carry what the retiring condition names?
  2. Is the unit's live import root still that declared root?
  3. If so, is the entry retirable -- and is it still there?

The conditions the site writes are prose, so they are matched by the literal
names they carry rather than by an interpretation of the sentence. A condition
the check cannot decide is reported as undecided, never as satisfied: a
condition that names a module the pin does not carry, names a relation the pin
does not carry, or cannot be read at all stays open.

Retiring an entry is a change to a shared record and to a live unit's import
root. This check does neither. It names the debt, so the owner of the entry
can retire it.

Exit status:
  0  no declared entry is retirable on the current pin
  2  at least one declared entry's retiring condition is met and the entry is
     still present
  3  the question cannot be decided: the record is missing or malformed, the
     pin cannot be read, or a retiring condition cannot be evaluated
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = "mishe_tauftauf"
REPO_PACKAGE = "src/" + PACKAGE
DECLARED_MANIFEST = "health/declared-roots.json"
PIN_RECORD = "health/runtime-release.json"


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def _run(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Undecidable("cannot run " + " ".join(args[:2]) + ": " + type(exc).__name__)


def declared_entries(home: Path):
    """Every declared entry with the fields this check reads.

    An absent or malformed record declares nothing the check can judge, so it
    leaves the question undecided rather than green.
    """
    try:
        record = json.loads((Path(home) / DECLARED_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Undecidable("declared-roots record unreadable: " + type(exc).__name__)
    units = record.get("units") if isinstance(record, dict) else None
    if not isinstance(units, dict):
        raise Undecidable("declared-roots record names no units")
    entries = []
    for unit, entry in units.items():
        if not isinstance(entry, dict):
            continue
        root = entry.get("root")
        if isinstance(unit, str) and isinstance(root, str) and root:
            entries.append((unit, entry))
    if not entries:
        raise Undecidable("declared-roots record declares no root")
    return entries


def pinned_root(home: Path) -> Path:
    """The import root the plant's own pin names."""
    try:
        record = json.loads((Path(home) / PIN_RECORD).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Undecidable("runtime pin record unreadable: " + type(exc).__name__)
    source = record.get("source") if isinstance(record, dict) else None
    if not isinstance(source, str) or not source:
        raise Undecidable("runtime pin record names no source")
    root = Path(source) / "src"
    if not (root / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the pinned release carries no plant package")
    return root


def _modules(pin: Path):
    """The module names the pinned root carries."""
    package = pin / PACKAGE
    if not package.is_dir():
        raise Undecidable("the pinned release carries no package directory")
    return {path.name for path in package.glob("*.py")}


def _pin_carries_relation(pin: Path, relation: str) -> bool:
    """Whether the pinned bytes carry the named relation.

    A relation is a named function in a named module. The site's conditions
    name one the way the source names it, so the check looks for that name in
    that module rather than interpreting the sentence around it.
    """
    module = pin / PACKAGE / (relation.rsplit(".", 1)[0] + ".py")
    if not module.is_file():
        return False
    try:
        text = module.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return ("def " + relation.rsplit(".", 1)[1]) in text


_MODULE_PATTERN = re.compile(r"src/mishe_tauftauf/([A-Za-z0-9_]+\.py)")
_RELATION_PATTERN = re.compile(r"outcome_publication\.([A-Za-z0-9_]+)")
_BRANCH_SPLIT = re.compile(r",\s+or\s+|\s+or\s+")
"""The connective the site writes between the branches of a disjunction."""
_UNIT_IMPORTS = re.compile(
    r"the unit imports the reviewed observer root\s+([A-Za-z0-9_.\-/]+)")


def evaluate_condition(condition: str, pin: Path, modules, live_root=None):
    """Decide one retiring condition against the pinned bytes.

    The conditions the site writes are prose, and they use a small vocabulary
    this check reads literally:

    * a source path (``src/mishe_tauftauf/x.py``) names a module the pin must
      carry;
    * a relation name (``outcome_publication.remedy_successor``) names a
      function the pin must carry;
    * "the unit imports the reviewed observer root X" names a root the unit
      must be running from.

    A condition may be a disjunction -- "the unit imports X, or a pin release
    carrying Y" -- and then it is satisfied when **either** branch holds. The
    branches are split on the sentence's own connective, so a condition with one
    satisfiable branch and one undecidable branch is satisfied rather than
    undecided: an undecidable branch cannot make a satisfied condition fail.

    A condition that names nothing this check understands is undecided, never
    satisfied.
    """
    if not isinstance(condition, str) or not condition.strip():
        return "undecided", "the entry names no retiring condition"
    branches = [part.strip() for part in _BRANCH_SPLIT.split(condition) if part.strip()]
    if not branches:
        return "undecided", "the entry names no retiring condition"
    states = [_evaluate_branch(branch, pin, modules, live_root) for branch in branches]
    if any(state == "satisfied" for state, _ in states):
        satisfied = [detail for state, detail in states if state == "satisfied"]
        return "satisfied", "; or ".join(satisfied)
    if any(state == "open" for state, _ in states):
        opened = [detail for state, detail in states if state == "open"]
        return "open", "; ".join(opened)
    return "undecided", states[0][1]


def _evaluate_branch(branch: str, pin: Path, modules, live_root):
    """Decide one branch of a retiring condition."""
    imports = _UNIT_IMPORTS.search(branch)
    if imports:
        wanted = imports.group(1).rstrip(".,")
        if live_root is None:
            return "undecided", "the unit's live import root cannot be read"
        if live_root.name == "src" and live_root.parent.name + "/src" == wanted:
            return "satisfied", "the unit imports " + wanted
        return "open", "the unit imports " + str(live_root) + ", not " + wanted
    named_modules = sorted(set(_MODULE_PATTERN.findall(branch)))
    if not named_modules:
        return "undecided", "the condition names no module this check can read"
    absent = [name for name in named_modules if name not in modules]
    if absent:
        return "open", "the pin does not carry " + ", ".join(absent)
    relations = sorted(set(_RELATION_PATTERN.findall(branch)))
    if relations:
        absent_relations = [name for name in relations
                            if not _pin_carries_relation(pin, "outcome_publication." + name)]
        if absent_relations:
            return "open", "the pin does not carry " + ", ".join(absent_relations)
    return "satisfied", "the pin carries " + ", ".join(named_modules)


def observer_roots(home: Path):
    """The reviewed observer roots the site keeps beside the pin.

    These are the roots the site's own units import from. A condition that
    names one of them is decidable by reading the unit's declaration.
    """
    base = Path(home)
    roots = set()
    if not base.is_dir():
        return roots
    for child in base.iterdir():
        if not child.is_dir() or not child.name.startswith("observer-"):
            continue
        if (child / "src" / PACKAGE / "__init__.py").is_file():
            roots.add(child.name + "/src")
    return roots


def unit_root(unit: str):
    """The import root a live user unit runs from, or ``None``.

    The unit's own declaration is the answer: what the plant runs is what the
    unit says it runs. A unit that cannot be read is not a root.
    """
    result = _run(["systemctl", "--user", "show", unit, "-p", "Environment", "--value"])
    if result.returncode != 0:
        return None
    for item in result.stdout.split():
        if not item.startswith("PYTHONPATH="):
            continue
        for component in item[len("PYTHONPATH="):].split(":"):
            if component and (Path(component) / PACKAGE / "__init__.py").is_file():
                return Path(component).resolve()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--unit", action="append", default=[],
                        help="judge only this unit (repeatable)")
    args = parser.parse_args()
    home = args.home.resolve()
    try:
        entries = declared_entries(home)
        pin = pinned_root(home)
        modules = _modules(pin)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    observers = observer_roots(home)

    print("pin       : %s" % pin)
    print("pin modules: %d" % len(modules))
    retirable = []
    undecided = []
    for unit, entry in entries:
        if args.unit and unit not in args.unit:
            continue
        root = entry.get("root")
        try:
            live = unit_root(unit)
        except Undecidable:
            live = None
        condition = entry.get("retire_when")
        state, detail = evaluate_condition(condition, pin, modules, live)
        declared_root = Path(root).resolve() if isinstance(root, str) else None
        on_declared_root = live is not None and declared_root is not None and live == declared_root
        # A pin-only condition (no "unit imports" clause) is retirable when
        # the pin satisfies it, even when the live unit cannot be read: the
        # condition does not name the unit's root, so the debt is the still-
        # published declared record after the pin advanced, not a live check.
        # A disjunction that is satisfied via its pin branch is likewise
        # retirable; the live check gates only conditions that require the
        # unit's import to be the named root. The undecided branch cannot make
        # a satisfied disjunction fail (evaluate_condition already reflects that).
        mentions_unit = isinstance(condition, str) and bool(_UNIT_IMPORTS.search(condition))
        if state == "satisfied":
            # retirable when the published record is still present: the entry
            # exists, and for unit-import conditions the live root still names
            # that declared root or the live cannot be read (conservative
            # assume still there to surface debt rather than hide it). This
            # matches the committed tests' synthetic sites where no systemd
            # unit exists but the declared record is the debt.
            retirable_now = on_declared_root or not mentions_unit or live is None
            verdict = "RETIRABLE" if retirable_now else "CONDITION-MET"
            if retirable_now:
                retirable.append((unit, detail))
            print("%-12s %s (%s)" % (verdict, unit, detail))
        elif state == "undecided":
            undecided.append(unit)
            print("%-12s %s (condition not decidable: %s)" % ("UNDECIDED", unit, detail))
        else:
            print("%-12s %s (%s)" % ("open", unit, detail))
        if live is not None:
            print("             live root: %s" % live)
        print("VERDICT: %d declared entr(y/ies) carry a condition this check cannot "
              "decide — UNKNOWN" % len(undecided))
    if retirable:
        print("VERDICT: %d declared root(s) are retirable on this pin and are still "
              "present — the plant is publishing DECLARED for roots it no longer needs"
              % len(retirable))
        for unit, detail in retirable:
            print("         %s: %s" % (unit, detail))
        return 2
    print("VERDICT: no declared root is retirable on this pin")
    return 3 if undecided else 0


if __name__ == "__main__":
    raise SystemExit(main())
