"""Run an engine command to completion within a wall-clock bound.

The engine gets its own process group; on timeout the whole group is killed, so
no descendant outlives the wake. A command that cannot be executed never
started and has no effect; a timeout leaves the effect unknown.

A run may also carry a *progress-aware deadline*: when the soft deadline is
reached and the output file is still growing, the deadline is extended in steps
up to a hard cap, so a wake that is carrying on is not cut mid-work (operator
DM 298902, 2026-11-09). An idle engine (no growth within the idle gap) or the
hard cap ends the run exactly as the plain deadline does. With no hard cap the
behaviour is unchanged.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from . import proc_table
from .wake import EngineNotStarted, EngineTimeout

IDLE_GAP_S = 180.0
"""How long the output may stay unchanged before the engine counts as idle."""
POLL_S = 5.0
"""How often a running engine is checked for output growth."""


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
    children = proc_table.children_by_parent()
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


def _output_size(path: Path | None) -> int | None:
    """Bytes written to the output file so far, or None when there is no file to watch."""
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def run_bounded(command: list[str], *, env: dict, cwd: Path, timeout: float,
                stdin_from: Path | None = None, stdout_to: Path | None = None, on_start=None,
                hard_timeout: float | None = None, idle_gap: float = IDLE_GAP_S) -> int:
    """Run ``command`` under a deadline.

    ``timeout`` is the soft deadline. With ``hard_timeout`` (a total runtime cap) the run may
    continue past it while the output file keeps growing: an engine that is carrying on is not
    cut mid-work. The run ends as a timeout when the output has not grown for ``idle_gap``
    seconds, or when the hard cap is reached. Without ``hard_timeout`` the run is cut exactly
    at the soft deadline, as before.
    """
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
            if hard_timeout is None or hard_timeout <= timeout:
                try:
                    return process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    raise EngineTimeout(f"exceeded {timeout:g}s") from exc
            return _run_with_progress(process, timeout=timeout, hard_timeout=hard_timeout,
                                      watched=stdout_to, idle_gap=idle_gap)
        except EngineTimeout:
            escaped = _descendants(process.pid)
            _kill_group(process)
            _kill_pids(escaped)
            raise
        finally:
            _kill_group(process) if process.poll() is None else None
    finally:
        for handle in (stdin, stdout):
            if hasattr(handle, "close"):
                handle.close()


def _run_with_progress(process: subprocess.Popen, *, timeout: float, hard_timeout: float,
                       watched: Path | None, idle_gap: float) -> int:
    """Wait for the engine, extending the deadline while its output still grows.

    Before the soft deadline the engine runs exactly as it always did. After it, the
    engine may continue only while its output grows: it is cut when the output has not
    grown for ``idle_gap`` seconds, and never past ``hard_timeout``. An idle engine and
    the hard cap both end the run as a timeout.

    The output is sampled every ``POLL_S`` so the last growth is known to within one
    sample, but each bound is enforced at its own moment rather than at the next sample:
    the wait ends exactly at the hard cap, and an idle engine is cut exactly one idle gap
    after its last output. A poll that only decides "still running" can no longer carry
    the run past either bound.
    """
    start = time.monotonic()
    soft_at = start + timeout
    hard_at = start + hard_timeout
    last_size = _output_size(watched)
    last_growth = start
    while True:
        now = time.monotonic()
        if now >= hard_at:
            raise EngineTimeout(f"exceeded {hard_timeout:g}s (hard cap)")
        size = _output_size(watched)
        if size is not None and last_size is not None and size > last_size:
            last_size = size
            last_growth = now
        if now < soft_at:
            boundary = soft_at
        else:
            if size is None or now - last_growth >= idle_gap:
                raise EngineTimeout(f"exceeded {timeout:g}s (no output for {idle_gap:g}s)")
            boundary = last_growth + idle_gap
        target = min(boundary, hard_at)
        # Wake at the earlier of the next boundary and the next sample, so growth is seen
        # promptly and no bound is passed unnoticed.
        wait = min(POLL_S, target - now)
        if wait > 0:
            try:
                return process.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                pass
        else:
            code = process.poll()
            if code is not None:
                return code
        now = time.monotonic()
        size = _output_size(watched)
        if size is not None and last_size is not None and size > last_size:
            last_size = size
            last_growth = now
        if now >= hard_at:
            raise EngineTimeout(f"exceeded {hard_timeout:g}s (hard cap)")
        if now >= soft_at and (size is None or now - last_growth >= idle_gap):
            raise EngineTimeout(f"exceeded {timeout:g}s (no output for {idle_gap:g}s)")
