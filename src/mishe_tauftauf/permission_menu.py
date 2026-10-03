"""Keyboard-only operator decisions, bound to the request actually displayed."""
from __future__ import annotations

import curses
import sys
import textwrap
from pathlib import Path

from . import access


def decide_selected(home: Path, item: access.Request, state: str, decision: str) -> str:
    return access.decide(home, item.identity, decision,
                         expected_request=item, expected_state=state)


def _safe(text: str) -> str:
    return "".join(c if c.isprintable() else " " for c in text)


def _put(screen, row: int, text: str, attr: int = 0) -> None:
    height, width = screen.getmaxyx()
    if 0 <= row < height:
        try:
            screen.addnstr(row, 0, _safe(text), max(0, width - 1), attr)
        except curses.error:
            pass  # A resize or wide character can reach the terminal edge.


def interactive(screen, home: Path, decision: str = "granted") -> int:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(1000)
    selected = None
    index = 0
    offset = 0
    status = "No decisions are made until you press Enter."
    verb = "Grant" if decision == "granted" else "Revoke"
    while True:
        retired = access.retired_requests(home)
        rows = [(item, state) for item, state in access.list_requests(home)
                if (state != "granted" and item.identity not in retired if decision == "granted" else state == "granted")]
        if selected:
            index = next((i for i, (item, _) in enumerate(rows) if item.identity == selected), index)
        index = min(max(0, index), max(0, len(rows) - 1))
        selected = rows[index][0].identity if rows else None
        height, width = screen.getmaxyx()
        screen.erase()
        _put(screen, 0, f"Permissions — {verb} one selected request", curses.A_BOLD)
        _put(screen, 1, f"Site: {home}")
        _put(screen, 2, "Up/Down: select | Enter: " + verb.lower() + " | PgUp/PgDn: details | q: shell")
        small = height < 12 or width < 40
        if small:
            _put(screen, 4, "Resize terminal to at least 40 columns and 12 rows. Decisions disabled.")
        elif not rows:
            _put(screen, 4, "No requests to " + verb.lower() + ". New requests appear automatically.")
        else:
            # A short list leaves room to inspect the complete selected scope.
            count = min(4, max(1, height // 5))
            start = max(0, index - count + 1)
            for i, (item, state) in enumerate(rows[start:start + count], start):
                _put(screen, 4 + i - start, f"{'>' if i == index else ' '} {item.identity} [{state}]",
                     curses.A_REVERSE if i == index else 0)
            item, state = rows[index]
            details = [f"Request: {item.identity}", f"Status: {state}; owner: {item.owner}",
                       f"Task: {item.task}", f"Capability: {item.capability}",
                       "Unblocks: " + ", ".join(item.unblocks), "Reason: " + item.reason]
            recovery = next((row for row in access.recoveries(home)
                             if row["permission"] == item.identity), None)
            if recovery:
                details.extend([f"Resolver: {recovery['resolver']}",
                                f"Missing: {recovery['missing']}", f"After grant: {recovery['action']}",
                                "Alternatives: " + "; ".join(recovery["alternatives"]),
                                f"Cutoff: {recovery['cutoff']}",
                                "Grant permits a retry; success still needs evidence."])
            wrapped = [line for detail in details
                       for line in textwrap.wrap(_safe(detail), max(1, width - 2))]
            top = 5 + count
            available = max(1, height - top - 3)
            offset = min(offset, max(0, len(wrapped) - available))
            for i, line in enumerate(wrapped[offset:offset + available]):
                _put(screen, top + i, line)
            _put(screen, height - 3, f"Request {index + 1}/{len(rows)}; scope lines {offset + 1}-{min(len(wrapped), offset + available)}/{len(wrapped)}")
        _put(screen, height - 2, status)
        screen.refresh()
        key = screen.getch()
        if key in (ord("q"), 27):
            return 0
        if small or not rows:
            continue
        if key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(rows)
            selected, offset = rows[index][0].identity, 0
        elif key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(rows)
            selected, offset = rows[index][0].identity, 0
        elif key == curses.KEY_NPAGE:
            offset += available
        elif key == curses.KEY_PPAGE:
            offset = max(0, offset - available)
        elif key in (10, 13, curses.KEY_ENTER):
            item, state = rows[index]
            try:
                decide_selected(home, item, state, decision)
                status = f"{verb} recorded: {item.identity}"
                selected, offset = None, 0
            except ValueError as exc:
                status = str(exc)


def run(home: Path, decision: str = "granted") -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("permission selection needs an interactive terminal; use access list and access grant/revoke REQUEST_ID")
    if decision not in {"granted", "revoked"}:
        raise ValueError("decision must be granted or revoked")
    try:
        return curses.wrapper(interactive, home.resolve(), decision)
    except curses.error as exc:
        raise ValueError(f"permission menu terminal unavailable: {exc}; use access list and an exact request ID") from exc
