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


def pane_records(raw: str) -> list[tuple[str, str, int]]:
    """(session, window, pid) from ``tmux list-panes -a -F`` three-field output.

    The third dialect names session, window and pid, separated by ``|``.
    Internal consumers attribute each pane to the site hosting it: two sites
    render on one host and share every window name, so a window and pid alone
    cannot say which plant a suspect belongs to. Junk lines yield no record
    rather than a guess.
    """
    records: list[tuple[str, str, int]] = []
    for line in raw.splitlines():
        fields = line.split("|")
        if len(fields) != 3:
            continue
        session, window, pid_text = fields
        if not pid_text.isdigit():
            continue
        records.append((session, window, int(pid_text)))
    return records


def pane_commands(raw: str) -> list[tuple[str, str, str, str]]:
    """(session, pid, start_command, current_path) from a four-field pane table.

    The fourth dialect names the same session key, then the watcher pid, the
    command the pane started and the pane's working directory, separated by
    ``|``. A reader resolves a pane's import root from that command and pid, and
    resolves the same role name on two sites through the session that leads each
    record. Junk lines yield no record rather than a guess.
    """
    records: list[tuple[str, str, str, str]] = []
    for line in raw.splitlines():
        fields = line.split("|", 3)
        if len(fields) != 4:
            continue
        session, pid, start_command, current_path = fields
        if not pid.isdigit():
            continue
        records.append((session, pid, start_command, current_path))
    return records
