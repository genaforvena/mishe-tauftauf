"""One-turn subprocess boundary; provider bootstrap and durable effects live elsewhere.

Workers read one JSON request from stdin and emit exactly one JSON object after
native stream termination: {terminal, assistant, wire_usage}. Diagnostics go to
stderr. A retained proposal is history, not permission to dispatch it.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import math
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class WorkerError(RuntimeError):
    """No authoritative worker completion was received."""


@dataclass(frozen=True)
class WorkerTurn:
    terminal: str
    assistant: dict[str, Any]
    wire_usage: Any = None  # None means UNKNOWN, not native synthesized zero.

    def dispatch(self, executor: Callable[[dict[str, Any]], Any], *,
                 cancelled: Callable[[], bool]) -> list[Any]:
        """Invoke the caller's authority/durability boundary, never a replay policy.

        Cancellation cannot undo an effect already committed by executor. Each
        subsequent proposal checks current cancellation again.
        """
        if self.terminal != "done" or self.assistant.get("stopReason") != "toolUse":
            return []
        results = []
        for call in self.assistant["content"]:
            if call.get("type") != "toolCall":
                continue
            if cancelled():
                break
            results.append(executor(call))
        return results


def _decode(payload: bytes) -> WorkerTurn:
    try:
        frame = json.loads(payload)
        assistant = frame["assistant"]
        terminal = frame["terminal"]
        if terminal not in ("done", "error") or not isinstance(assistant, dict):
            raise ValueError("invalid terminal or assistant")
        content = assistant["content"]
        if not isinstance(content, list) or not all(isinstance(c, dict) for c in content):
            raise ValueError("invalid assistant content")
        ids = set()
        for call in content:
            if call.get("type") != "toolCall":
                continue
            if (not isinstance(call.get("id"), str) or not call["id"]
                    or not isinstance(call.get("name"), str) or not call["name"]
                    or not isinstance(call.get("arguments"), dict) or call["id"] in ids):
                raise ValueError("invalid or duplicate tool call")
            ids.add(call["id"])
        if not isinstance(assistant.get("stopReason"), str):
            raise ValueError("missing stop reason")
        return WorkerTurn(terminal, assistant, frame.get("wire_usage"))
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise WorkerError(f"invalid worker completion: {exc}") from exc


def run_worker(command: Sequence[str], request: Mapping[str, Any], *,
               cancelled: Callable[[], bool], timeout: float = 60,
               max_output: int = 4 * 1024 * 1024) -> WorkerTurn:
    """Bound total runtime/output and reap the experiment-owned process group.

    Nonzero exit, EOF without a completion, malformed output and cancellation
    are failures even if stdout contains a successful-looking frame.
    """
    if (not math.isfinite(timeout) or timeout <= 0
            or not isinstance(max_output, int) or isinstance(max_output, bool) or max_output <= 0):
        raise ValueError("finite positive runtime and positive integer output limits required")
    if cancelled():
        raise WorkerError("cancelled before worker start")
    data = memoryview(json.dumps(request, ensure_ascii=False).encode() + b"\n")
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    stdout = bytearray()
    stderr = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            for stream, event, name in ((process.stdin, selectors.EVENT_WRITE, "input"),
                                        (process.stdout, selectors.EVENT_READ, "output"),
                                        (process.stderr, selectors.EVENT_READ, "error")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event, name)
            while selector.get_map() or process.poll() is None:
                if cancelled():
                    raise WorkerError("cancelled during worker turn")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WorkerError("worker total time limit exceeded")
                for key, _ in selector.select(min(remaining, 0.05)):
                    stream = key.fileobj
                    if key.data == "input":
                        try:
                            sent = os.write(stream.fileno(), data)
                        except BrokenPipeError:
                            sent = len(data)
                        data = data[sent:]
                        if not data:
                            selector.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(stream.fileno(), 65536)
                        if not chunk:
                            selector.unregister(stream)
                            continue
                        (stdout if key.data == "output" else stderr).extend(chunk)
                        if len(stdout) + len(stderr) > max_output:
                            raise WorkerError("worker output limit exceeded")
            if process.returncode != 0:
                raise WorkerError(f"worker exited {process.returncode}: "
                                  + stderr.decode(errors="replace"))
            if cancelled():
                raise WorkerError("cancelled after worker completion")
            return _decode(bytes(stdout))
    finally:
        # Descendants can retain pipes after the parent exits; kill the isolated
        # group on success as well as failure. Never touch resident processes.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
