#!/usr/bin/env python3
"""Detector: every role's top and bottom pane renders as human-readable text.

The panes are what a person watches. They must carry words, clock times and
counts, never JSON, dict reprs, escaped newlines, raw epoch numbers or the
interpreter's private path. This renders each role's two panes from the live
site with the code under --repo and lints them with the same function the unit
tests use (`plant_view_boundary.machine_syntax`). Lines marked UNKNOWN, the
delimited raw block and what a tool call carried (its own arguments and its
printed output) are deliberate evidence and are exempt.

Exit status:
  0  every role's top and bottom pane is human-readable
  2  at least one pane contains machine syntax (the lines are listed)
  3  undecidable: presentation.json is missing or names no roles, or a pane could not be rendered
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def roles(home: Path, valid_role) -> list[str]:
    """The views the plant presents: presentation.json's active and resting areas.

    walls/ also holds dozens of staging notes whose names look like roles, so it is not a role list.
    """
    try:
        config = json.loads((home / "presentation.json").read_text(encoding="utf-8"))
        names = [*config["active"], *config["resting"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return sorted(n for n in names if isinstance(n, str) and valid_role(n) and n != "chat")


def evaluate(home: Path, now: float, render, machine_syntax, valid_role) -> tuple[int, list[str]]:
    found = roles(home, valid_role)
    if not found:
        return 3, [f"no roles in {home}/presentation.json to render"]
    bad, broken = [], []
    for role in found:
        for pane, output in (("top", False), ("bottom", True)):
            try:
                text = render(home, role, now=now, output=output)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                broken.append(f"{role} {pane}: could not render ({exc})")
                continue
            bad += [f"{role} {pane} {line}" for line in machine_syntax(text)]
    if bad:
        return 2, [f"{len(bad)} line(s) of machine syntax in the panes"] + bad[:12]
    if broken:
        return 3, broken[:12]
    return 0, [f"{len(found)} roles x 2 panes render as human-readable text"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.repo / "src"))
    from mishe_tauftauf.core.dispatch_record import valid_role
    from mishe_tauftauf.plant_view import render
    from mishe_tauftauf.plant_view_boundary import machine_syntax
    code, lines = evaluate(args.home, time.time(), render, machine_syntax, valid_role)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
