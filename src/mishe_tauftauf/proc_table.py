"""Decode external tables of process ids.

Two dialects name pids as text: ``ps -eo pid=,ppid=`` (one process per line, two
whitespace-separated decimal columns) and ``tmux list-panes -F '#{pane_pid}'``
(one pid per line). Internal consumers act on the decoded values, never on the
columns. Unreadable or malformed output yields no pid rather than a guess, so a
caller reaps only processes it can attribute to a known parent.
"""
from __future__ import annotations

import subprocess


def children_by_parent() -> dict[int, list[int]]:
    """Child pids keyed by parent pid; empty when the table is unreadable."""
    try:
        listing = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, check=False)
    except (OSError, UnicodeError):
        # A missing binary or an undecodable table is unreadable input, not a
        # caller error: the sweep must still reap what it can attribute.
        return {}
    children: dict[int, list[int]] = {}
    for line in listing.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and all(field.isdigit() for field in fields):
            children.setdefault(int(fields[1]), []).append(int(fields[0]))
    return children


def pane_pids(raw: bytes) -> list[int]:
    """Pids from ``tmux list-panes -F '#{pane_pid}'`` output; junk is skipped."""
    return [int(value) for value in raw.decode("ascii", "replace").split() if value.isdigit()]
