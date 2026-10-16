"""A readable, rendering-only view of native caller events and decoded effects."""
from __future__ import annotations

import json
import os
import termios
import unicodedata
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def quiet_input(stream):
    """Suppress TTY input echo, without changing input delivery or interpretation."""
    if not stream.isatty():
        yield stream
        return
    fd = stream.fileno()
    original = termios.tcgetattr(fd)
    quiet = original.copy()
    quiet[3] &= ~termios.ECHO
    try:
        termios.tcsetattr(fd, termios.TCSANOW, quiet)
        yield stream
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)


class _StoryOutput:
    def __init__(self, terminal, transcript):
        self.terminal = terminal
        self.transcript = transcript

    def write(self, text):
        written = self.transcript.write(text)
        self.terminal.write(text)
        return written

    def flush(self):
        self.transcript.flush()
        self.terminal.flush()

    def isatty(self):
        return self.terminal.isatty()

    def fileno(self):
        return self.terminal.fileno()


@contextmanager
def story_output(terminal, path):
    """Append readable output across process/context resets; never truncate it."""
    with Path(path).open('a', encoding='utf-8', buffering=1) as transcript:
        yield _StoryOutput(terminal, transcript)


def retain_scrollback(stream):
    """Move an append-only TTY's last screen into history before tmux respawns it.

    tmux retains history on respawn, but discards the visible screen. Padding is
    terminal-only, not part of the readable transcript. This does not reset the
    model, send input or change terminal modes; call before releasing readiness.
    """
    stream.flush()
    if stream.isatty():
        fd = stream.fileno()
        padding = b'\n' * os.get_terminal_size(fd).lines
        while padding:
            padding = padding[os.write(fd, padding):]


def _safe(text):
    # ESC, C1 controls, carriage returns and bidi formatting must never act on
    # the terminal. Keep ordinary Unicode, newlines and tabs readable.
    return ''.join(
        (f'\\x{ord(char):02x}' if ord(char) <= 255 else f'\\u{ord(char):04x}')
        if char not in '\n\t' and unicodedata.category(char) in ('Cc', 'Cf', 'Cs')
        else char
        for char in str(text)
    )


def _text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


