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
    import os
    root = tmp_path / 'wakes'
    root.mkdir()
    key = 'wake-health-100'
    write_json(root / (key + '.snapshot.json'), {'role': 'health', 'snapshot': {'reasons': ['signal:failed']}})
    write_json(root / (key + '.yield.json'), {'role': 'health', 'result': 'changed'})
    (root / (key + '.prompt.out.txt')).write_text('Restored observation. Literal $(touch NO)\x1b[31m')
    write_json(root / (key + '.work.json'), {'role': 'health'})
    # Coarse filesystems can give all lifecycle records the same timestamp.
    # Filename ordering alone places .work before .yield in that case.
    for path in root.glob('*.json'):
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
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
    import os, time
    aged = time.time() - plant_tape.OUTPUT_GRACE_SECONDS - 5                  # a work record this old with no output and no yield is UNKNOWN
    os.utime(root / 'wake-genome-200.work.json', (aged, aged))
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


def test_output_written_after_its_work_record_is_still_shown(tmp_path):
    # Review of the delivery of 67ca1ad2: a work record seen before its engine output was emitted 'Output UNKNOWN' and was never revisited.
    root = tmp_path / 'wakes'
    root.mkdir()
    key = 'wake-health-200'
    write_json(root / (key + '.snapshot.json'), {'role': 'health', 'snapshot': {'reasons': ['inbox']}})
    write_json(root / (key + '.work.json'), {'role': 'health'})
    cursor, text = plant_tape.wakes_step(tmp_path, None)
    assert 'WAKE health' in text and 'OUTPUT' not in text                     # output not written yet: deferred, not UNKNOWN
    assert key + '.work.json' not in cursor
    (root / (key + '.prompt.out.json')).write_text('{"result": "engine words"}')
    cursor, text = plant_tape.wakes_step(tmp_path, cursor)
    assert 'OUTPUT health' in text and 'Output UNKNOWN' not in text
    assert key + '.work.json' in cursor


def test_wake_blocks_show_the_model_that_ran_and_the_fall_through(tmp_path):
    import json
    root = tmp_path / 'wakes'
    root.mkdir()
    (tmp_path / 'engine').mkdir()
    rows = [
        {'event': 'select', 'key': 'omp:routerai/z-ai/glm-5.3-flash:-', 'role': 'genome', 'wake': 'wake-genome-1'},
        {'event': 'fallback', 'key': 'omp:routerai/z-ai/glm-5.3-flash:-', 'role': 'genome', 'wake': 'wake-genome-1',
         'reason': 'rate limited: provider rate limit before any tool call: 429'},
        {'event': 'select', 'key': 'omp:minimax-code/MiniMax-M3.1-Flash:minimax-a', 'role': 'genome', 'wake': 'wake-genome-1'},
    ]
    (tmp_path / 'engine' / 'routing-ledger.jsonl').write_text('{cut\n' + '\n'.join(json.dumps(r) for r in rows) + '\n')
    write_json(root / 'wake-genome-1.snapshot.json', {'role': 'genome', 'snapshot': {'reasons': ['inbox']}})
    write_json(root / 'wake-genome-1.yield.json', {'role': 'genome', 'result': 'changed'})
    write_json(root / 'wake-docs-2.snapshot.json', {'role': 'docs', 'snapshot': {'reasons': ['inbox']}})
    write_json(root / 'wake-docs-3.yield.json', {'role': 'docs', 'result': 'changed'})
    _, text = plant_tape.wakes_step(tmp_path, None)
    assert 'SETTLED genome · omp minimax-code/MiniMax-M3.1-Flash (after: routerai/z-ai/glm-5.3-flash rate limited)' in text
    assert 'WAKE docs · model: not selected yet' in text      # a wake just starting has no select yet
    assert 'SETTLED docs · model UNKNOWN' in text             # never the configured model


def test_record_names_name_their_kind_and_skip_files_that_are_not_records():
    # The wakes pane reads records, and a file that names no record is not one.
    # A name that ends with a record suffix is that record, and the kind must
    # match the suffix: a wake that is named as a settlement would render the
    # wrong block for every reader.
    from mishe_tauftauf.plant_view_boundary import record_kind, read_records

    assert record_kind('wake-docs-1.snapshot.json') == 'wake'
    assert record_kind('wake-docs-1.yield.json') == 'settled'
    assert record_kind('wake-docs-1.work.json') == 'output'
    assert record_kind('wake-docs-1.work-start.json') is None   # a start is not a record
    assert record_kind('wake-docs-1.prompt.out.json') is None
    assert record_kind('notes.txt') is None

    records, starts = read_records([
        'wake-docs-1.snapshot.json',
        'wake-docs-1.yield.json',
        'wake-docs-1.work.json',
        'wake-docs-1.work-start.json',
        'wake-docs-1.prompt.out.json',
    ])
    assert records == [
        ('wake', 'wake-docs-1.snapshot.json'),
        ('settled', 'wake-docs-1.yield.json'),
        ('output', 'wake-docs-1.work.json'),
    ]
    assert starts == ['wake-docs-1.work-start.json']


def test_a_started_wake_with_no_output_still_shows_a_running_block(tmp_path):
    # The progress block is the only thing that shows a wake is alive; if the
    # start name is missed, a running wake reads as nothing at all.
    import os
    import time
    root = tmp_path / 'wakes'
    root.mkdir()
    key = 'wake-health-300'
    start = root / (key + '.work-start.json')
    start.write_text('{}')
    (root / (key + '.prompt.out.jsonl')).write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"still working"}]}}\n')
    # A progress block appears from the first bucket onwards, so the start
    # record has to be old enough to be in one.
    aged = time.time() - plant_tape.PROGRESS_SECONDS - 5
    os.utime(start, (aged, aged))
    _, text = plant_tape.wakes_step(tmp_path, None)
    # The role is UNKNOWN until the work record names it, but the block itself
    # must appear: a start record that is not read leaves the wake invisible.
    assert 'RUNNING' in text and key in text
