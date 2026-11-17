#!/usr/bin/env python3
"""Detector: the budget window must show the engine each mind actually runs on.

The ENGINE table is ``budget_view.engine_view()`` over ``engine/budget.json``.
Two of its columns come from different clocks, so they are judged separately:

1. ``next`` is the producer's ``routing.roles[role].selected``, computed by
   ``engine_router.assess()`` when the file was written (every 600 s). It is
   judged against that same file, not against a fresh ``assess()``: running
   counts and cooling move between writes, and a live comparison would fail on
   the snapshot's age instead of on a defect. Two properties are checked — the
   view renders the file's own ``selected``, and that ``selected`` is the first
   candidate the file's own ``trail`` marks eligible. A view that fell back to
   the first candidate of ``routes.json`` (or to ``engines/ROLE.json``) breaks
   one of the two.
2. ``running`` is the engine occupied right now: the latest ``select`` with no
   ``outcome`` after it in ``engine/routing-ledger.jsonl``. Read live on both
   sides (the view's and this check's), so it is a same-clock comparison.

It must NOT be compared against the first candidate of ``routes.json`` or
against ``engines/ROLE.json``; that shortcut was the earlier wrong mapping.

Limit: a file written with ``running=None`` (caps ignored) still shows a
self-consistent trail, so this check cannot see that omission; the producer's
``build_routing`` shares its call with the router's own ``publish``.

Exit codes: 0 holds (view renders the file, the file's trail picks that
selection, the ledger agrees on running), 2 fails (a role disagrees), 3 cannot
decide (site, budget view or ledger unreadable).

Usage: budget-view-mapping.py [--home SITE_HOME]
SITE_HOME defaults to $MISHE_SEED_HOME, then $MISHE_TAUFTAUF_HOME, then
``<repo>/.mishe-tauftauf``.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def site_home(argv: list[str]) -> Path:
    """The site home from ``--home``, then the environment, then the repo's own.

    ``--home`` is the convention the other checks use, including
    ``budget-consistency.py`` and the scheduler-coverage probe that runs a tracked
    check with ``--home``. A positional-only CLI made that probe pass ``--home``
    itself as the path, so the check read ``<cwd>/--home`` and reported a bogus
    CANNOT-DECIDE instead of the site's verdict (2026-11-17).
    """
    parser = argparse.ArgumentParser(description="The budget window must show the engine each mind runs on.")
    parser.add_argument("--home", type=Path, help="site home of the plant")
    args = parser.parse_args(argv[1:])
    if args.home is not None:
        return args.home.resolve()
    for name in ("MISHE_SEED_HOME", "MISHE_TAUFTAUF_HOME"):
        value = os.environ.get(name)
        if value:
            return Path(value).resolve()
    return REPO / ".mishe-tauftauf"


def load_view(home: Path):
    """Import the budget window module from the site, without running its main."""
    path = home / "operator" / "budget" / "budget_view.py"
    if not path.is_file():
        return None, f"budget view missing: {path}"
    spec = importlib.util.spec_from_file_location("_budget_view_under_check", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - reported as cannot-decide
        return None, f"budget view import failed: {str(exc)[:120]}"
    return module, None


def published_routing(budget: dict) -> tuple[dict | None, str | None]:
    """Per-role ``selected`` and ``trail`` from the budget file's own routing section."""
    routing = budget.get("routing")
    if not isinstance(routing, dict):
        return None, "routing section missing or malformed"
    roles = routing.get("roles")
    if not isinstance(roles, dict):
        return None, "routing.roles malformed"
    out = {}
    for role, row in roles.items():
        if not isinstance(row, dict):
            return None, f"routing.roles[{role!r}] malformed"
        trail = row.get("trail")
        out[role] = {"selected": row.get("selected"),
                     "decision": row.get("decision"),
                     "trail": trail if isinstance(trail, list) else None}
    return out, None


def first_eligible(trail) -> str | None:
    """The key the published trail's own verdicts choose: the first candidate marked eligible."""
    for step in trail or []:
        if isinstance(step, dict) and step.get("verdict") == "eligible":
            return step.get("key")
    return None


def at_capacity(trail) -> int:
    """How many candidates the publisher skipped for a full cap; reported, never a verdict."""
    return sum(1 for step in trail or []
               if isinstance(step, dict) and step.get("verdict") == "at-capacity")


def disagreements(view: dict, published: dict, running: dict) -> list[str]:
    """One line per role that disagrees. Pure, so the three properties stay separately testable."""
    bad = []
    for role in sorted(r for r in view if isinstance(view.get(r), dict)):
        row = view[role]
        if row.get("running") != running.get(role):
            bad.append(f"{role}: view running={row.get('running')} ledger running={running.get(role)}")
        pub = published.get(role)
        if pub is None:
            bad.append(f"{role}: view shows a role the file's routing section does not carry")
            continue
        if row.get("next") != pub["selected"]:
            bad.append(f"{role}: view next={row.get('next')} file routing.selected={pub['selected']}")
            continue
        want = first_eligible(pub["trail"])
        if want != pub["selected"]:
            bad.append(f"{role}: file routing.selected={pub['selected']} "
                       f"but its own trail's first eligible candidate is {want}")
    return bad


def ledger_running(home: Path) -> tuple[dict | None, str | None]:
    """Per-role engine running now: latest select with no outcome after it."""
    path = home / "engine" / "routing-ledger.jsonl"
    try:
        lines = path.read_text().splitlines()[-600:]
    except OSError as exc:
        return None, f"ledger unreadable: {exc}"
    running: dict[str, str] = {}
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("event") == "select":
            running[ev["role"]] = ev["key"]
        elif ev.get("event") == "outcome":
            running.pop(ev.get("role"), None)
    return running, None


def main(argv: list[str]) -> int:
    home = site_home(argv)
    if not (home / "engine").is_dir():
        print(f"budget-view-mapping: CANNOT-DECIDE no engine dir under {home}")
        return 3
    view_mod, err = load_view(home)
    if view_mod is None:
        print(f"budget-view-mapping: CANNOT-DECIDE {err}")
        return 3
    budget, err = view_mod.load_budget()
    if budget is None:
        print(f"budget-view-mapping: CANNOT-DECIDE {err}")
        return 3
    try:
        view = view_mod.engine_view(budget)
    except Exception as exc:  # noqa: BLE001
        print(f"budget-view-mapping: CANNOT-DECIDE engine_view raised {str(exc)[:120]}")
        return 3
    if view.get("error"):
        print(f"budget-view-mapping: CANNOT-DECIDE engine_view error {view['error']}")
        return 3
    published, err = published_routing(budget)
    if published is None:
        print(f"budget-view-mapping: CANNOT-DECIDE {err}")
        return 3
    running, err = ledger_running(home)
    if running is None:
        print(f"budget-view-mapping: CANNOT-DECIDE {err}")
        return 3

    roles = sorted(r for r in view if isinstance(view.get(r), dict))
    if not roles:
        print("budget-view-mapping: CANNOT-DECIDE view has no role rows")
        return 3
    bad = disagreements(view, published, running)
    if bad:
        for line in bad:
            print(f"budget-view-mapping: FAIL {line}")
        return 2
    capped = sum(at_capacity(row["trail"]) for row in published.values())
    note = f"; {capped} candidate(s) were at capacity when the file was written" if capped else ""
    print(f"budget-view-mapping: HOLDS {len(roles)} roles render the file's own selection and the ledger's running{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
