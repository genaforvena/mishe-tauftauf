import io
import os
import pty
import queue
import select
import termios
import threading
from contextlib import contextmanager

import pytest

from mishe_tauftauf.inference_loop import read_pane_prompt


START = '\x1b[200~'
END = '\x1b[201~'


def framed(text):
    return START + text + END + '\n'


@contextmanager
def pipe_input(text):
    reader, writer = os.pipe()
    try:
        os.write(writer, text.encode('utf-8'))
        with io.open(reader, 'r', encoding='utf-8', newline='') as stream:
            yield stream
    finally:
        os.close(writer)


def test_open_pipe_preserves_buffered_subsequent_prompts():
    with pipe_input('first\nsecond\n' + framed('third\nfourth')) as stream:
        assert read_pane_prompt(stream) == 'first\n'
        assert read_pane_prompt(stream) == 'second\n'
        assert read_pane_prompt(stream) == 'third\nfourth\n'


@pytest.mark.parametrize('text,expected', [
    ('', ['', '']),
    ('\n', ['\n', '']),
    ('partial', ['partial', '']),
    ('first\n\nlast', ['first\n', '\n', 'last', '']),
    (framed('first\n\nlast') + framed('next'), ['first\n\nlast\n', 'next\n', '']),
    ('\x1b[not-a-frame\r\n', ['\x1b[not-a-frame\r\n', '']),
])
def test_eof_blank_and_subsequent_submissions(text, expected):
    stream = io.StringIO(text)
    assert [read_pane_prompt(stream) for _ in expected] == expected


@pytest.mark.parametrize('wrap', [lambda text: text + '\n', framed])
def test_limit_counts_encoded_bytes_and_submission_not_frame_markers(wrap):
    text = wrap('é' * 4)
    assert read_pane_prompt(io.StringIO(text), limit=9) == 'é' * 4 + '\n'
    with pytest.raises(ValueError, match='exceeded 8 bytes'):
        read_pane_prompt(io.StringIO(text), limit=8)


@pytest.mark.parametrize('text', [
    START + 'keep this evidence',
    START + 'keep this evidence' + END,
    START + 'keep this evidence' + END + 'not Enter\n',
])
def test_incomplete_or_invalid_frames_fail_with_received_evidence(text):
    with pytest.raises(ValueError, match='keep this evidence'):
        read_pane_prompt(io.StringIO(text))


def test_payload_control_bytes_and_partial_marker_prefixes_are_opaque():
    message = 'tabs\tNUL\x00interrupt\x03clear\x15flow\x11\x13\x1b[201x\x1b[20'
    assert read_pane_prompt(io.StringIO(framed(message))) == message + '\n'


@contextmanager
def tty_input():
    master, slave = pty.openpty()
    stream = io.open(slave, 'r', encoding='utf-8', newline='')
    workers = []
    previous = termios.tcgetattr(slave)

    def start(count=1, **kwargs):
        results = queue.Queue()

        def consume():
            try:
                for _ in range(count):
                    results.put(read_pane_prompt(stream, **kwargs))
            except BaseException as exc:
                results.put(exc)

        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        workers.append(worker)
        return results

    def enabled():
        output = b''
        while b'\x1b[?2004h' not in output:
            ready, _, _ = select.select([master], [], [], 3)
            assert ready, 'reader did not enable bracketed paste'
            output += os.read(master, 8192)

    try:
        yield master, slave, start, enabled, previous
    finally:
        os.close(master)
        for worker in workers:
            worker.join(timeout=3)
            assert not worker.is_alive(), 'reader remained blocked after terminal close'
        stream.close()


def test_canonical_tty_waits_for_late_enter_and_next_real_prompt():
    with tty_input() as (master, slave, start, enabled, previous):
        assert previous[3] & termios.ICANON
        results = start(count=2)
        enabled()
        message = 'Handoff\rWAKE 42\rOBLIGATION\rfinal instruction'
        os.write(master, b'\x15' + (START + message + END).encode())
        # Longer than the compatibility idle argument: neither internal lines
        # nor the end marker itself can submit work before the later Enter.
        with pytest.raises(queue.Empty):
            results.get(timeout=0.1)
        os.write(master, b'\r')
        assert results.get(timeout=3) == message.replace('\r', '\n') + '\n'
        enabled()
        with pytest.raises(queue.Empty):
            results.get(timeout=0.1)
        os.write(master, b'\x15' + framed('next real\nprompt').encode())
        assert results.get(timeout=3) == 'next real\nprompt\n'
        # Input-mode restoration precedes publication of each result.
        assert termios.tcgetattr(slave) == previous


def test_tty_long_line_preserves_literal_controls_and_restores_mode():
    with tty_input() as (master, slave, start, enabled, previous):
        results = start()
        enabled()
        message = 'é' * 5000 + '\t\x00\x03\x15\x11\x13\x1b[201x'
        encoded = (START + message + END + '\r').encode()
        offset = 0
        while offset < len(encoded):
            offset += os.write(master, encoded[offset:])
        assert results.get(timeout=3) == message + '\n'
        # Mode restoration follows the return but precedes queue publication.
        assert termios.tcgetattr(slave) == previous


def test_tty_oversize_rejection_restores_mode():
    with tty_input() as (master, slave, start, enabled, previous):
        results = start(limit=8)
        enabled()
        os.write(master, framed('é' * 4).encode())
        error = results.get(timeout=3)
        assert isinstance(error, ValueError)
        assert 'exceeded 8 bytes' in str(error)
        assert termios.tcgetattr(slave) == previous


def test_tty_editor_clear_prefix_preserves_single_line_and_next_paste():
    with tty_input() as (master, slave, start, enabled, previous):
        results = start(count=2)
        enabled()
        os.write(master, b'\x15/clear\r')
        assert results.get(timeout=3) == '/clear\n'
        enabled()
        os.write(master, b'\x15' + framed('next\nactual wake').encode())
        assert results.get(timeout=3) == 'next\nactual wake\n'
        assert termios.tcgetattr(slave) == previous


def test_every_readiness_publication_follows_terminal_framing():
    with tty_input() as (master, slave, start, enabled, previous):
        results = start(count=2, on_ready=lambda: os.write(slave, b'READY\n'))
        for message in ('first\nwake', 'second\nwake'):
            output = b''
            while b'READY' not in output:
                readable, _, _ = select.select([master], [], [], 3)
                assert readable, 'readiness was not published'
                output += os.read(master, 8192)
            assert output.rfind(b'\x1b[?2004h') > output.rfind(b'\x1b[?2004l')
            assert output.index(b'READY') > output.rfind(b'\x1b[?2004h')
            os.write(master, b'\x15' + framed(message).encode())
            assert results.get(timeout=3) == message + '\n'
        assert termios.tcgetattr(slave) == previous
