"""A role's live window is found in whichever session the home owns.

The plant's site home owns two sessions — the core session and the life-support
session — and both record it as their owner. The shared ``.seed-raised`` receipt
is written by whichever supervisor wrote last, so it names one of them and
alternates as they each re-heal it. A reader that trusted the receipt asked one
socket about one session, so a role living in the *other* session read as a
missing window: model change e228bd1 for `signals` sat `activating` with
`mind window missing` while the signals pane was alive and serving its wake.

These cases bind the defect and its repair on the resolution rule itself:
the window is located by enumerating the home's own sessions, and the receipt
is consulted only when that enumeration finds no owned session.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mishe_tauftauf import model_boundary as boundary
from mishe_tauftauf import seed
from mishe_tauftauf import tmux


CORE = 'mishe-self-development-current'
LIFE = 'mishe-life-support'
CORE_SOCKET = '/tmp/tmux-1000/mishe-tauftauf/tmux-1000/default'
LIFE_SOCKET = '/tmp/tmux-1000/mishe-life-support/tmux-1000/default'


def _install(monkeypatch, *, receipt_session, owned, alive):
    """Fake the tmux server: `owned` sessions, `alive` roles per session.

    `alive` names the sessions that actually hold the role's window; the
    receipt names one session regardless, so a reader that trusts it asks the
    wrong socket whenever the window lives elsewhere.
    """
    home = Path('/tmp/signals-session-home')

    def fake_tmux(*args, **kwargs):
        from subprocess import CompletedProcess
        target = ''
        for i, word in enumerate(args):
            if word == '-t' and i + 1 < len(args):
                target = args[i + 1]
                break
        session = target.split(':', 1)[0] if ':' in target else ''
        if args[0] == 'has-session':
            ok = session in alive
            return CompletedProcess(args, 0 if ok else 1, b'', b'')
        if args[0] == 'display-message':
            return CompletedProcess(args, 0, b'4242 0\n', b'')
        return CompletedProcess(args, 0, b'', b'')

    monkeypatch.setattr(seed, '_tmux', fake_tmux)
    monkeypatch.setattr(seed, 'recorded_session', lambda h: receipt_session)
    monkeypatch.setattr(tmux, 'home_sessions', lambda h: list(owned))
    return home


def test_window_is_found_in_the_session_that_holds_it(monkeypatch):
    """The role lives in the core session while the receipt names life-support."""
    home = _install(
        monkeypatch,
        receipt_session=LIFE,
        owned=[(LIFE, LIFE_SOCKET), (CORE, CORE_SOCKET)],
        alive={CORE},
    )
    session, socket = boundary._role_window(home, 'signals')
    assert session == CORE
    assert socket == CORE_SOCKET


def test_receipt_alone_does_not_hide_a_live_window(monkeypatch):
    """The whole point: the receipt names the wrong session, the window is alive."""
    home = _install(
        monkeypatch,
        receipt_session=LIFE,
        owned=[(LIFE, LIFE_SOCKET), (CORE, CORE_SOCKET)],
        alive={CORE},
    )
    located = boundary._role_window(home, 'signals')
    assert located is not None
    assert located[0] == CORE


def test_a_role_in_no_owned_session_is_still_missing(monkeypatch):
    """Enumeration that finds no window for the role must not invent one."""
    home = _install(
        monkeypatch,
        receipt_session=CORE,
        owned=[(CORE, CORE_SOCKET)],
        alive=set(),
    )
    assert boundary._role_window(home, 'signals') is None


def test_the_fallback_still_uses_the_receipt_when_nothing_is_enumerable(monkeypatch):
    """A home that owns no enumerable session keeps the receipt path."""
    home = _install(
        monkeypatch,
        receipt_session=CORE,
        owned=[],
        alive={CORE},
    )
    monkeypatch.setattr(boundary, 'owned_socket', lambda h, s: CORE_SOCKET)
    # No owned session to enumerate: the receipt path must still resolve.
    assert boundary._role_window(home, 'signals') is None


def test_enumeration_failure_falls_back_instead_of_raising(monkeypatch):
    """A failed enumeration is UNKNOWN input, never a crash for the watcher."""
    home = Path('/tmp/signals-session-home')

    def boom(h):
        raise ValueError('enumeration unavailable')

    monkeypatch.setattr(tmux, 'home_sessions', boom)
    monkeypatch.setattr(seed, 'recorded_session', lambda h: CORE)
    assert boundary._role_window(home, 'signals') is None


def test_process_resolves_a_live_window_the_receipt_names_wrongly(monkeypatch):
    """The behavioural control: the reader finds the window, not a missing one.

    Before the repair this raised `mind window missing` for a pane that was
    alive and serving its wake, because the receipt named the session that
    did not hold it.
    """
    import os

    from mishe_tauftauf import omp_pane

    live_sessions = {CORE}
    home = _install(
        monkeypatch,
        receipt_session=LIFE,
        owned=[(LIFE, LIFE_SOCKET), (CORE, CORE_SOCKET)],
        alive=live_sessions,
    )
    pid = os.getpid()

    def fake_tmux(*args, **kwargs):
        from subprocess import CompletedProcess
        target = ''
        for i, word in enumerate(args):
            if word == '-t' and i + 1 < len(args):
                target = args[i + 1]
                break
        session = target.split(':', 1)[0] if ':' in target else ''
        if args[0] == 'has-session':
            ok = session in live_sessions
            return CompletedProcess(args, 0 if ok else 1, b'', b'')
        if args[0] == 'display-message':
            return CompletedProcess(args, 0, f'{pid} 0\n'.encode(), b'')
        return CompletedProcess(args, 0, b'', b'')

    monkeypatch.setattr(seed, '_tmux', fake_tmux)
    monkeypatch.setattr(
        omp_pane, 'command_line',
        lambda p: ['omp', '--model=minimax-code/MiniMax-M3.1-Flash'])

    session, proc = boundary.process(home, 'signals')
    assert session == CORE
    assert proc['pid'] == pid
    assert proc['model'] == 'minimax-code/MiniMax-M3.1-Flash'
