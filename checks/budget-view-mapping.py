#!/usr/bin/env python3
"""Detector: the budget window must show the engine each mind actually runs on.

The budget window's ENGINE table is computed by ``budget_view.engine_view()``.
It must agree with two independent readings of the router's own state:

1. ``engine_router.assess()`` over the live routes, live account status and
   cooling state gives the engine each role would pick for its next wake.
2. ``engine/routing-ledger.jsonl`` gives the engine running now: the latest
   ``select`` for a role with no ``outcome`` after it.

It must NOT be compared against the first candidate of ``routes.json`` or
against ``engines/ROLE.json``; that shortcut was the earlier wrong mapping.

Exit codes: 0 holds (view agrees with router + ledger), 2 fails (a role's
view disagrees), 3 cannot decide (site, router or budget view unreadable).

Usage: budget-view-mapping.py [SITE_HOME]
SITE_HOME defaults to $MISHE_SEED_HOME, then $MISHE_TAUFTAUF_HOME, then
``<repo>/.mishe-tauftauf``.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def site_home(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1]).resolve()
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


def router_next(home: Path) -> tuple[dict | None, str | None]:
    """Per-role next engine from engine_router.assess() on live inputs."""
    src = home.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from mishe_tauftauf import engine_router as er
    except Exception as exc:  # noqa: BLE001
        return None, f"engine_router import failed: {str(exc)[:120]}"
    try:
        routes = er.load_routes(home)
        a = er.assess(routes, er.live_status(home), er._load_state(home)["cooling"], time.time() * 1000)
    except Exception as exc:  # noqa: BLE001
        return None, f"assess failed: {str(exc)[:120]}"
    roles = a.get("roles")
    if roles is None:
        return None, f"assess returned no roles (state {a.get('state')})"
    return {role: row.get("selected") for role, row in roles.items()}, None


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
    nxt, err = router_next(home)
    if nxt is None:
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
    bad = []
    for role in roles:
        row = view[role]
        want_next = nxt.get(role)
        want_run = running.get(role)
        if row.get("next") != want_next or row.get("running") != want_run:
            bad.append((role, row.get("running"), want_run, row.get("next"), want_next))
    if bad:
        for role, vr, wr, vn, wn in bad:
            print(f"budget-view-mapping: FAIL {role}: view running={vr} router-ledger running={wr}; "
                  f"view next={vn} assess next={wn}")
        return 2
    print(f"budget-view-mapping: HOLDS {len(roles)} roles agree with assess() and the ledger")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
