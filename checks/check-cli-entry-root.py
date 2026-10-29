#!/usr/bin/env python3
"""Detector: the CLI entry point must serve the bytes the plant pinned.

The site has two different statements of "the bytes this plant runs":

* the **runtime pin** (``SITE/health/runtime-release.json``), which names the
  release every service is started from; and
* the **CLI wrapper** (``SITE/bin/mishe-tauftauf`` and ``SITE/bin/mishe``),
  which exports the import root every ``mishe`` call actually loads.

When those two agree, a fix committed to the reviewed tree reaches both the
services and the commands a mind types. When they disagree, the plant has a
second, undeclared execution path, and a fix can be live on one path and
inert on the other while the plant publishes no verdict about it.

This is not hypothetical, and the failure is silent rather than loud. Measured
on this plant (2026-10-23):

* the runtime pin names release ``6576207b``;
* the CLI wrapper exports ``.mishe-tauftauf/model-cli-20261022/src`` — which is
  neither the pin nor the reviewed checkout;
* the wrapper's root predates the write-once clause for bound outcome evidence
  (commit ``d9f4ebe``, which makes a bound evidence file read-only at
  publication), so every ``wall outcome`` published *through the CLI* leaves
  its bound evidence file writable, and a later edit drops that row
  permanently.

The symptom was visible only in aggregate — the plant's
``writable-bound-evidence`` check reported 3577 bound evidence files still
writable — and nothing named the root that caused it. This check names the
root.

The check answers one question, read-only:

  1. Which import root does the site's CLI entry point export?
  2. Is that root the pinned root, the reviewed checkout, or a third root?
  3. Does that root carry the plant's write-once clause for bound evidence,
     and does the reviewed checkout carry it?

The third question is what makes this check more than a pointer. The plant's
reviewed tree carries the clause; the question is whether the path a mind
actually types carries it too. A root the site declared on purpose
(``health/declared-roots.json``) is a *decision the plant recorded*, but the
declaration is scoped to a **unit** — and the CLI wrapper is a second consumer
of the same root that the declaration does not name. So a declared root is not
by itself an answer: what matters is whether the clause is enforced on the path
that publishes.

The baseline is the reviewed checkout, not the pin. The pin is allowed to lag
HEAD — that is a published, owned fact (a pin advance is genome's action) — so
a pin that lacks the clause is reported as information, not as a failure. What
is a failure is a **reviewed fix that is inert on the CLI path**: the checkout
carries the clause and the root the CLI serves does not.

Exit status:
  0  the CLI entry point serves the pinned root, or the root it serves carries
     the write-once clause the reviewed checkout carries
  2  the CLI entry point serves a root that is not the pin, and that root does
     not carry the write-once clause the reviewed checkout carries: a reviewed
     fix is live in the tree and inert on the path every command runs
  3  the question cannot be decided: no entry point, no readable wrapper, no
     readable pin record, an unparseable root, or a root that carries no plant
     package
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

#: The site's CLI entry points. Both are read; the first that names a root wins.
ENTRY_POINTS = ("bin/mishe-tauftauf", "bin/mishe")

#: The plant's package directory, relative to an import root.
PACKAGE = "mishe_tauftauf"

#: The module that carries the write-once clause for bound evidence.
CLAUSE_MODULE = "wall.py"

#: The function whose body performs the clause. The clause is present when
#: this function exists in the module *and* the module changes the evidence
#: file's mode; a function that only reads the mode would not enforce it.
CLAUSE_FUNCTION = "_publish_outcome"

#: The call that enforces the clause, matched by attribute name so that a
#: renamed import of ``os`` still matches.
CLAUSE_CALL = "chmod"


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Undecidable("%s cannot be read: %s" % (path, exc)) from exc


def entry_point_root(home: Path) -> tuple[Path, Path]:
    """The import root the site's CLI entry point exports, and the script.

    The wrapper's ``export PYTHONPATH=`` line is the plant's own statement of
    the bytes every command it runs loads. The wrapper may append the caller's
    own ``PYTHONPATH``; only the path it names itself is the plant's bytes.
    """
    for name in ENTRY_POINTS:
        script = home / name
        if not script.exists():
            continue
        for line in _read_text(script).splitlines():
            line = line.strip()
            if not line.startswith("export PYTHONPATH="):
                continue
            for raw in line[len("export PYTHONPATH="):].split(":"):
                candidate = raw.split("${")[0]
                if candidate.endswith("/src") and Path(candidate).is_dir():
                    return Path(candidate).resolve(), script
    raise Undecidable("no site entry point names an import root")


def pinned_root(home: Path) -> Path:
    """The import root of the bytes the plant is pinned to."""
    path = home / "health" / "runtime-release.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Undecidable("no runtime pin record at " + str(path))
    except (OSError, ValueError) as exc:
        raise Undecidable("the runtime pin record is unreadable: " + type(exc).__name__)
    if not isinstance(record, dict) or not isinstance(record.get("source"), str):
        raise Undecidable("the runtime pin record names no source")
    root = Path(record["source"]) / "src"
    if not (root / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the pinned source carries no plant package")
    return root


def carries_clause(root: Path) -> bool:
    """Whether the package at ``root`` enforces the write-once clause.

    The module is parsed, not imported: importing a plant package would run
    the candidate's own program, and a root that cannot be parsed is a root
    this check cannot judge.
    """
    module = root / PACKAGE / CLAUSE_MODULE
    if not module.is_file():
        return False
    source = _read_text(module)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise Undecidable("%s is unparseable: %s" % (module, exc)) from exc
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != CLAUSE_FUNCTION:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Attribute) and func.attr == CLAUSE_CALL:
                return True
    return False


def declared_roots(home: Path) -> dict:
    """The off-pin roots the site declares, mapped to their entries."""
    path = home / "health" / "declared-roots.json"
    if not path.exists():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Undecidable("the declared-roots record is unreadable: " + type(exc).__name__)
    if not isinstance(record, dict):
        raise Undecidable("the declared-roots record is not a JSON object")
    units = record.get("units")
    if not isinstance(units, dict):
        return {}
    declared = {}
    for name, entry in units.items():
        if isinstance(entry, dict) and isinstance(entry.get("root"), str):
            declared[entry["root"]] = {"unit": name, "entry": entry}
    return declared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    args = parser.parse_args()
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    try:
        cli_root, script = entry_point_root(home)
        pin = pinned_root(home)
        cli_clause = carries_clause(cli_root)
        pin_clause = carries_clause(pin)
        checkout = (repo / "src").resolve()
        if not (checkout / PACKAGE / "__init__.py").is_file():
            raise Undecidable("the reviewed checkout carries no plant package")
        checkout_clause = carries_clause(checkout)
        declared = declared_roots(home)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3

    print("entry point : %s" % script)
    print("cli root    : %s" % cli_root)
    print("pinned root : %s" % pin)
    print("checkout    : %s" % checkout)
    print("cli root carries the write-once clause: %s" % ("yes" if cli_clause else "no"))
    print("pin carries the write-once clause     : %s" % ("yes" if pin_clause else "no"))
    print("checkout carries the write-once clause: %s" % ("yes" if checkout_clause else "no"))

    if cli_root == pin:
        print("VERDICT: the CLI entry point serves the pinned bytes")
        return 0

    declaration = declared.get(str(cli_root))
    if declaration is None:
        print("verdict     : the CLI entry point serves an undeclared root")
    else:
        print("verdict     : the CLI entry point serves a declared root (%s, owner %s)"
              % (declaration["unit"], declaration["entry"].get("owner", "unknown")))
        print("              retire when: %s" % declaration["entry"].get("retire_when", "unstated"))

    if not checkout_clause:
        # The reviewed tree does not carry the clause, so there is no reviewed
        # fix for the CLI path to be inert about. Nothing is failing here.
        print("VERDICT: the reviewed checkout does not carry the write-once clause, so no "
              "reviewed fix is inert on the CLI path")
        return 0

    if cli_clause:
        print("VERDICT: the CLI entry point serves a third root that already carries the "
              "write-once clause the reviewed checkout carries")
        return 0

    print("VERDICT: the CLI entry point serves %s, which does not carry the write-once clause "
          "the reviewed checkout carries — a reviewed fix is live in the tree and inert on the "
          "path every command runs, so every bound outcome published through the CLI leaves its "
          "evidence writable" % cli_root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
