#!/usr/bin/env python3
"""Detector: the plant's pinned bytes must read the coverage rows the same way.

The plant publishes one coverage reading (``SITE/checks/initiative.json``): a
rolling 24-hour per-role view of recorded wakes, settlements and outcome
evidence. It is written by whichever import root runs the publisher, and the
plant runs more than one import root at once: the pinned release every service
and every CLI call uses, plus the observer bundle copy the publisher imports.

A row whose bound artifact was edited after binding is *re-evidenced*, not
checked, when a later outcome of the same role binds the corrected form under
the plant's remedy naming. That relation lives in the reader
(``outcome_publication.remedy_successor``), so it is **code, not data**: two
roots whose packages differ in that relation read the same rows differently.
The relation entered the reviewed tree in ``cb30132`` ("initiative: recognise
the plant's own remedy successor in the coverage reader").

The published reading names the rows it re-evidences (``link_kind``
``remedy-name``) beside the defect they still carry
(``own_evidence_state``). That is the plant's own statement of which rows rest
on the relation, so this check needs no second decider: it reads the rows the
plant names and asks whether the bytes the plant pins carry the relation.

Measured on the plant (2026-10-22, discover wake 88102):

* the published reading re-evidences five rows -- ``chat.log#77622`` and
  ``chat.log#79843`` and ``chat.log#86926`` (genome), ``chat.log#86397``
  (inference-research) and ``chat.log#86804`` (health) -- and every one of
  them still carries ``own_evidence_state`` UNKNOWN, so the loss is not gone,
  only its successor is recognised;
* the publisher runs the observer bundle copy, whose package carries the
  relation, and publishes ``outcomes.state`` OBSERVED with zero broken rows;
* the pinned release every other service runs does **not** carry the
  relation, and reading the same site with that package names exactly those
  five rows broken.

So the plant's green coverage reading is the *bundle's* reading. The site's
own declared-roots record retires the bundle root when "a pin release
carrying ``src/mishe_tauftauf/initiative.py``" is pinned -- and the current pin
carries that module while predating the relation. Repointing the publisher
onto it would flip those five rows back to broken, and the routed lifetime of
a broken row is bounded by the coverage window, so the loss would leave the
routed window without a notice.

This check reads published artifacts, the site's own CLI wrapper, process
environments and package *source*. It imports nothing from the plant package
and runs none of the plant's programs, so the rows it names come from the
plant's own published reading and not from a second reading of the plant.

Exit status:
  0  the published reading names no row that rests on the remedy relation, or
     the bytes the plant pins carry the relation the named rows rest on
  2  the published reading re-evidences rows by the remedy relation and the
     pinned bytes do not carry it: the pinned bytes read the named rows
     broken, and the two readings disagree about the same rows
  3  the question cannot be decided: no published reading, an unreadable or
     stale one, a reading that does not name the relation at all, or no pinned
     release the site can name
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

#: The published coverage reading: the plant's own per-role view.
REPORT_NAME = "checks/initiative.json"

#: How old a published reading may be before the check cannot decide.
DEFAULT_MAX_AGE = 300.0

#: The relation a row's re-evidence rests on, and the module that defines it.
RELATION = "remedy_successor"
RELATION_MODULE = "mishe_tauftauf/outcome_publication.py"

#: The field the reading uses to name a re-evidenced row, and the field that
#: carries the defect the row still has.
LINK_KIND = "link_kind"
REMEDY_KIND = "remedy-name"
OWN_STATE = "own_evidence_state"

#: The plant's own entry points, which name the bytes every call runs.
ENTRY_POINTS = ("bin/mishe-tauftauf", "bin/mishe")

#: The module the coverage reading is published by.
PUBLISHER_MODULE = "mishe_tauftauf.initiative"


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def _read_json(path: Path) -> dict:
    """A JSON object at path, or the reason it cannot be read."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Undecidable("%s cannot be read: %s" % (path.name, exc)) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise Undecidable("%s is not readable JSON: %s" % (path.name, exc)) from exc
    if not isinstance(payload, dict):
        raise Undecidable("%s is not a JSON object" % path.name)
    return payload


def _age_seconds(path: Path) -> float:
    """How old the published file is, in seconds."""
    return max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)


def _remedy_rows(report: dict) -> list[dict]:
    """The rows the reading re-evidences by the remedy relation.

    A reading whose rows carry no ``link_kind`` cannot say which rows rest on
    the relation, and a check that assumed none do would read a green the
    reading never gave.
    """
    outcomes = report.get("outcomes")
    items = outcomes.get("items") if isinstance(outcomes, dict) else None
    if not isinstance(items, list):
        raise Undecidable("the published reading carries no outcome items")
    names = False
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if LINK_KIND in item:
            names = True
        if item.get(LINK_KIND) == REMEDY_KIND:
            rows.append({
                "ref": item.get("ref"),
                "role": item.get("role"),
                "state": item.get("state"),
                "own_evidence_state": item.get(OWN_STATE),
                "successor": item.get("re_evidenced_by"),
            })
    if not names:
        raise Undecidable("the published reading names no remedy relation, "
                          "so it cannot say which rows rest on it")
    return rows


def _root_carries_relation(root: Path) -> bool:
    """Whether the package at root defines the remedy relation.

    The module is parsed, not imported: a root that cannot be parsed is a
    root this check cannot judge, and importing a plant package would run the
    candidate's own program.
    """
    module = root / RELATION_MODULE
    try:
        source = module.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Undecidable("%s cannot be read: %s" % (module, exc)) from exc
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise Undecidable("%s is unparseable: %s" % (module, exc)) from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == RELATION:
            return True
    return False


