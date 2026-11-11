"""Idempotent presentation on an explicitly supplied plant socket; no models.

Existing panes are never respawned. Conflicting layouts refuse mutation rather
than killing human activity. The caller chooses the owned socket and session.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .core.dispatch_record import valid_role

# Format for `tmux list-windows -a` probes: session, window name, pane count.
WINDOW_FORMAT = "#{session_name}\t#{window_name}\t#{window_panes}"


def parse_list_windows(text: str) -> list[dict[str, object]] | None:
    """Parse ``tmux list-windows -a -F WINDOW_FORMAT`` output.

    Returns None when any line is malformed: a line that does not split into
    exactly three fields, or a non-integer pane count, is evidence of a broken
    probe, not a window to count.
    """
    windows: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3 or not fields[2].isdigit():
            return None
        windows.append({"session": fields[0], "window": fields[1], "panes": int(fields[2])})
    return windows


def parse_session_pids(text: str) -> dict[str, str] | None:
    """Parse ``#{session_name}\\t#{pid}`` lines into name -> pid; None when malformed."""
    sessions: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        name, sep, pid = line.rpartition("\t")
        if not sep or not name or not pid.isdigit():
            return None
        sessions[name] = pid
    return sessions


def parse_session_births(output: str) -> list[tuple[str, float]] | None:
    """Parse ``#{session_name}\\t#{session_created}`` lines; None when malformed."""
    births: list[tuple[str, float]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not parts[0] or not parts[1].strip().isdigit():
            return None
        births.append((parts[0], float(parts[1])))
    return births


def parse_pane_dead_flags(text: str) -> dict[str, list[bool]]:
    """Decode ``#{window_name}\\t#{pane_index}\\t#{pane_dead}`` rows.

    Preserve tmux's row order and reject malformed field counts rather than
    treating an unreadable observation as an empty session.
    """
    windows: dict[str, list[bool]] = {}
    for line in text.splitlines():
        name, _index, dead = line.split("\t")
        windows.setdefault(name, []).append(dead == "1")
    return windows


def restore_arguments(argv=None):
    """Decode the standalone registry restorer's command-line options."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--registry")
    return parser.parse_args(argv)


def view_argv(view_command=None):
    """Program prefix for a view pane, with PYTHONPATH pinned to this checkout.

    The pane must not inherit PYTHONPATH from the tmux server environment, which can
    point at an old release (observed 2026-11-08: plant_view ran an April-era runtime/src).
    """
    program = list(view_command) if view_command else [sys.executable, '-m', 'mishe_tauftauf.plant_view']
    return ['env', 'PYTHONPATH=' + str(Path(__file__).resolve().parents[1]), *program]


def setup(socket: str, session: str, home: Path, roles, *, command=None, view_command=None):
    if not roles or any(not valid_role(r) or r in ('chat', 'git', 'wakes') for r in roles) or len(set(roles)) != len(roles):
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
    tmux('set-option', '-t', session, 'history-limit', '3000')   # measured 4.8 KB per full line: 9 panes x 10000 = 432 MB OOMed the 512M scope
    for role in [*roles, 'chat', 'git', 'wakes']:
        target = session + ':' + role
        exists = role in tmux('list-windows', '-t', '=' + session, '-F', '#{window_name}').splitlines()
        marker = tmux('show-options', '-wqv', '-t', target, '@mishe-view', check=False) if exists else ''
        if exists and marker not in (role, 'bootstrap'):
            raise ValueError(f'window {target} is not an owned view; reconcile it explicitly')
        base = command or [*view_argv(view_command), '--home', str(home.resolve()), '--role', role, '--follow']
        chat = ['tail', '-n', '100', '-F', '--', str(home.resolve() / 'chat.log')]
        tape = command or ['env', 'PYTHONPATH=' + str(Path(__file__).resolve().parents[1]),
                           sys.executable, '-m', 'mishe_tauftauf.plant_tape',
                           '--home', str(home.resolve()), '--tape', role, '--follow']
        top = shlex.join(chat if role == 'chat' else tape if role in ('git', 'wakes') else base)
        if not exists:
            tmux('new-window', '-d', '-t', session + ':', '-n', role, top)
        elif marker == 'bootstrap':
            tmux('respawn-pane', '-k', '-t', target, top)  # only our newly created cat
        tmux('set-option', '-w', '-t', target, '@mishe-view', role)
        panes = tmux('list-panes', '-t', target, '-F', '#{pane_id}').splitlines()
        expected = 1 if role in ('chat', 'git', 'wakes') else 2
        if len(panes) == 1 and expected == 2:
            tmux('split-window', '-d', '-v', '-t', panes[0], shlex.join([*base, '--output'] if not command else base))
        elif len(panes) != expected:
            raise ValueError(f'{target}: unexpected pane count; preserve and reconcile')
        tmux('set-option', '-w', '-t', target, 'automatic-rename', 'off')
        if role in ('git', 'wakes'):
            tmux('set-option', '-w', '-t', target, 'remain-on-exit', 'on')
        if expected == 2:
            tmux('select-layout', '-t', target, 'even-vertical')
    return tmux('list-panes', '-s', '-t', session, '-F', '#{window_name}|#{pane_id}')
