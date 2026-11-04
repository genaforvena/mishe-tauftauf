import os
import subprocess

import pytest

from mishe_tauftauf.plant_tmux import setup


def test_real_scratch_layout_is_idempotent_and_preserves_foreign_windows(tmp_path):
    socket = str(tmp_path / 'tmux.sock')
    env = {k: v for k, v in os.environ.items() if k != 'TMUX'}
    def tmux(*args):
        return subprocess.check_output(['tmux', '-S', socket, *args], env=env, text=True).strip()
    try:
        tmux('new-session', '-d', '-s', 'test', '-n', 'foreign')
        tmux('bind-key', '-T', 'prefix', 'C-a', 'display-message', 'foreign-binding')
        original_binding = tmux('list-keys', '-T', 'prefix', 'C-a')
        foreign = tmux('display-message', '-p', '-t', 'test:foreign', '#{pane_id}')
        setup(socket, 'test', tmp_path, ['genome', 'health'], command=['/bin/cat'])
        assert tmux('list-keys', '-T', 'prefix', 'C-a') == original_binding
        first = tmux('list-panes', '-a', '-F', '#{window_name}|#{pane_id}')
        setup(socket, 'test', tmp_path, ['genome', 'health'], command=['/bin/cat'])
        assert first == tmux('list-panes', '-a', '-F', '#{window_name}|#{pane_id}')
        assert tmux('display-message', '-p', '-t', 'test:foreign', '#{pane_id}') == foreign
        assert tmux('list-panes', '-t', 'test:genome', '-F', '#{pane_id}').count('\n') == 1
        assert '\n' not in tmux('list-panes', '-t', 'test:chat', '-F', '#{pane_id}')
        assert tmux('show-options', '-v', '-t', 'test', 'prefix') == 'C-a'
        tmux('resize-window', '-t', 'test:genome', '-x', '100', '-y', '40')
        assert tmux('display-message', '-p', '-t', 'test:genome', '#{window_width}') == '100'
    finally:
        subprocess.run(['tmux', '-S', socket, 'kill-server'], env=env, capture_output=True)
