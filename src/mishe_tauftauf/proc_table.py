"""Decode external tables of process ids.

Three dialects name pids as text: ``ps -eo pid=,ppid=`` (one process per line,
two whitespace-separated decimal columns), ``tmux list-panes -F '#{pane_pid}'``
(one pid per line), and the session/window/pid and watcher tables below.
Internal consumers act on the decoded values, never on the columns. Unreadable
or malformed output yields no pid rather than a guess, so a caller reaps only
processes it can attribute to a known parent. A pane's live import root lives
in its process environment, decoded by the same rule everywhere it is read.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


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


def pane_pids(raw: bytes | str) -> list[int]:
    """Pids from ``tmux list-panes -F '#{pane_pid}'`` output; junk is skipped."""
    text = raw.decode("ascii", "replace") if isinstance(raw, bytes) else raw
    return [int(value) for value in text.split() if value.isdigit()]


def window_names(raw: bytes | str) -> list[str]:
    """Window names from ``tmux list-windows -F '#{window_name}'`` output.

    One name per line; blank lines are unreadable input, not an empty name,
    so they are skipped rather than guessed.
    """
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    return [line for line in text.splitlines() if line.strip()]


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


def git_log_entries(raw: str) -> list[dict]:
    """Newest-first ``{subject, files}`` entries from ``--name-only`` log output.

    The sixth dialect groups each ``SUBJECT:``-prefixed subject line with
    the non-blank file names that follow it until the next subject. Junk
    lines yield no entry rather than a guess; a subject with no files still
    records its empty file list so the caller can classify it.
    """
    entries: list[dict] = []
    subject: str | None = None
    files: list[str] = []

    def flush() -> None:
        if subject is not None:
            entries.append({"subject": subject, "files": list(files)})

    for line in raw.splitlines():
        if line.startswith("SUBJECT:"):
            flush()
            subject = line[len("SUBJECT:"):]
            files = []
        elif line.strip():
            files.append(line.strip())
    flush()
    return entries



def git_reflog_entries(raw: str) -> list[tuple[str, str]]:
    """Newest-first ``(sha, message)`` pairs from ``git reflog --format=%H %gs``.

    Each line is ``<sha> <message>``; the reflog records every HEAD movement,
    so a caller filters the messages it cares about. A line without both a sha
    and a non-blank message yields no entry rather than a guess, so the caller
    attributes only rewinds it can actually read.
    """
    entries: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2 or re.fullmatch(r"[0-9a-f]{40}", parts[0]) is None:
            continue
        if not parts[1].strip():
            continue
        entries.append((parts[0], parts[1].strip()))
    return entries


def git_path_names(raw: str) -> list[str]:
    """Path names from ``git diff-tree --no-commit-id --name-only -r`` output.

    One path per line; blank lines yield no path. The caller bounds how many
    it classifies, so an unbounded diff cannot make the reader slow.
    """
    return [line.strip() for line in raw.splitlines() if line.strip()]

def git_reachable_shas(raw: str) -> set[str]:
    """Commit ids reachable from a ref, from ``git rev-list <ref>`` output.

    One commit id per line. The set answers "is this commit still reachable
    from the ref", which a caller needs over a whole reflog window; a
    per-commit ``git merge-base --is-ancestor`` answers the same question at
    the cost of one git process per commit. A line that is not a commit id
    yields no entry rather than a guess, so a caller never treats a stray
    line as a reachable commit.
    """
    shas: set[str] = set()
    for line in raw.splitlines():
        sha = line.strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            shas.add(sha)
    return shas

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


def pane_windows(raw: str) -> list[tuple[str, str, str, str]]:
    """(window, index, dead, pid) from a four-field window table.

    The fifth dialect extends the window/index/dead triple with the pane's
    process id, so one sample carries both the window presence a caller checks
    and the pid whose live environment it reads. Junk lines, and rows whose pid
    is not decimal, yield no record rather than a guess.
    """
    records: list[tuple[str, str, str, str]] = []
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        window, index, dead, pid = fields
        if not pid.isdigit():
            continue
        records.append((window, index, dead, pid))
    return records


def pane_environ_pythonpath(raw: bytes) -> list[str]:
    """PYTHONPATH components from a pane process's ``/proc/<pid>/environ`` bytes.

    The environment is NUL-separated ``KEY=VALUE`` fields; ``execve`` cannot
    carry a repeated key, so the first PYTHONPATH entry is the value the
    process runs with. Undecodable bytes are replaced, never guessed.
    """
    prefix = b"PYTHONPATH="
    for field in raw.split(b"\0"):
        if field.startswith(prefix):
            return field[len(prefix):].decode("utf-8", "replace").split(os.pathsep)
    return []


def read_pane_pythonpath(pid: str) -> list[str]:
    """PYTHONPATH components a pane process's live environment names.

    The pane's environment is what the process actually imported from: tmux
    adds the server global environment and any ``-e``/``respawn-pane``
    override at pane creation, neither of which a stored start command shows.
    A vanished or unreadable pid yields no components rather than a guess.
    """
    if not pid:
        return []
    try:
        raw = Path("/proc", pid, "environ").read_bytes()
    except OSError:
        return []
    return pane_environ_pythonpath(raw)
RELEASE_NAME = re.compile(r"releases/([0-9a-f]{40})(?![0-9a-f])")
"""A release directory name, as a process environment or unit text names it."""


def release_names(raw: bytes | str) -> list[str]:
    """Release directory names a process environment or unit text names.

    A live process names its release in ``PYTHONPATH`` and an installed unit in
    ``Environment``; both are external text, so the extraction lives at this
    boundary and the caller acts on the decoded names. Only a full 40-hex name is
    returned: a placeholder such as ``releases/0`` is not a release. Undecodable
    bytes are replaced, never guessed.
    """
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    return sorted(set(RELEASE_NAME.findall(text)))

def read_pane_cmdline(pid: str) -> str:
    """The pane process's ``/proc/<pid>/cmdline`` as a single string.

    The pane pid is the process the launcher raised, so the cmdline says
    whether the pane runs a mind (``python -m mishe_tauftauf …``) or a
    plain shell that only carries a launcher's exported PYTHONPATH. A
    vanished or unreadable pid yields "" rather than a guess.
    """
    if not pid:
        return ""
    try:
        raw = Path("/proc", pid, "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()

def read_cgroup_unit(pid: str) -> str:
    """The systemd unit a pid's cgroup names, or "" when unreadable.

    ``/proc/<pid>/cgroup`` carries one ``0::/path`` line for the unified
    hierarchy, whose last component is the systemd unit that owns the pid.
    The unit name is how a consumer attributes a process to the keeper or
    scope that raised it, so this decoder returns the last path component
    rather than the whole slice path. An unreadable pid, a non-``0::`` line
    or an empty path yields "" rather than a guess.
    """
    if not pid:
        return ""
    try:
        text = Path("/proc", pid, "cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        fields = line.split(":")
        if len(fields) < 3 or fields[0] != "0" or not fields[2].startswith("/"):
            continue
        return fields[2].rsplit("/", 1)[-1]
    return ""


def belongs_to_session(unit: str, session: str) -> bool:
    """Whether a cgroup unit is one of ``session``'s own keeper units.

    A plant raises its session through one of two unit shapes, both prefixed
    with the session name: ``<session>-session.scope`` for the legacy scoped
    raise and ``<session>-session-<uuid>.service`` for the readiness-aware
    transient keeper. A unit belonging to a foreign session — a test's
    ``mishe-tauftauf-test-<id>-<uuid>-session-<uuid>.service`` keeper, for
    instance — holds the shared server while nothing restsarts it. The test
    lives here with the rest of the cgroup decoding so consumers compare
    decoded values instead of parsing unit text themselves.
    """
    if not unit or not session:
        return False
    prefix = session + "-session"
    if unit == prefix + ".scope":
        return True
    if not unit.startswith(prefix + "-") or not unit.endswith(".service"):
        return False
    uuid_part = unit[len(prefix) + 1:-len(".service")]
    return len(uuid_part) == 32 and all(c in "0123456789abcdef" for c in uuid_part)


def read_process_comm(pid: str) -> str:
    """The process's ``/proc/<pid>/comm`` name, or "" when unreadable.

    The kernel truncates comm to 15 bytes, so callers match prefixes rather
    than full argv words. A vanished pid yields "" rather than a guess.
    """
    if not pid:
        return ""
    try:
        return Path("/proc", pid, "comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def is_tmux_server(pid: str) -> bool:
    """Whether the pid is a tmux server process, by its own comm and cmdline.

    The server's comm is "tmux: server", so pgrep -x misses it and pgrep -f
    over-matches non-tmux wrappers whose arguments name a tmux session (rnsh
    wrapping `tmux new-session -A`). Both checks live here so callers never
    parse comm or cmdline inline.
    """
    if not pid:
        return False
    # Comm "tmux: server" names the server; "tmux: client" (an attach) and
    # wrappers (rnsh) are not servers even when their argv names new-session.
    if read_process_comm(pid) != "tmux: server":
        return False
    return "new-session" in read_pane_cmdline(pid)

def read_oom_score_adj(pid: str) -> str | None:
    """The process's ``/proc/<pid>/oom_score_adj`` value, stripped, or None.

    The kernel terminates the value with a newline; callers compare the
    stripped text so a shielded server reads exactly "-900". Unreadable pids
    yield None rather than a guess.
    """
    if not pid:
        return None
    try:
        return Path("/proc", pid, "oom_score_adj").read_text(
            encoding="utf-8", errors="replace").strip()
    except OSError:
        return None

def pane_root(component: str) -> str:
    """The import root a PYTHONPATH component names, without its ``src`` leaf.

    A release publishes ``<root>/src`` and a checkout the same leaf, while a
    pin names the root itself, so both dialects compare equal only after this
    normalization.
    """
    return component[:-4] if component.endswith("/src") else component
def pane_field(raw: bytes | str) -> str:
    """Decode one tmux format-field value; empty is valid for absent fields."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("tmux pane field is not UTF-8") from exc
    else:
        text = raw
    fields = text.split()
    if not fields:
        return ""
    if len(fields) != 1:
        raise ValueError("tmux pane field is not one value")
    return fields[0]


def launch_argv(raw: bytes | str) -> list[str]:
    """Decode tmux's pane_start_command as argv; malformed quoting is unknown."""
    import shlex

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("tmux launch command is not UTF-8") from exc
    else:
        text = raw
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise ValueError("tmux launch command has invalid quoting") from exc
    if not argv:
        raise ValueError("tmux launch command is empty")
    return argv
