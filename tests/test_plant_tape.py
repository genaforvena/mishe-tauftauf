import subprocess

import pytest

from mishe_tauftauf.state_files import write_json
from mishe_tauftauf import plant_tape


def test_git_replays_in_order_follows_multiple_commits_and_reports_rewind(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init', '-q')
    git('config', 'user.name', 'Tape fixture')
    git('config', 'user.email', 'fixture@example.test')
    def commit(text):
        (tmp_path / 'example.txt').write_text(text)
        git('add', 'example.txt')
        git('commit', '-qm', text)
        return git('rev-parse', 'HEAD')
    first = commit('first change')
    commit('second change')
    cursor, text = plant_tape.git_step(tmp_path, None)
    assert text.index('first change') < text.index('second change')
    assert 'example.txt' in text and 'Tape fixture' in text
    assert plant_tape.git_step(tmp_path, cursor) == (cursor, '')
    commit('third change')
    commit('fourth change')
    cursor, text = plant_tape.git_step(tmp_path, cursor)
    assert 'second change' not in text
    assert text.index('third change') < text.index('fourth change')
    git('reset', '--hard', first)
    _, text = plant_tape.git_step(tmp_path, cursor)
    assert 'history changed' in text and 'first change' in text


def test_wakes_keep_settlement_speech_and_do_not_mutate(tmp_path):
    root = tmp_path / 'wakes'
    root.mkdir()
    key = 'wake-health-100'
    write_json(root / (key + '.snapshot.json'), {'role': 'health', 'snapshot': {'reasons': ['signal:failed']}})
    write_json(root / (key + '.yield.json'), {'role': 'health', 'result': 'changed'})
    (root / (key + '.prompt.out.txt')).write_text('Restored observation. Literal $(touch NO)\x1b[31m')
    write_json(root / (key + '.work.json'), {'role': 'health'})
    def snapshot():
        return {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    before = snapshot()
    cursor, text = plant_tape.wakes_step(tmp_path, None)
    assert text.index('WAKE health') < text.index('SETTLED health') < text.index('OUTPUT health')
    assert 'Restored observation.' in text and 'signal:failed' in text
    assert '$(touch NO)' in text and '\x1b' not in text
    assert snapshot() == before
    assert plant_tape.wakes_step(tmp_path, cursor) == (cursor, '')
    write_json(root / 'wake-genome-200.snapshot.json', {'role': 'genome', 'snapshot': {'reasons': []}})
    cursor, text = plant_tape.wakes_step(tmp_path, cursor)
    assert 'WAKE genome' in text and 'WAKE health' not in text
    write_json(root / 'wake-genome-200.work.json', {'role': 'genome'})
    _, text = plant_tape.wakes_step(tmp_path, cursor)
    assert 'Settlement UNKNOWN' in text and 'Output UNKNOWN' in text
    (root / 'wake-broken.snapshot.json').write_text('{')
    with pytest.raises(ValueError):
        plant_tape.wakes_step(tmp_path, cursor)


def test_follow_reports_distinct_failures_and_recovery_without_advancing_cursor():
    calls = []
    def step(cursor):
        calls.append(cursor)
        if len(calls) < 3:
            raise ValueError('missing tape')
        return 5, 'new evidence'
    follower = plant_tape.Follower(step)
    assert 'UNKNOWN' in follower.poll()
    assert follower.poll() == ''
    assert 'recovered' in follower.poll()
    assert calls == [None, None, None]
    assert follower.cursor == 5


def test_real_wake_failure_remains_unknown_until_repaired(tmp_path):
    follower = plant_tape.Follower(lambda cursor: plant_tape.wakes_step(tmp_path, cursor))
    assert 'UNKNOWN' in follower.poll()
    root = tmp_path / 'wakes'
    root.mkdir()
    path = root / 'wake-health-1.snapshot.json'
    path.write_text('{')
    assert 'UNKNOWN' in follower.poll()
    assert follower.cursor is None and path.read_text() == '{'
    assert follower.poll() == ''
    write_json(path, {'role': 'health', 'snapshot': {'reasons': ['inbox']}})
    text = follower.poll()
    assert 'recovered' in text and 'WAKE health' in text
    assert follower.poll() == ''
