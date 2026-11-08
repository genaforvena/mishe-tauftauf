"""Run an engine command to completion within a wall-clock bound.

The engine gets its own process group; on timeout the whole group is killed, so
no descendant outlives the wake. A command that cannot be executed never
started and has no effect; a timeout leaves the effect unknown.
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from .wake import EngineNotStarted, EngineTimeout


def _kill_group(process: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2 if sig == signal.SIGTERM else 5)
            if sig == signal.SIGTERM:
                continue        # leader exited; still sweep stragglers with KILL
            return
        except subprocess.TimeoutExpired:
            continue


def _descendants(root: int) -> set[int]:
    """Live descendants of ``root`` by parent-pid links, regardless of session.

    A child that calls ``setsid`` leaves the engine's process group, so a group
    kill alone does not reach it; its parent link still does. Collect these
    while the root is alive: once the root dies, orphans reparent away.
    """
    children: dict[int, list[int]] = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/stat", "rb") as handle:
                stat = handle.read().decode("ascii", "replace")
        except OSError:
            continue
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        children.setdefault(ppid, []).append(int(entry.name))
    found: set[int] = set()
    frontier = [root]
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in found:
                found.add(child)
                frontier.append(child)
    return found


def _kill_pids(pids: set[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue


def run_bounded(command: list[str], *, env: dict, cwd: Path, timeout: float,
                stdin_from: Path | None = None, stdout_to: Path | None = None, on_start=None) -> int:
    stdin = open(stdin_from, "rb") if stdin_from else subprocess.DEVNULL
    stdout = open(stdout_to, "wb") if stdout_to else subprocess.DEVNULL
    try:
        try:
            process = subprocess.Popen(command, env=env, cwd=cwd, stdin=stdin, stdout=stdout,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            raise EngineNotStarted(f"{command[0]}: {exc}") from exc
        if on_start is not None:
            on_start(process.pid)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            escaped = _descendants(process.pid)
            _kill_group(process)
            _kill_pids(escaped)
            raise EngineTimeout(f"exceeded {timeout:g}s") from exc
        finally:
            _kill_group(process) if process.poll() is None else None
    finally:
        for handle in (stdin, stdout):
            if hasattr(handle, "close"):
                handle.close()
