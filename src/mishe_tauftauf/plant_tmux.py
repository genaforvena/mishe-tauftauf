"""Idempotent presentation on an explicitly supplied plant socket; no models.

Existing panes are never respawned. Conflicting layouts refuse mutation rather
than killing human activity. The caller chooses the owned socket and session.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from .core.dispatch_record import valid_role


def setup(socket: str, session: str, home: Path, roles, *, command=None, view_command=None):
    if not roles or any(not valid_role(r) or r == 'chat' for r in roles) or len(set(roles)) != len(roles):
        raise ValueError('distinct valid view roles required')
    env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_TMPDIR')}
    def tmux(*args, check=True):
        return subprocess.run(['tmux', '-S', socket, *args], env=env, text=True,
                              capture_output=True, check=check).stdout.rstrip('\n')
    if tmux('has-session', '-t', '=' + session, check=False) == '':
        # has-session has no stdout even on success; inspect sessions explicitly.
        sessions = tmux('list-sessions', '-F', '#{session_name}', check=False).splitlines()
        if session not in sessions:
            tmux('new-session', '-d', '-s', session, '-n', roles[0], '/bin/cat')
            tmux('set-option', '-w', '-t', session + ':' + roles[0], '@mishe-view', 'bootstrap')
    tmux('set-option', '-t', session, 'prefix', 'C-a')
    tmux('set-option', '-t', session, 'history-limit', '2000')   # frames are not history; 100000 x 18 panes OOMed the tmux scope
    for role in [*roles, 'chat']:
        target = session + ':' + role
        exists = role in tmux('list-windows', '-t', '=' + session, '-F', '#{window_name}').splitlines()
        marker = tmux('show-options', '-wqv', '-t', target, '@mishe-view', check=False) if exists else ''
        if exists and marker not in (role, 'bootstrap'):
            raise ValueError(f'window {target} is not an owned view; reconcile it explicitly')
        base = command or [*(view_command or [sys.executable, '-m', 'mishe_tauftauf.plant_view']), '--home', str(home.resolve()),
                           '--role', role, '--follow']
        chat = ['tail', '-n', '100', '-F', '--', str(home.resolve() / 'chat.log')]
        top = shlex.join(chat if role == 'chat' else base)
        if not exists:
            tmux('new-window', '-d', '-t', session + ':', '-n', role, top)
        elif marker == 'bootstrap':
            tmux('respawn-pane', '-k', '-t', target, top)  # only our newly created cat
        tmux('set-option', '-w', '-t', target, '@mishe-view', role)
        panes = tmux('list-panes', '-t', target, '-F', '#{pane_id}').splitlines()
        expected = 1 if role == 'chat' else 2
        if len(panes) == 1 and expected == 2:
            tmux('split-window', '-d', '-v', '-t', panes[0], shlex.join([*base, '--output'] if not command else base))
        elif len(panes) != expected:
            raise ValueError(f'{target}: unexpected pane count; preserve and reconcile')
        tmux('set-option', '-w', '-t', target, 'automatic-rename', 'off')
        if expected == 2:
            tmux('select-layout', '-t', target, 'even-vertical')
    return tmux('list-panes', '-s', '-t', session, '-F', '#{window_name}|#{pane_id}')
