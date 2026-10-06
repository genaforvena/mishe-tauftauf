"""Experimental persistent native IPC; no effect authority or automatic retry."""
from __future__ import annotations

import json
import math
import os
import selectors
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from mishe_tauftauf.inference_worker import WorkerError, WorkerTurn, _decode


@dataclass(frozen=True)
class NativeTurn(WorkerTurn):
    wire_terminal: Any = None
    checkpoint: Any = None


class NativeSession:
    """One owner, sequential turns, lifetime deadline and combined output bound.

    A failed exchange kills/reaps this session; callers must reconcile effects
    before any new session. close() requires the disposal receipt AND clean exit.
    Earlier turns cannot prove that a later close will succeed.
    """

    def __init__(self, command, selector, session_id, *, cancelled=lambda: False,
                 timeout=60, max_output=4 * 1024 * 1024, env=None, cwd=None,
                 checkpoint=None):
        if (not math.isfinite(timeout) or timeout <= 0 or
                type(max_output) is not int or max_output <= 0):
            raise ValueError('finite positive timeout and positive integer output bound required')
        if cancelled():
            raise WorkerError('cancelled before session start')
        self.cancelled = cancelled
        self.deadline = time.monotonic() + timeout
        self.max_output = max_output
        self.output_size = 0
        self.pending = bytearray()
        self.errors = bytearray()
        self.lock = threading.Lock()
        self.closed = False
        self.next_id = 1
        self.checkpoint = checkpoint
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True,
                                        env=env, cwd=cwd)
        self.selector = selectors.DefaultSelector()
        try:
            for stream, name in ((self.process.stdout, 'output'), (self.process.stderr, 'error')):
                os.set_blocking(stream.fileno(), False)
                self.selector.register(stream, selectors.EVENT_READ, name)
            os.set_blocking(self.process.stdin.fileno(), False)
            if self._exchange({'type': 'open', 'selector': selector, 'sessionId': session_id}) != {'type': 'ready'}:
                raise WorkerError('expected ready receipt')
        except BaseException:
            self._reap()
            raise

    def _check(self):
        remaining = self.deadline - time.monotonic()
        reason = None
        if self.cancelled():
            reason = 'cancelled during native session'
        elif remaining <= 0:
            reason = 'native session total time limit exceeded'
        if reason is not None:
            # Already buffered diagnostics must not extend the deadline to drain.
            if self.errors:
                tail = self.errors[-4096:].decode(errors='replace')
                marker = '[truncated] ' if len(self.errors) > 4096 else ''
                reason += ': stderr: ' + marker + tail
            raise WorkerError(reason)
        return min(remaining, .05)

    def _pump(self, data):
        for key, _ in self.selector.select(self._check()):
            stream = key.fileobj
            if key.data == 'input':
                try:
                    count = os.write(stream.fileno(), data)
                except BrokenPipeError as exc:
                    raise WorkerError('native session input closed') from exc
                data = data[count:]
                if not data:
                    self.selector.unregister(stream)
            else:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    self.selector.unregister(stream)
                    continue
                self.output_size += len(chunk)
                if self.output_size > self.max_output:
                    raise WorkerError('native session output limit exceeded')
                (self.pending if key.data == 'output' else self.errors).extend(chunk)
        self._check()
        return data

    def _exchange(self, request):
        data = memoryview((json.dumps(request, ensure_ascii=False) + '\n').encode())
        if len(data) > 4 * 1024 * 1024:
            raise WorkerError('native request too large')
        if self.pending:
            raise WorkerError('unsolicited native output')
        self.selector.register(self.process.stdin, selectors.EVENT_WRITE, 'input')
        while data or b'\n' not in self.pending:
            data = self._pump(data)
            ended = self.process.poll() is not None
            eof = self.process.stdout not in [key.fileobj for key in self.selector.get_map().values()]
            if b'\n' not in self.pending and (ended or eof):
                # stdout EOF can precede stderr delivery and process exit.
                # Drain refusal evidence under the original lifetime deadline.
                if self.process.stdin in [key.fileobj for key in self.selector.get_map().values()]:
                    self.selector.unregister(self.process.stdin)
                while self.selector.get_map() or self.process.poll() is None:
                    self._pump(memoryview(b''))
                raise WorkerError(f'native session exited {self.process.returncode}: ' + self.errors.decode(errors='replace'))
        line, _, rest = self.pending.partition(b'\n')
        self.pending[:] = rest
        if rest:
            raise WorkerError('extra native completion')
        try:
            frame = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise WorkerError('malformed native receipt') from exc
        if not isinstance(frame, dict):
            raise WorkerError('native receipt must be an object')
        return frame

    def turn(self, context):
        if not self.lock.acquire(blocking=False):
            raise WorkerError('concurrent native session call')
        try:
            if self.closed:
                raise WorkerError('native session closed')
            request = {'type': 'turn', 'id': self.next_id, 'context': context}
            if self.checkpoint is not None:
                request['checkpoint'] = self.checkpoint
            frame = self._exchange(request)
            if self.process.poll() is not None:
                raise WorkerError('native session exited during turn')
            if frame.get('type') != 'turn' or type(frame.get('id')) is not int or frame['id'] != self.next_id:
                raise WorkerError('mismatched native turn receipt')
            completion = frame.get('frame')
            decoded = _decode(json.dumps(completion).encode())
            checkpoint = completion.get('checkpoint')
            # Error/incomplete frames remain diagnostic history, never recovery
            # evidence. Only a correlated completed response can checkpoint.
            wire = completion.get('wire_terminal')
            if (decoded.terminal == 'done' and isinstance(wire, dict)
                    and wire.get('correlated') is True
                    and wire.get('event') == 'response.completed'
                    and not isinstance(checkpoint, dict)):
                raise WorkerError('UNKNOWN: missing native logical checkpoint')
            self.checkpoint = None
            self.next_id += 1
            return NativeTurn(decoded.terminal, decoded.assistant, decoded.wire_usage,
                              completion.get('wire_terminal'), checkpoint)
        except BaseException:
            self._reap()
            raise
        finally:
            self.lock.release()

    def close(self):
        if not self.lock.acquire(blocking=False):
            raise WorkerError('concurrent native session call')
        try:
            if self.closed:
                return
            if self._exchange({'type': 'close'}) != {'type': 'closed'}:
                raise WorkerError('expected disposal receipt')
            self.process.stdin.close()
            while self.selector.get_map() or self.process.poll() is None:
                self._pump(memoryview(b''))
            if self.process.returncode != 0 or self.pending:
                raise WorkerError('native session failed after disposal receipt: ' + self.errors.decode(errors='replace'))
        finally:
            self._reap()
            self.lock.release()

    def _reap(self):
        self.closed = True
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait()
        self.selector.close()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is not None:
            self._reap()
        else:
            self.close()
