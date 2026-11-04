import os
import subprocess

from mishe_tauftauf.plant_boot import layout, boot_start


def test_boot_reconstructs_views_without_models_and_preserves_existing_humans(tmp_path):
    from mishe_tauftauf.state_files import write_json
    (tmp_path / 'charters').mkdir()
    (tmp_path / 'charters/genome.md').write_text('owned area')
    write_json(tmp_path / 'presentation.json', {'active': ['genome'], 'resting': [], 'session': 'inner'})
    outer, inner = str(tmp_path / 'outer.sock'), str(tmp_path / 'inner.sock')
    env = {k: v for k, v in os.environ.items() if k != 'TMUX'}
    def tmux(*args):
        return subprocess.check_output(['tmux', '-S', outer, *args], env=env, text=True).strip()
    try:
        tmux('new-session', '-d', '-s', 'plants', '-n', 'op', '/bin/cat')
        pane = tmux('display-message', '-p', '-t', 'plants:op', '#{pane_id}')
        layout(tmp_path, tmp_path, outer=outer, inner=inner, command=['/bin/cat'])
        first = tmux('list-panes', '-a', '-F', '#{window_name}|#{pane_id}')
        layout(tmp_path, tmp_path, outer=outer, inner=inner, command=['/bin/cat'])
        assert first == tmux('list-panes', '-a', '-F', '#{window_name}|#{pane_id}')
        assert tmux('display-message', '-p', '-t', 'plants:op', '#{pane_id}') == pane
        assert set(tmux('list-windows', '-t', 'plants', '-F', '#{window_name}').splitlines()) == {
            'op', 'budget', 'bash', 'mishe', 'android-body'}
        assert tmux('show-options', '-gv', 'prefix') == 'C-b'
    finally:
        for socket in (outer, inner):
            subprocess.run(['tmux', '-S', socket, 'kill-server'], env=env, capture_output=True)


def test_no_boot_receipt_keeps_hold_and_never_enables_models(tmp_path, monkeypatch):
    import pytest
    from mishe_tauftauf import plant_boot
    calls = []
    monkeypatch.setattr(plant_boot.subprocess, 'run', lambda args, **kw: calls.append(args))
    with pytest.raises(RuntimeError, match='no current authorized boot receipt'):
        boot_start(tmp_path, tmp_path, 'requested', timeout=0)
    assert len(calls) == 1 and calls[0][-4:] == ['hold', 'on', '--reason',
        'authorized reboot: waiting for current-boot guard admission']
