#!/usr/bin/env python3
"""Detector: every role's top and bottom pane renders as human-readable text.

The panes are what a person watches. They must carry words, clock times and
counts, never JSON, dict reprs, escaped newlines, raw epoch numbers or the
interpreter's private path. This renders each role's two panes from the live
site with the code under --repo and lints them with the same function the unit
tests use (`plant_view_boundary.machine_syntax`). Lines marked UNKNOWN, the
delimited raw block and what a tool call carried (its own arguments and its
printed output) are deliberate evidence and are exempt.

A finding's message shortens the line it blames to 80 characters, so the whole of
each offending line is also appended to SITE/views/view-readability-findings.log
(newest 200 rows): on 2026-11-17 07:13Z a title line was blamed for a quoted key
that was past column 80 and was gone before anyone could read it.

Exit status:
  0  every role's top and bottom pane is human-readable
  2  at least one pane contains machine syntax (the lines are listed)
  3  undecidable: presentation.json is missing or names no roles, or a pane could not be rendered
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

FINDINGS = Path("views") / "view-readability-findings.log"
FINDINGS_KEPT = 200
"""A finding's whole line, kept for diagnosis. ``machine_syntax`` shortens the line it reports to 80 characters, and a
pane's title line is longer: on 2026-11-17 07:13Z it blamed ``health`` line 1 for "a quoted key" that was not inside
those 80 characters, so what actually tripped the rule was gone by the time anyone looked. Findings are rare, so the
log stays small; it is trimmed to the newest lines when it grows past ``FINDINGS_KEPT``."""

_LINE_NUMBER = re.compile(r"^line (\d+): ")


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


def _whole_line(text: str, message: str) -> str:
    """The pane line a ``machine_syntax`` message shortened to 80 characters."""
    match = _LINE_NUMBER.match(message)
    if not match:
        return message[:80]
    lines = text.splitlines()
    number = int(match.group(1))
    return lines[number - 1] if 0 < number <= len(lines) else message[:80]


def retain(home: Path, findings: list[tuple[str, str, str, str]], now: float) -> str | None:
    """Append each ``(role, pane, message, whole line)`` finding; return where it was kept.

    Evidence, not a gate: a log that cannot be written must not hide the finding or turn it into UNKNOWN.
    """
    path = Path(home) / FINDINGS
    stamp = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [f"{stamp} {role} {pane} {message} | {line}" for role, pane, message, line in findings]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        kept = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        kept = (kept + rows)[-FINDINGS_KEPT:]
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return None
    return str(path)


def evaluate(home: Path, now: float, render, machine_syntax, valid_role) -> tuple[int, list[str]]:
    found = roles(home, valid_role)
    if not found:
        return 3, [f"no roles in {home}/presentation.json to render"]
    bad, broken, gathered = [], [], []
    for role in found:
        for pane, output in (("top", False), ("bottom", True)):
            try:
                text = render(home, role, now=now, output=output)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                broken.append(f"{role} {pane}: could not render ({exc})")
                continue
            for message in machine_syntax(text):
                bad.append(f"{role} {pane} {message}")
                gathered.append((role, pane, message, _whole_line(text, message)))
    if bad:
        kept = retain(home, gathered, now)
        where = f"; whole lines in {kept}" if kept else "; whole lines could not be kept"
        return 2, [f"{len(bad)} line(s) of machine syntax in the panes{where}"] + bad[:12]
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
