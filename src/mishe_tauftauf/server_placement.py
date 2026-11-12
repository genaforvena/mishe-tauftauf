"""Server placement sense: is each live tmux server inside its intended slice?

Replaces the legacy ``sense.host.server-slice`` reading, which named PIDs that no
longer exist. The live server for each session is found through its tmux socket
(``tmux -S <socket> display-message -p '#{pid}'``), so the reading follows the
server that is running now, not a remembered PID. Its cgroup is then read from
``/proc/<pid>/cgroup`` and checked for the expected slice as a path component.

Verdicts: ``verified`` when the server sits under the expected slice; ``drift``
when it runs outside that slice (a real placement fault); ``unknown`` when the
socket, the session or the cgroup line cannot be read. Absence is never calm.
Observation only: this never moves a process or changes a slice.
"""
from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path
from typing import Callable

SENSE_ID = "sense.host.server-slice"

# (session name, expected slice) pairs. The self-development session's server is
# the one the legacy reading placed in mishe-plants.slice.
EXPECTED = (("mishe-self-development-current", "mishe-plants.slice"),)

Runner = Callable[[list[str]], str | None]


def placement_verdict(session: str, pid: int | None, cgroup_text: str | None, slice_name: str) -> dict:
    """Pure verdict for one server: its PID, its ``/proc/<pid>/cgroup`` text, the expected slice."""
    if pid is None:
        return {"state": "unknown", "session": session, "reason": "no live tmux server for session"}
    if cgroup_text is None:
        return {"state": "unknown", "session": session, "pid": pid, "reason": "cgroup unreadable"}
    path = _unified_path(cgroup_text)
    if path is None:
        return {"state": "unknown", "session": session, "pid": pid, "reason": "no unified cgroup line"}
    components = path.split("/")
    if slice_name in components:
        return {"state": "verified", "session": session, "pid": pid, "slice": slice_name, "cgroup": path}
    return {"state": "drift", "session": session, "pid": pid, "slice": slice_name, "cgroup": path,
            "reason": f"server outside {slice_name}"}


def _unified_path(cgroup_text: str) -> str | None:
    """The cgroup v2 path (the ``0::`` line), or None when the file has no unified entry."""
    for line in cgroup_text.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0" and parts[1] == "":
            return parts[2]
    return None


def _tmux_pid(socket: str, session: str, runner: Runner) -> int | None:
    """Live server PID for ``session`` on ``socket``, or None when the session is absent."""
    if runner(["tmux", "-S", socket, "has-session", "-t", session]) is None:
        return None
    out = runner(["tmux", "-S", socket, "display-message", "-p", "#{pid}"])
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _default_runner(argv: list[str]) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _sockets(uid: int) -> list[str]:
    return sorted(glob.glob(f"/tmp/tmux-{uid}/*/tmux-{uid}/default"))


def read_server_placement(uid: int | None = None, proc_root: Path = Path("/proc"),
                          runner: Runner = _default_runner,
                          expected: tuple = EXPECTED) -> list[dict]:
    """One ``sense.host.server-slice`` reading covering every expected session."""
    uid = os.getuid() if uid is None else uid
    sockets = _sockets(uid)
    parts: list[str] = []
    states: list[str] = []
    for session, slice_name in expected:
        pid = None
        for socket in sockets:
            pid = _tmux_pid(socket, session, runner)
            if pid is not None:
                break
        cgroup_text = None
        if pid is not None:
            try:
                cgroup_text = (proc_root / str(pid) / "cgroup").read_text(encoding="utf-8")
            except OSError:
                cgroup_text = None
        verdict = placement_verdict(session, pid, cgroup_text, slice_name)
        states.append(verdict["state"])
        if verdict["state"] == "verified":
            parts.append(f"{session}@{verdict['pid']}={slice_name}")
        elif verdict["state"] == "drift":
            parts.append(f"{session}@{verdict['pid']} outside {slice_name} ({verdict['cgroup']})")
        else:
            parts.append(f"{session} unknown: {verdict['reason']}")
    if states and all(state == "verified" for state in states):
        state, sample = "verified", "servers placed correctly: " + ", ".join(parts)
    elif "drift" in states:
        state, sample = "drift", "servers misplaced: " + "; ".join(parts)
    else:
        state, sample = "unknown", "server placement not measured: " + "; ".join(parts)
    return [{"id": SENSE_ID, "state": state, "sample": sample, "kind": "read"}]
