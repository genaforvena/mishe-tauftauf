"""Running a worker as a bounded, isolated child process.

The bound is on the process group, not on the command: a worker that forks
must not leave descendants behind when it is stopped.

A worker that exits non-zero has an unknown effect, not a failed one.  A
non-zero exit says the command did not finish successfully; it says nothing
about whether the command already did its work before it stopped.  Treating
it as a clean failure would let a successor replay work that had already
happened, so it is recorded as ``unknown`` and blocks the next dispatch
until somebody checks what actually occurred.

The one exception is a command that never started: there was no process, so
there was no effect, and that is a plain failure.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Mapping, Sequence

from .interfaces import Dispatch, WorkerResult


class WorkerTimeout(RuntimeError):
    """The worker exceeded its bound.

    Raised only by callers that want an exception; :class:`SubprocessWorker`
    reports the same condition as an unknown outcome, because a stopped
    worker may already have had an effect.
    """


class SubprocessWorker:
    """Run one command per dispatch, in its own process group.

    ``timeout`` bounds the whole run.  When it expires the process group is
    signalled and then killed, and the result says the effect is unknown:
    the command may have done its work before it was stopped.
    """

    def __init__(self, *, timeout: float = 900.0, cwd: str | Path | None = None,
                 env: Mapping[str, str] | None = None, max_output_bytes: int = 65536,
                 stop_grace_seconds: float = 2.0):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.timeout = timeout
        self.cwd = Path(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self.max_output_bytes = max_output_bytes
        self.stop_grace_seconds = stop_grace_seconds

    def run(self, dispatch: Dispatch) -> WorkerResult:
        command = dispatch.payload.get("command")
        if not isinstance(command, (list, tuple)) or not command:
            raise ValueError("dispatch payload must carry a nonempty command list")
        argv = [str(part) for part in command]
        environment = dict(self.env) if self.env is not None else None
        try:
            process = subprocess.Popen(
                argv, start_new_session=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, cwd=self.cwd,
                env=environment)
        except OSError as exc:
            # The command never started, so it never had an effect.
            return WorkerResult(outcome="failed", detail=f"worker could not start: {exc}")
        try:
            output, _ = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._stop_group(process, self.stop_grace_seconds)
            try:
                output, _ = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                output = ""
            # The worker was stopped, so it may already have had its effect.
            # The outcome is unknown, never a clean failure.
            return WorkerResult(
                outcome="unknown",
                detail=(output or "")[-self.max_output_bytes:] +
                       "\nworker exceeded its bound; its effect is unknown")
        code = process.returncode
        tail = (output or "")[-self.max_output_bytes:]
        if code != 0:
            # The command ran and did not finish successfully.  Whether it
            # had an effect is not established, so the outcome is unknown.
            return WorkerResult(
                outcome="unknown",
                detail=tail or f"worker exited {code}")
        return WorkerResult(outcome="completed", detail=tail)

    @staticmethod
    def _stop_group(process: subprocess.Popen, grace: float = 2.0) -> None:
        """Signal the worker's whole group, then insist that it is gone.

        The group leader exiting does not mean the group is empty: a worker
        that forked leaves its children behind.  The group is signalled and
        then checked until nothing in it is running, so a stopped worker
        leaves no live descendant.
        """
        for number, pause in ((signal.SIGTERM, grace), (signal.SIGKILL, max(grace / 2, 0.5))):
            try:
                os.killpg(process.pid, number)
            except (ProcessLookupError, PermissionError):
                return
            deadline = time.monotonic() + pause
            while time.monotonic() < deadline:
                if not _group_has_live_members(process.pid):
                    return
                time.sleep(0.05)
        # The group is signalled twice and still has members.  They are
        # reported by the caller through the unknown outcome; nothing more
        # can be done to a process in another session.


def _group_has_live_members(pgid: int) -> bool:
    """Whether any live process remains in the group.

    A zombie is not running work, so it does not count.  The group is read
    from procfs, because the leader may already have exited and its pid may
    already be gone.
    """
    from .dispatch_record import process_group_members
    return bool(process_group_members(pgid))