class NativeStory:
    """Render only visible narrative and explicitly decoded dispatch outcomes.

    The caller journals events before passing them here, and wraps dispatch with
    start/result/failure. Serialized tool-result messages are deliberately ignored.
    No rendering decision authorizes execution or infers settlement from prose.
    """

    def __init__(self, stream, *, role, wake, evidence_dir, max_chars=8000):
        if type(max_chars) is not int or max_chars <= 0:
            raise ValueError('positive integer text limit required')
        self.stream = stream
        self.role = role
        self.wake = wake
        self.evidence_dir = Path(evidence_dir).absolute()
        self.max_chars = max_chars
        self._settled = False
        self._line('Wake started')
        self._body(f'Evidence: {self.evidence_dir}')
        self.stream.flush()

    def _line(self, text):
        self.stream.write(_safe(f'[native {self.role} wake={self.wake}] {text}') + '\n')

    def _body(self, value, *, evidence=None):
        text = _safe(_text(value))
        if len(text) > self.max_chars:
            self.stream.write(text[:self.max_chars] + '\n')
            location = evidence or self.evidence_dir / 'journal.jsonl'
            self.stream.write(_safe(
                f'[truncated: {len(text) - self.max_chars} rendered characters omitted; '
                f'full evidence: {location}]') + '\n')
        else:
            self.stream.write(text + '\n')

    def _action(self, call):
        name = call.get('name')
        args = call.get('arguments', {})
        if not isinstance(args, Mapping):
            return f'Tool {name}; arguments: {_text(args)}'
        if name == 'read_file':
            start, count = args.get('start', 1), args.get('count', 160)
            end = start + count - 1 if type(start) is int and type(count) is int else '?'
            return f'Read {args.get("path", "?")} lines {start}-{end}'
        if name == 'pain_read':
            return f'Read {self.role} dashboard'
        if name == 'list_files':
            return f'List files in {args.get("path", "?")}'
        if name == 'exec':
            return f'Run command:\n{args.get("cmd", "?")}'
        if name == 'write_note':
            return f'Write evidence note {args.get("name", "?")}'
        if name == 'wall_dm':
            return f'Write wall message to {args.get("to", "?")}'
        if name == 'wall_write':
            return f'Write wall for {self.role}'
        if name == 'seed_yield':
            return (f'Settle wake {self.wake}: result={args.get("result", "?")}; '
                    f'continue_work={_text(args.get("continue_work", False))}')
        return f'Tool {name}; arguments:\n{_text(args)}'

    def event(self, event):
        kind = event.get('kind')
        if kind == 'model_input':
            self._line(f'Turn {event.get("turn", "?")} — waiting for model')
        elif kind == 'model_output':
            self._line(f'Turn {event.get("turn", "?")} — assistant')
            response = event.get('response', {})
            assistant = response.get('assistant', {})
            if assistant.get('role') == 'assistant':
                for item in assistant.get('content', []):
                    if item.get('type') == 'text':
                        self._body(item.get('text', ''))
            if response.get('terminal') != 'done':
                self._line('UNKNOWN — model did not return a normal terminal')
                self._body(response.get('terminal'))
                if assistant.get('errorMessage') is not None:
                    self._line('Provider error:')
                    self._body(assistant['errorMessage'])
        elif kind == 'proposal':
            self._line('Proposed action — not executed')
            self._body(self._action(event['call']))
        elif kind == 'model_failure':
            self._line('FAILED — model turn; effect/completion state UNKNOWN')
            self._body(event.get('error'))
        elif kind == 'stop':
            self._line('Loop stopped — not itself proof of wake settlement')
            self._body(f'status={event.get("status", "unknown")}; '
                       f'turns={event.get("turns", "?")}; calls={event.get("calls", "?")}')
            self._line('Canonical wake settlement: '
                       + ('observed' if self._settled else 'not observed'))
        else:
            # Checkpoints/model inputs contain private context; tool_result has
            # opaque serialized prose. Only result() accepts decoded outcomes.
            return
        self.stream.flush()

    def start(self, call):
        self._line('Starting action — completion not yet known')
        self._body(self._action(call))
        args = call.get('arguments', {})
        if call.get('name') in ('wall_write', 'wall_dm', 'write_note') and isinstance(args, Mapping):
            self._body(args.get('content', ''))
        self.stream.flush()

    def result(self, call, outcome):
        outer = outcome if isinstance(outcome, Mapping) else {}
        inner = outer.get('result')
        inner = inner if isinstance(inner, Mapping) else {}
        actual = inner.get('result')
        completed = outer.get('status') == 'completed' and inner.get('status') == 'completed'
        command = call.get('name') == 'exec' and isinstance(actual, Mapping)
        if not completed:
            self._line('UNKNOWN — effect not confirmed completed')
            self._body({'effect_status': outer.get('status', 'unknown'),
                        'tool_status': inner.get('status', 'unknown')})
        elif call.get('name') == 'exec':
            code = actual.get('returncode') if command else None
            if type(code) is not int:
                self._line('UNKNOWN — command exit status unavailable; durable effect completed')
            elif code != 0:
                self._line(f'FAILED — command exit {code}; durable effect completed')
            else:
                self._line('Completed command — exit 0')
        elif call.get('name') == 'seed_yield':
            self._settled = True
            self._line('Canonical wake settlement observed — completed seed_yield effect')
        else:
            self._line('Completed effect')
        for source in (outer, inner):
            if source.get('failure') is not None:
                self._line('Failure detail:')
                self._body(source['failure'])
        if outer.get('reconciliation_ref') is not None:
            self._line('Reconciliation evidence:')
            self._body(outer['reconciliation_ref'])
        if command:
            prefix = actual.get('full_output_prefix')
            prefix = Path(prefix).absolute() if isinstance(prefix, str) and prefix else None
            for channel in ('stdout', 'stderr'):
                self._line(channel + ':')
                self._body(actual.get(channel, ''),
                           evidence=f'{prefix}.{channel}' if prefix else None)
            if actual.get('truncated'):
                self._line('Tool output truncated before rendering')
                if prefix:
                    self._line(f'Full stdout: {prefix}.stdout')
                    self._line(f'Full stderr: {prefix}.stderr')
                else:
                    self._line(f'Full evidence: {self.evidence_dir}')
        else:
            self._line('Result:')
            self._body(actual if 'result' in inner else outer.get('result'))
        self.stream.flush()

    def failure(self, call, error):
        self._line('FAILED — dispatch raised; effect state UNKNOWN; no completion inferred')
        self._body(self._action(call))
        self._body(f'{type(error).__name__}: {error}')
        self.stream.flush()
