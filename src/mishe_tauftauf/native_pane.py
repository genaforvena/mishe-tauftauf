"""Native pane input: bracketed paste plus Enter, or one unframed line.

A tmux ``paste-buffer -p`` is framed only while DEC mode 2004 is enabled.
TTY reads own that mode and temporarily bypass canonical line editing, whose
4096-byte line cap otherwise silently discards long wake instructions. CR still
maps to LF, matching the canonical input seen by existing callers. Pipes must
supply the same frames and LF submit for multiline prompts; unframed lines are
separate inputs. The bracketed-paste end sequence is reserved transport syntax,
not payload. No canonical editing or signal/flow-control bytes act inside a
paste; leading C-u clears the idle editor before either kind of submission.
"""
from __future__ import annotations

import os
import termios
from contextlib import contextmanager


_START = '\x1b[200~'
_END = '\x1b[201~'


@contextmanager
def input_mode(stream):
    """Prepare the input tty before a caller publishes its readiness gate.

    No-op for pipes. Native readers also enter this context themselves; an outer
    context can therefore close the Ready-before-negotiation race without
    changing read_pane_prompt's API. Exit restores the prior termios settings
    and disables bracketed paste, including on malformed/oversized input.
    """
    if not stream.isatty():
        yield False
        return
    fd = stream.fileno()
    previous = termios.tcgetattr(fd)
    current = termios.tcgetattr(fd)
    current[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK |
                    termios.ISTRIP | termios.INLCR | termios.IGNCR |
                    termios.IXON | termios.IXOFF)
    current[0] |= termios.ICRNL
    current[2] &= ~(termios.CSIZE | termios.PARENB)
    current[2] |= termios.CS8
    current[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON |
                    termios.ISIG | termios.IEXTEN)
    current[6][termios.VMIN] = 1
    current[6][termios.VTIME] = 0
    # stdin is commonly read-only; write mode negotiation to that same tty,
    # never an unrelated controlling terminal or redirected stdout.
    output = os.open(os.ttyname(fd), os.O_WRONLY | os.O_NOCTTY)
    try:
        termios.tcsetattr(fd, termios.TCSANOW, current)
        os.write(output, b'\x1b[?2004h')
        yield True
    finally:
        try:
            os.write(output, b'\x1b[?2004l')
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, previous)
            finally:
                os.close(output)


def read_prompt(stream, *, limit: int, on_ready=None) -> str:
    """Read a complete submission without timing or fd/read-ahead assumptions.

    The byte limit covers encoded payload and its submitting newline, not frame
    markers. EOF before any unframed input returns ''; a blank submit returns
    '\\n'. An incomplete frame or invalid post-frame submit fails visibly with
    the received input in the exception rather than authorizing partial work.
    Oversize errors are fatal to this input stream: callers must not continue
    treating its unread remainder as another submission.
    on_ready runs only after framing and termios are prepared, before reading.
    Readiness owners must use this boundary rather than nest input_mode.
    """
    with input_mode(stream) as terminal:
        if on_ready is not None:
            on_ready()
        return _read_submission(stream, terminal=terminal, limit=limit)


def _read_submission(stream, *, terminal: bool, limit: int) -> str:
    encoding = getattr(stream, 'encoding', None) or 'utf-8'
    errors = getattr(stream, 'errors', None) or 'strict'
    payload = []
    size = 0

    def append(char):
        nonlocal size
        size += len(char.encode(encoding, errors))
        if size > limit:
            raise ValueError(f'pane prompt exceeded {limit} bytes')
        payload.append(char)

    first = stream.read(1)
    # seed._send clears the idle editor before both pastes and /clear. In
    # noncanonical mode C-u is visible; consume it only before the submission.
    cleared = 0
    if terminal:
        while first == '\x15':
            cleared += 1
            if cleared > limit:
                raise ValueError(f'pane prompt exceeded {limit} bytes')
            first = stream.read(1)
    prefix = first
    if first == _START[0]:
        while len(prefix) < len(_START) and _START.startswith(prefix):
            char = stream.read(1)
            if not char:
                break
            prefix += char
    framed = prefix == _START
    if framed:
        pending = ''
        while pending != _END:
            char = stream.read(1)
            if not char:
                received = ''.join(payload) + pending
                raise ValueError(f'incomplete pane paste: {received!r}')
            pending += char
            while pending and not _END.startswith(pending):
                append(pending[0])
                pending = pending[1:]
        submit = stream.read(1)
        if submit != '\n':
            received = ''.join(payload) + _END + submit
            raise ValueError(f'pane paste missing submitting Enter: {received!r}')
        append(submit)
    else:
        # Never select the fd behind a buffered TextIO stream. read(1) consumes
        # its existing decoder/read-ahead state, including subsequent prompts.
        if terminal and prefix == '\x04':
            return ''
        for char in prefix:
            append(char)
        while prefix and payload[-1] != '\n':
            char = stream.read(1)
            if not char:
                break
            append(char)
    return ''.join(payload)