def _pinned_root(home: Path) -> Path:
    """The import root the plant's own entry point pins.

    The site's wrapper exports the pinned bytes for every call it makes, so
    it is the plant's own statement of what it runs.
    """
    for name in ENTRY_POINTS:
        script = home / name
        if not script.exists():
            continue
        try:
            text = script.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise Undecidable("%s cannot be read: %s" % (script, exc)) from exc
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("export PYTHONPATH="):
                continue
            for raw in line[len("export PYTHONPATH="):].split(":"):
                # The wrapper may append the caller's own PYTHONPATH; only the
                # pinned path it names is the plant's own bytes.
                candidate = raw.split("${")[0]
                if candidate.endswith("/src") and Path(candidate).is_dir():
                    return Path(candidate).resolve()
    raise Undecidable("no site entry point names a pinned import root")


def _site_processes(home: Path) -> list[tuple[list[str], Path]]:
    """The processes this site runs, with the import root each one uses.

    A process counts only when it names this site: by its ``--home``
    argument, or by the site home in its command line. Another site's
    processes share this host and may even import this site's releases, and
    they are not this site's reading.
    """
    found: list[tuple[list[str], Path]] = []
    try:
        pids = sorted(entry.name for entry in Path("/proc").iterdir() if entry.name.isdigit())
    except OSError as exc:
        raise Undecidable("/proc cannot be read: %s" % exc) from exc
    site = str(home)
    for pid in pids:
        base = Path("/proc") / pid
        try:
            raw = (base / "cmdline").read_bytes()
        except OSError:
            continue
        args = [part for part in raw.decode("utf-8", "replace").split("\0") if part]
        if not any("mishe_tauftauf" in part for part in args):
            continue
        if "--home" in args:
            index = args.index("--home")
            if index + 1 >= len(args):
                continue
            try:
                other = Path(args[index + 1]).resolve()
            except OSError:
                continue
            if other != home:
                continue
        elif site not in " ".join(args):
            continue
        try:
            environ = (base / "environ").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for item in environ.split("\0"):
            if not item.startswith("PYTHONPATH="):
                continue
            for entry in item[len("PYTHONPATH="):].split(":"):
                if not entry:
                    continue
                root = Path(entry)
                if root.name != "src" or not root.is_dir():
                    continue
                found.append((args, root.resolve()))
    return found


def _publisher_roots(processes: list[tuple[list[str], Path]]) -> list[Path]:
    """The roots a process that publishes the coverage reading imports."""
    roots: list[Path] = []
    for args, root in processes:
        joined = " ".join(args)
        if "-m %s " % PUBLISHER_MODULE in joined + " ":
            if root not in roots:
                roots.append(root)
    return roots


def check(home: Path, *, max_age: float, pin: Path | None) -> tuple[int, list[str]]:
    """Decide the question and return the exit status and the report lines."""
    report_path = home / REPORT_NAME
    if not report_path.exists():
        raise Undecidable("no published coverage reading at %s" % report_path)
    age = _age_seconds(report_path)
    if age > max_age:
        raise Undecidable("the published coverage reading is %ds old, over the %ds limit"
                          % (int(age), int(max_age)))
    report = _read_json(report_path)
    rows = _remedy_rows(report)
    lines: list[str] = []

    processes = _site_processes(home)
    live = sorted({str(root) for _args, root in processes})
    publishers = _publisher_roots(processes)
    if not live:
        lines.append("live root : no process of this site could be read")

    pinned = pin.resolve() if pin is not None else _pinned_root(home)
    pin_carries = _root_carries_relation(pinned)
    lines.append("pin       : %s carries %s: %s"
                 % (pinned, RELATION, "yes" if pin_carries else "no"))
    for root in sorted(set(publishers)):
        lines.append("publisher : %s carries %s: %s"
                     % (root, RELATION, "yes" if _root_carries_relation(root) else "no"))
    for root in live:
        lines.append("live root : %s" % root)

    if not rows:
        lines.append("rows      : the published reading re-evidences no row by the remedy relation")
        return 0, lines
    lines.append("rows      : %d row(s) re-evidenced by the remedy relation" % len(rows))
    for row in rows:
        lines.append("  row     : %s role=%s state=%s own_evidence=%s successor=%s"
                     % (row["ref"], row["role"], row["state"],
                        row["own_evidence_state"], row["successor"]))
    if pin_carries:
        lines.append("verdict   : the pinned bytes carry the relation the named rows rest on")
        return 0, lines
    lines.append("verdict   : the pinned bytes do not carry the relation; they read %d "
                 "row(s) broken that the published reading re-evidences, and repointing the "
                 "publisher onto the pin would flip the coverage reading" % len(rows))
    return 2, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, default=None,
                        help="the site home that holds the published reading")
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE,
                        help="how old the published reading may be, in seconds")
    parser.add_argument("--pin", type=Path, default=None,
                        help="the pinned import root to judge, instead of the site's own entry point")
    args = parser.parse_args(argv)
    home = args.home
    if home is None:
        for name in ("MISHE_SEED_HOME", "MISHE_TAUFTAUF_HOME"):
            value = os.environ.get(name)
            if value:
                home = Path(value)
                break
    if home is None:
        print("UNKNOWN: no site home given and none in the environment")
        return 3
    try:
        status, lines = check(home.resolve(), max_age=args.max_age, pin=args.pin)
    except Undecidable as exc:
        print("UNKNOWN: %s" % exc)
        return 3
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
