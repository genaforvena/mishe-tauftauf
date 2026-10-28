"""Running a worker as a bounded, isolated child process.

The bound is on the process group, not on the command: a worker that forks
must not leave descendants behind when it is stopped.  A worker that is
stopped for exceeding its bound leaves its effect unknown, so the caller
sees ``unknown`` rather than a clean failure.
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
                 env: Mapping[str, str] | None = None, max_output_bytes: int = 65536):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.timeout = timeout
        self.cwd = Path(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self.max_output_bytes = max_output_bytes

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
            return WorkerResult(outcome="failed", detail=f"worker could not start: {exc}")
        try:
            output, _ = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._stop_group(process)
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
            return WorkerResult(outcome="failed", detail=tail or f"worker exited {code}")
        return WorkerResult(outcome="completed", detail=tail)

    @staticmethod
    def _stop_group(process: subprocess.Popen) -> None:
        """Signal the worker's own group, then insist."""
        for number, pause in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 1.0)):
            try:
                os.killpg(process.pid, number)
            except (ProcessLookupError, PermissionError):
                return
            deadline = time.monotonic() + pause
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    return
                time.sleep(0.05)
