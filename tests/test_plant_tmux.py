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
        for name in ('git', 'wakes'):
            assert tmux('show-options', '-wqv', '-t', 'test:' + name, '@mishe-view') == name
            assert '\n' not in tmux('list-panes', '-t', 'test:' + name, '-F', '#{pane_id}')
        assert tmux('show-options', '-v', '-t', 'test', 'prefix') == 'C-a'
        tmux('resize-window', '-t', 'test:genome', '-x', '100', '-y', '40')
        assert tmux('display-message', '-p', '-t', 'test:genome', '#{window_width}') == '100'
    finally:
        subprocess.run(['tmux', '-S', socket, 'kill-server'], env=env, capture_output=True)


def test_view_pane_pins_pythonpath_instead_of_inheriting_server_environment(monkeypatch, tmp_path):
    from pathlib import Path
    from mishe_tauftauf import plant_tmux
    monkeypatch.setenv('PYTHONPATH', '/stale/release/src')
    checkout_src = str(Path(plant_tmux.__file__).resolve().parents[1])
    default = plant_tmux.view_argv()
    assert default[:2] == ['env', 'PYTHONPATH=' + checkout_src]
    assert default[-2:] == ['-m', 'mishe_tauftauf.plant_view']
    assert '/stale/release/src' not in ' '.join(default)
    assert plant_tmux.view_argv(['/usr/bin/python3', 'view-host.py'])[-2:] == ['/usr/bin/python3', 'view-host.py']
    assert plant_tmux.view_argv(['/usr/bin/python3', 'view-host.py'])[:2] == ['env', 'PYTHONPATH=' + checkout_src]
