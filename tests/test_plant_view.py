import json

import pytest

from mishe_tauftauf import plant_view as view, signal_store
from mishe_tauftauf.feed import Feed, FeedError
from mishe_tauftauf.state_files import write_json


def bytes_(home):
    return {str(p.relative_to(home)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in home.rglob('*') if p.is_file()}


def test_missing_view_never_creates_state(tmp_path):
    before = bytes_(tmp_path)
    text = view.render(tmp_path, 'genome', now=100)
    assert 'UNKNOWN' in text
    assert bytes_(tmp_path) == before


def test_held_unknown_signals_unread_and_literal_prose_are_read_only(tmp_path):
    home = tmp_path
    (home / 'hold').write_text('maintenance')
    (home / 'walls').mkdir()
    (home / 'walls/genome.md').write_text('question $(touch SHOULD_NOT_EXIST)\x1b[31m')
    signal_store.register(home, 'fault', max_age=10, subscribers=['genome'])
    signal_store.observe(home, 'fault', 'GREEN', now=0, source='fixture')
    feed = Feed(home)
    feed.append('health', '[dm] to=genome\ncheck the failure')
    before = bytes_(home)
    text = view.render(home, 'genome', now=100)
    assert 'HELD' in text and 'UNKNOWN' in text and 'check the failure' in text
    assert '$(touch SHOULD_NOT_EXIST)' in text and '\x1b' not in text
    assert bytes_(home) == before


def test_read_only_mail_refuses_stale_checkpoint(tmp_path):
    feed = Feed(tmp_path)
    feed.append('health', '[dm] to=genome\ncheck')
    assert feed.view_entries(start=1)[0].body.endswith('check')
    feed.index_path.unlink()
    before = bytes_(tmp_path)
    with pytest.raises(FeedError):
        feed.view_entries(start=1)
    assert bytes_(tmp_path) == before


def test_large_wall_and_mail_do_not_drown_current_view(tmp_path):
    (tmp_path / 'walls').mkdir()
    (tmp_path / 'walls/genome.md').write_text('current question\n' + 'evidence\n' * 1000)
    for n in range(20):
        Feed(tmp_path).append('health', '[dm] to=genome\n' + 'literal text\n' * 100)
    text = view.render(tmp_path, 'genome', now=100)
    assert 'current question' in text and 'omitted' in text
    assert len(text.splitlines()) < 20


@pytest.mark.parametrize('state', ['started', 'unknown', 'completed', 'failed'])
def test_bottom_keeps_actual_output_and_lifecycle(tmp_path, state):
    write_json(tmp_path / 'dispatch/genome.json', dict(role='genome', key='wake-genome-1', state=state))
    out = tmp_path / 'wakes/wake-genome-1.prompt.out.json'
    out.parent.mkdir()
    out.write_text('{partial stderr and json')
    before = bytes_(tmp_path)
    text = view.render(tmp_path, 'genome', now=100, output=True)
    assert state.upper() in text and 'partial stderr' in text and 'UNKNOWN' in text
    out.write_text(json.dumps({'result': 'actual final text'}))
    text = view.render(tmp_path, 'genome', now=100, output=True)
    assert 'actual final text' in text
    write_json(tmp_path / 'wakes/wake-genome-1.yield.json',
               {'role': 'genome', 'key': 'wake-genome-1', 'result': 'changed', 'settled_at': 100})
    assert 'YIELDED' in view.render(tmp_path, 'genome', now=100, output=True)


def test_malformed_yield_receipt_is_unknown_not_settled(tmp_path):
    write_json(tmp_path / 'dispatch/genome.json', dict(role='genome', key='wake-genome-1', state='unknown'))
    write_json(tmp_path / 'wakes/wake-genome-1.yield.json', 'not a receipt')
    text = view.render(tmp_path, 'genome', now=100, output=True)
    assert 'YIELDED' not in text and 'Yield UNKNOWN' in text


def test_bottom_streams_assistant_and_tools_before_engine_exits(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / "wake.prompt.out.jsonl"
    events = [
        {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "private scratch"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Checking readings"}},
        {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "mishe signal show ci"}},
        {"type": "tool_execution_update", "toolName": "bash", "partialResult": {"content": [{"type": "text", "text": "GREEN"}]}},
    ]
    out.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
    text = output_text(out)
    assert "Checking readings" in text and "mishe signal show ci" in text and "GREEN" in text
    assert "private scratch" not in text
    assert "assistantMessageEvent" not in text
    with out.open('a') as handle:
        handle.write('{incomplete')
    assert "UNKNOWN" in output_text(out) and '{incomplete' in output_text(out)


def test_stream_finished_message_and_unknown_event_are_visible(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / "out.jsonl"
    out.write_text(json.dumps({"type":"message_end", "message":{"role":"assistant", "content":[{"type":"text","text":"Done"}]}})+'\n'+json.dumps({"type":"future_event"})+'\n')
    text = output_text(out)
    assert "Done" in text and "UNKNOWN" in text and "future_event" in text


def test_stream_tail_keeps_new_events_after_large_output(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / "out.jsonl"
    out.write_text(json.dumps({"type":"tool_execution_end", "result":{"content":[{"type":"text","text":"EARLIEST"+'x'*20000}]}})+'\n'+json.dumps({"type":"message_update", "assistantMessageEvent":{"type":"text_delta","delta":"LATEST LIVE OUTPUT"}})+'\n')
    text = output_text(out)
    assert "LATEST LIVE OUTPUT" in text and "EARLIEST" not in text
    assert "rendered tail" in text


def test_large_terminal_envelope_keeps_preceding_readable_output(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / "out.jsonl"
    out.write_text(json.dumps({"type":"message_end", "message":{"role":"assistant","content":[{"type":"text","text":"PUBLIC FINAL ANSWER"}]}})+'\n'+json.dumps({"type":"agent_end","messages":[{"large":"z"*70000}]})+'\n')
    text = output_text(out)
    assert "PUBLIC FINAL ANSWER" in text
    assert len(text) < 17000 and '"large"' not in text


def test_oversized_stream_window_marks_loss_without_raw_flood(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / "out.jsonl"
    out.write_text(json.dumps({"type":"agent_end","messages":[{"large":"z"*(1024*1024+100)}]})+'\n'+json.dumps({"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"LATEST"}})+'\n')
    text = output_text(out)
    assert "UNKNOWN leading stream event truncated" in text and "LATEST" in text
    assert len(text)<1000


def test_not_yet_dispatched_role_has_explanatory_unknown_read_only_view(tmp_path):
    (tmp_path / 'dispatch').mkdir()
    before = bytes_(tmp_path)
    text = view.render(tmp_path, 'steward', now=100, output=True)
    assert 'no dispatch recorded' in text and 'UNKNOWN' in text
    assert 'No such file' not in text
    assert bytes_(tmp_path) == before


def test_missing_record_with_retained_output_does_not_infer_first_wake(tmp_path):
    (tmp_path / 'dispatch').mkdir()
    (tmp_path / 'wakes').mkdir()
    (tmp_path / 'wakes/wake-steward-1.prompt.out.json').write_text('{"result":"prior effect"}')
    text = view.render(tmp_path, 'steward', now=100, output=True)
    assert 'UNKNOWN' in text and 'prior effect' in text
    assert 'never ran' not in text


def test_corrupt_dispatch_remains_unknown(tmp_path):
    (tmp_path / 'dispatch').mkdir()
    (tmp_path / 'dispatch/steward.json').write_text('{broken')
    text = view.render(tmp_path, 'steward', now=100, output=True)
    assert 'UNKNOWN' in text and 'not JSON' in text


# --- human-readable panes: model, wake summary, what the wake changed -----------

import os
import subprocess
import time as _time

from mishe_tauftauf import intentions, yielding
from mishe_tauftauf.plant_view_boundary import RAW_CLOSE, RAW_OPEN, machine_syntax

T0 = 1_800_000_000


def envelope(**over):
    base = {'type': 'result', 'subtype': 'success', 'is_error': False, 'num_turns': 5,
            'duration_ms': 24000, 'total_cost_usd': 0.0073, 'stop_reason': 'end_turn',
            'result': 'First finding.\n\n- second finding with `code`',
            'usage': {'input_tokens': 8, 'cache_creation_input_tokens': 2000,
                      'cache_read_input_tokens': 100000, 'output_tokens': 3693},
            'modelUsage': {'claude-haiku-5-5': {'costUSD': 0.007}}, 'permission_denials': []}
    base.update(over)
    return base


def settled_wake(home, role='genome', key='wake-genome-1', *, body=None, start=T0, end=T0 + 30, state='completed'):
    write_json(home / f'dispatch/{role}.json', dict(role=role, key=key, state=state, detail='engine exit 0'))
    write_json(yielding.snapshot_path(home, key), {'role': role, 'snapshot': {}})
    os.utime(yielding.snapshot_path(home, key), (start, start))
    out = home / f'wakes/{key}.prompt.out.json'
    out.write_text(json.dumps(body if body is not None else envelope()))
    os.utime(out, (end, end))
    write_json(yielding.receipt_path(home, key),
               {'role': role, 'key': key, 'result': 'blocked', 'continue': False, 'settled_at': end})


def test_bottom_leads_with_model_and_summary_and_shows_the_result_as_text(tmp_path):
    settled_wake(tmp_path)
    text = view.render(tmp_path, 'genome', now=T0 + 60, output=True)
    first, second = text.splitlines()[:2]
    assert 'model claude-haiku-5-5' in first
    assert '5 turns' in second and '$0.0073' in second and '24 s' in second
    assert 'tokens 102,008 in / 3,693 out' in second
    assert 'First finding.\n\n- second finding with `code`' in text
    assert 'modelUsage' not in text and 'ttft' not in text and machine_syntax(text) == []


def test_model_that_differs_from_configuration_is_stated(tmp_path):
    settled_wake(tmp_path)
    write_json(tmp_path / 'engines/genome.json', {'engine': 'claude', 'model': 'claude-sonnet-5-5'})
    first = view.render(tmp_path, 'genome', now=T0 + 60, output=True).splitlines()[0]
    assert 'model claude-haiku-5-5 (configured claude-sonnet-5-5; differs)' in first


def test_model_is_known_before_a_running_wake_has_output(tmp_path):
    write_json(tmp_path / 'engines/genome.json', {'engine': 'claude', 'model': 'claude-sonnet-5-5'})
    write_json(tmp_path / 'dispatch/genome.json', dict(role='genome', key='wake-genome-2', state='started', detail=''))
    for output in (False, True):
        text = view.render(tmp_path, 'genome', now=T0, output=output)
        assert 'model claude-sonnet-5-5 (configured' in text.splitlines()[0]


def test_unrecognised_json_is_one_unknown_line_not_a_dump(tmp_path):
    settled_wake(tmp_path, body={'surprise': {'nested': [1, 2, 3]}})
    text = view.render(tmp_path, 'genome', now=T0 + 60, output=True)
    assert 'UNKNOWN: the engine JSON has an unrecognised shape' in text
    assert 'nested' not in text and machine_syntax(text) == []


def test_malformed_output_keeps_a_delimited_raw_tail_as_failure_evidence(tmp_path):
    settled_wake(tmp_path)
    (tmp_path / 'wakes/wake-genome-1.prompt.out.json').write_text('{"half": "written')
    text = view.render(tmp_path, 'genome', now=T0 + 60, output=True)
    assert RAW_OPEN in text and RAW_CLOSE in text and '"half": "written' in text
    assert machine_syntax(text) == []          # deliberate, labelled evidence is exempt


def git(repo, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME='mind', GIT_AUTHOR_EMAIL='m@x', GIT_COMMITTER_NAME='mind',
               GIT_COMMITTER_EMAIL='m@x', GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f'{when} +0000', GIT_COMMITTER_DATE=f'{when} +0000')
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, env=env)


def test_a_wake_reports_the_lines_it_changed_and_where(tmp_path):
    repo, home = tmp_path / 'repo', tmp_path / 'repo/site'
    (repo / 'src').mkdir(parents=True)
    git(repo, 'init', '-q')
    (repo / 'src/a.py').write_text('1\n2\n3\n4\n')
    git(repo, 'add', 'src/a.py')
    git(repo, 'commit', '-qm', 'before the wake', when=T0 - 500)
    (repo / 'src/a.py').write_text('1\n2\nX\n')                      # +1 −2
    (repo / 'src/b.py').write_text('x\ny\nz\n')                      # +3
    git(repo, 'add', 'src')
    git(repo, 'commit', '-qm', 'Fix the thing in the wake', when=T0 + 10)
    (repo / 'src/c.py').write_text('later\n')
    git(repo, 'add', 'src/c.py')
    git(repo, 'commit', '-qm', 'after the wake', when=T0 + 500)
    settled_wake(home)                                                # wake window T0 .. T0+30
    top = view.render(home, 'genome', now=T0 + 60)
    bottom = view.render(home, 'genome', now=T0 + 60, output=True)
    assert 'Changed +4 −2 lines in 2 files, 1 commit' in top and 'src/b.py +3 −0' in top
    assert 'git +4 −2 in 2 file(s), 1 commit(s)' in bottom.splitlines()[1]
    assert 'Fix the thing in the wake' in bottom and '+3 −0  src/b.py' in bottom and '+1 −2  src/a.py' in bottom
    assert 'before the wake' not in bottom and 'after the wake' not in bottom


def test_a_wake_with_no_commits_says_so_and_outside_git_stays_unknown(tmp_path):
    settled_wake(tmp_path)                                            # tmp_path is not a git checkout
    assert 'Changes UNKNOWN' in view.render(tmp_path, 'genome', now=T0 + 60)
    repo = tmp_path / 'r'
    (repo / 'site').mkdir(parents=True)
    git(repo, 'init', '-q')
    (repo / 'f').write_text('x')
    git(repo, 'add', 'f')
    git(repo, 'commit', '-qm', 'old', when=T0 - 5000)
    settled_wake(repo / 'site')
    assert 'Made no commits' in view.render(repo / 'site', 'genome', now=T0 + 60)


def test_top_pane_is_human_text_and_fits_a_screen(tmp_path):
    home = tmp_path
    settled_wake(home)
    (home / 'walls').mkdir()
    (home / 'walls/genome.md').write_text('# genome\n\n## Plate\n' + ''.join(f'{n}. item {n}\n' for n in range(30)))
    for name in ('failed-a', 'failed-b', 'failed-c', 'ok-one'):
        signal_store.register(home, name, max_age=900, subscribers=['genome'])
    for name in ('failed-a', 'failed-b', 'failed-c'):
        signal_store.observe(home, name, 'RED', now=T0, source='run:/x/.venv/bin/python')
    signal_store.observe(home, 'ok-one', 'GREEN', now=T0, source='fixture')
    intentions.add(home, 'genome', 'again', note='scheduled return', due=T0 + 3600)
    for n in range(4):
        Feed(home).append('health', f'[dm] to=genome\nmessage {n}')
    text = view.render(home, 'genome', now=T0 + 60)
    assert machine_syntax(text) == [], text
    assert "{'" not in text and 'None' not in text and '.venv' not in text
    assert 'YIELDED blocked at' in text and 'Signals RED (3): failed-a' in text and '+1 more' in text
    assert 'Next due: ' in text and 'in 59 min' in text and 'latest 2 shown' in text
    assert len(text.splitlines()) <= 18


def test_the_linter_flags_the_old_machine_output():
    old = ["genome — read-only view — sampled 1793818396",
           "YIELDED {'continue': False, 'key': 'wake-genome-1', 'result': 'blocked'} — transport receipt",
           "Signal ci: OK GREEN source=run:/home/x/.venv/bin/python observed=1793818367.33 max_age=900.0s",
           '"ttft_ms": 3701,', '}', 'text with literal \\n escapes', '{"a": 1}']
    found = machine_syntax('\n'.join(old))
    assert len(found) == len(old)


def test_the_linter_exempts_only_labelled_evidence_and_leaves_prose_alone():
    text = '\n'.join(['UNKNOWN stream record: bad; raw: {"a": 1}', RAW_OPEN, '{"b": 2}', RAW_CLOSE,
                      'Plain prose, a list [1] and a call f(x) are fine; so is 296474.'])
    assert machine_syntax(text) == []
    assert machine_syntax(text + '\n{"leaked": true}') != []


def test_stream_tool_arguments_read_as_words_not_json(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / 'o.jsonl'
    out.write_text(json.dumps({'type': 'tool_execution_start', 'toolName': 'read', 'args': {'path': '/a/b.md', 'limit': 5}}) + '\n')
    text = output_text(out)
    assert '[tool read] path: /a/b.md; limit: 5' in text and '{' not in text


def test_unchanged_panes_print_identical_text_between_refreshes(tmp_path):
    # An identical text is not redrawn; a text that changed every 5 s once filled tmux scrollback until OOM.
    settled_wake(tmp_path)
    signal_store.register(tmp_path, 'failed-a', max_age=900, subscribers=['genome'])
    signal_store.observe(tmp_path, 'failed-a', 'RED', now=T0 + 18, source='fixture')
    for output in (False, True):
        first = view.render(tmp_path, 'genome', now=T0 + 40, output=output)
        assert view.render(tmp_path, 'genome', now=T0 + 45, output=output) == first
        assert view.render(tmp_path, 'genome', now=T0 + 59, output=output) == first


def test_every_redraw_clears_tmux_scrollback(capsys, tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, 'argv', ['plant_view', '--home', str(tmp_path), '--role', 'genome', '--follow'])
    def stop(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(view.time, 'sleep', stop)
    with pytest.raises(KeyboardInterrupt):
        view.main()
    assert capsys.readouterr().out.startswith('\033[H\033[2J\033[3J')


# --- bottom pane as an append-only log of every wake -----------------------------------------

def wake_file(home, key, *, start, end, text, role='genome', yielded=True):
    write_json(yielding.snapshot_path(home, key), {'role': role, 'snapshot': {}})
    os.utime(yielding.snapshot_path(home, key), (start, start))
    out = home / f'wakes/{key}.prompt.out.json'
    out.write_text(json.dumps(envelope(result=text)))
    os.utime(out, (end, end))
    if yielded:
        write_json(yielding.receipt_path(home, key), {'role': role, 'key': key, 'result': 'changed', 'continue': False, 'settled_at': end})


def dispatch_state(home, key, state, role='genome'):
    write_json(home / f'dispatch/{role}.json', dict(role=role, key=key, state=state, detail=''))


def test_log_prints_each_wake_once_and_never_repaints(tmp_path):
    for n in range(5):
        wake_file(tmp_path, f'wake-genome-{n}', start=T0 + 100 * n, end=T0 + 100 * n + 30, text=f'answer number {n}')
    dispatch_state(tmp_path, 'wake-genome-4', 'completed')
    first = '\n'.join(view.log_step(tmp_path, 'genome', T0 + 1000))
    assert 'answer number 4' in first and 'answer number 2' in first and 'answer number 1' not in first
    assert 'model claude-haiku-5-5' in first and first.count('══') >= 6      # one header per wake, model in each
    assert view.log_step(tmp_path, 'genome', T0 + 1005) == []                 # nothing new: nothing printed
    wake_file(tmp_path, 'wake-genome-5', start=T0 + 600, end=T0 + 630, text='answer number 5')
    dispatch_state(tmp_path, 'wake-genome-5', 'completed')
    more = '\n'.join(view.log_step(tmp_path, 'genome', T0 + 1010))
    assert 'answer number 5' in more and 'answer number 4' not in more
    log = (tmp_path / 'pane-logs/genome.log').read_text()
    assert all(f'answer number {n}' in log for n in (2, 3, 4, 5)) and machine_syntax(log) == []


def test_running_wake_is_announced_once_then_printed_when_it_settles(tmp_path):
    wake_file(tmp_path, 'wake-genome-1', start=T0, end=T0 + 30, text='old answer')
    dispatch_state(tmp_path, 'wake-genome-1', 'completed')
    view.log_step(tmp_path, 'genome', T0 + 100)
    wake_file(tmp_path, 'wake-genome-2', start=T0 + 200, end=T0 + 210, text='half written', yielded=False)
    dispatch_state(tmp_path, 'wake-genome-2', 'started')
    started = view.log_step(tmp_path, 'genome', T0 + 205)
    assert len(started) == 1 and '▶ wake started' in started[0] and 'half written' not in started[0]
    assert view.log_step(tmp_path, 'genome', T0 + 210) == []
    wake_file(tmp_path, 'wake-genome-2', start=T0 + 200, end=T0 + 240, text='final answer')
    dispatch_state(tmp_path, 'wake-genome-2', 'completed')
    done = '\n'.join(view.log_step(tmp_path, 'genome', T0 + 250))
    assert 'final answer' in done and 'half written' not in done


def test_a_restarted_pane_replays_its_log_tail_and_carries_on(tmp_path):
    for n in range(3):
        wake_file(tmp_path, f'wake-genome-{n}', start=T0 + 100 * n, end=T0 + 100 * n + 30, text=f'answer number {n}')
    dispatch_state(tmp_path, 'wake-genome-2', 'completed')
    view.log_step(tmp_path, 'genome', T0 + 400)
    replay = '\n'.join(view.log_step(tmp_path, 'genome', T0 + 500, replay=True))     # new tmux, same site
    assert 'answer number 0' in replay and 'answer number 2' in replay
    assert view.log_step(tmp_path, 'genome', T0 + 505) == []


def test_an_unreadable_log_and_a_missing_site_stay_visible_not_silent(tmp_path):
    wake_file(tmp_path, 'wake-genome-1', start=T0, end=T0 + 30, text='x')
    dispatch_state(tmp_path, 'wake-genome-1', 'completed')
    view.log_step(tmp_path, 'genome', T0 + 100)
    (tmp_path / 'pane-logs/genome.log').unlink()
    assert 'log UNKNOWN' in '\n'.join(view.log_step(tmp_path, 'genome', T0 + 110, replay=True))
    missing = tmp_path / 'nowhere'
    assert 'does not exist; nothing was written' in '\n'.join(view.log_step(missing, 'steward', T0))
    assert not missing.exists()                                  # no stray site tree is ever created


def test_log_rotates_and_history_limit_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(view, 'LOG_MAX_BYTES', 100)
    wake_file(tmp_path, 'wake-genome-1', start=T0, end=T0 + 30, text='a' * 300)
    dispatch_state(tmp_path, 'wake-genome-1', 'completed')
    view.log_step(tmp_path, 'genome', T0 + 100)
    wake_file(tmp_path, 'wake-genome-2', start=T0 + 200, end=T0 + 230, text='b' * 300)
    dispatch_state(tmp_path, 'wake-genome-2', 'completed')
    view.log_step(tmp_path, 'genome', T0 + 300)
    assert (tmp_path / 'pane-logs/genome.log.1').exists() and 'b' * 300 in (tmp_path / 'pane-logs/genome.log').read_text()
    from mishe_tauftauf import plant_tmux
    import inspect
    assert "'history-limit', '3000'" in inspect.getsource(plant_tmux.setup)


def test_forgetting_old_printed_wakes_never_reprints_them(tmp_path, monkeypatch):
    # Reviewer finding: trimming the printed set made trimmed wakes look new, so they were appended again.
    monkeypatch.setattr(view, 'LOG_KEEP_KEYS', 3)
    for n in range(8):
        wake_file(tmp_path, f'wake-genome-{n:03d}', start=T0 + 100 * n, end=T0 + 100 * n + 30, text=f'answer number {n}')
    dispatch_state(tmp_path, 'wake-genome-007', 'completed')
    view.log_step(tmp_path, 'genome', T0 + 2000)
    for n in range(8, 14):
        wake_file(tmp_path, f'wake-genome-{n:03d}', start=T0 + 100 * n, end=T0 + 100 * n + 30, text=f'answer number {n}')
        dispatch_state(tmp_path, f'wake-genome-{n:03d}', 'completed')
        step = '\n'.join(view.log_step(tmp_path, 'genome', T0 + 2000 + 100 * n))
        assert f'answer number {n}' in step and step.count('══ model') == 1
        assert view.log_step(tmp_path, 'genome', T0 + 2001 + 100 * n) == []
    log = (tmp_path / 'pane-logs/genome.log').read_text()
    assert all(log.count(f'answer number {n}\n') == 1 for n in range(5, 14))      # each exactly once


def test_plain_text_output_is_bounded_like_every_other_shape(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / 'wake.prompt.out.txt'
    out.write_text('line of plain output\n' * 200000)
    text = output_text(out)
    assert len(text) < 17000 and 'truncated to its last 16000 characters' in text


def test_wall_excerpt_is_never_labelled_whole_when_something_was_cut(tmp_path):
    # Reviewer finding: a long single-line or very large wall must not be presented as the whole wall.
    (tmp_path / 'walls').mkdir()
    wall = tmp_path / 'walls/genome.md'
    wall.write_text('short wall\nsecond line\n')
    assert 'whole wall' in view.render(tmp_path, 'genome', now=T0)
    wall.write_text('x' * 400 + '\n')
    text = view.render(tmp_path, 'genome', now=T0)
    assert 'whole wall' not in text and 'long lines shortened' in text
    wall.write_text('word ' * 10000)
    text = view.render(tmp_path, 'genome', now=T0)
    assert 'whole wall' not in text and 'only the first 20000 characters were read' in text


def test_an_idle_role_says_so_once_not_on_every_poll(tmp_path):
    # Reviewer finding: with no retained output the IDLE line was appended again every 5 seconds.
    first = view.log_step(tmp_path, 'steward', T0)
    assert any('IDLE / UNKNOWN' in c for c in first)
    for n in range(1, 6):
        assert view.log_step(tmp_path, 'steward', T0 + 5 * n) == []
    assert (tmp_path / 'pane-logs/steward.log').read_text().count('IDLE / UNKNOWN') == 1
    restarted = '\n'.join(view.log_step(tmp_path, 'steward', T0 + 100, replay=True))      # a new pane starts from the log
    assert restarted.count('IDLE / UNKNOWN') >= 1


def test_failures_in_the_log_follower_are_reported_once_not_every_poll(tmp_path, monkeypatch, capsys):
    wake_file(tmp_path, 'wake-genome-1', start=T0, end=T0 + 30, text='x')
    dispatch_state(tmp_path, 'wake-genome-1', 'completed')
    out = tmp_path / 'wakes/wake-genome-1.prompt.out.json'
    real = view.output_view
    def broken(path, *a, **k):
        raise OSError('disk gremlin')
    monkeypatch.setattr(view, 'output_view', broken)
    first = '\n'.join(view.log_step(tmp_path, 'genome', T0 + 100))
    assert first.count('disk gremlin') == 1
    assert view.log_step(tmp_path, 'genome', T0 + 105) == []                      # marked printed: not repeated
    # the loop's own failures: the same fault prints once until it changes or clears
    import sys
    monkeypatch.setattr(sys, 'argv', ['plant_view', '--home', str(tmp_path), '--role', 'genome', '--log', '--follow'])
    calls = []
    def failing(*a, **k):
        raise ValueError('same fault')
    monkeypatch.setattr(view, 'log_step', failing)
    def tick(_):
        calls.append(1)
        if len(calls) >= 4:
            raise KeyboardInterrupt
    monkeypatch.setattr(view.time, 'sleep', tick)
    with pytest.raises(KeyboardInterrupt):
        view.main()
    assert capsys.readouterr().out.count('View UNKNOWN: ValueError: same fault') == 1


def test_hostile_envelopes_degrade_to_unknown_and_never_kill_the_follower(tmp_path, capsys, monkeypatch):
    # Reviewer finding: an integer too large for a float (and deeply nested JSON) raised out of the pane process.
    from mishe_tauftauf.plant_view_boundary import output_text
    settled_wake(tmp_path, body={'result': 'still shown', 'duration_ms': 10 ** 400, 'total_cost_usd': 10 ** 400,
                                 'num_turns': -10 ** 400, 'usage': {'output_tokens': 10 ** 400}})
    text = view.render(tmp_path, 'genome', now=T0 + 60, output=True)
    assert 'still shown' in text and machine_syntax(text) == []
    deep = tmp_path / 'wakes/wake-genome-2.prompt.out.json'
    deep.write_text('[' * 200000 + ']' * 200000)
    assert 'UNKNOWN' in output_text(deep)
    import sys
    monkeypatch.setattr(sys, 'argv', ['plant_view', '--home', str(tmp_path), '--role', 'genome', '--log', '--follow'])
    def explode(*a, **k):
        raise RecursionError('maximum recursion depth exceeded')
    monkeypatch.setattr(view, 'log_step', explode)
    ticks = []
    def tick(_):
        ticks.append(1)
        if len(ticks) >= 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(view.time, 'sleep', tick)
    with pytest.raises(KeyboardInterrupt):
        view.main()
    assert capsys.readouterr().out.count('RecursionError') == 1          # reported, once; the loop kept running


def test_log_mode_renders_a_multi_event_jsonl_stream_as_readable_text(tmp_path):
    # Reviewer claim: .jsonl was handled as one JSON document. It goes through stream_text; this proves it end to end.
    key = 'wake-genome-9'
    write_json(yielding.snapshot_path(tmp_path, key), {'role': 'genome', 'snapshot': {}})
    os.utime(yielding.snapshot_path(tmp_path, key), (T0, T0))
    events = [{'type': 'agent_start'},
              {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'Reading the signals'}},
              {'type': 'tool_execution_start', 'toolName': 'bash', 'args': {'command': 'mishe signal show ci'}},
              {'type': 'tool_execution_end', 'result': {'content': [{'type': 'text', 'text': 'GREEN'}]}},
              {'type': 'message_end', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'CI is green; nothing to do.'}]}}]
    out = tmp_path / f'wakes/{key}.prompt.out.jsonl'
    out.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
    os.utime(out, (T0 + 30, T0 + 30))
    write_json(yielding.receipt_path(tmp_path, key), {'role': 'genome', 'key': key, 'result': 'changed', 'continue': False, 'settled_at': T0 + 30})
    dispatch_state(tmp_path, key, 'completed')
    text = '\n'.join(view.log_step(tmp_path, 'genome', T0 + 100))
    # A settled OMP wake reads like a Claude wake: the final message, with a count of the tool calls behind it.
    for expected in ('CI is green; nothing to do.', '(1 tool calls)'):
        assert expected in text
    assert '[tool output] GREEN' not in text
    assert 'malformed' not in text and 'UNKNOWN stream' not in text and 'raw tail' not in text   # not the single-document path
    assert 'assistantMessageEvent' not in text and machine_syntax(text) == []
    assert 'model' in text.splitlines()[1]          # the header still says which model (configured, as a stream has none)


def test_implausible_file_times_render_as_unknown_not_a_crash(tmp_path):
    # Reviewer finding: a finite but absurd timestamp (1e300) reached time.gmtime and raised OverflowError.
    settled_wake(tmp_path)
    write_json(yielding.receipt_path(tmp_path, 'wake-genome-1'),
               {'role': 'genome', 'key': 'wake-genome-1', 'result': 'changed', 'continue': False, 'settled_at': 1e300})
    intentions.add(tmp_path, 'genome', 'far', note='absurd due', due=1e300)
    intentions.add(tmp_path, 'genome', 'neg', note='absurd negative', due=-1e300)
    for output in (False, True):
        text = view.render(tmp_path, 'genome', now=T0 + 60, output=output)
        assert 'Traceback' not in text and machine_syntax(text) == []
    top = view.render(tmp_path, 'genome', now=T0 + 60)
    assert 'time UNKNOWN' in top and 'Next due:' in top


def test_a_mind_quoting_json_in_its_answer_is_prose_but_the_chrome_stays_strict(tmp_path):
    # Found by the delivery observer: a mind explained a bug by quoting a process's JSON argument inline.
    quote = 'The daemon argv carried a JSON object ({"label":"claude","pid":187610}) which is the cause.'
    settled_wake(tmp_path, body=envelope(result=quote))
    for output in (False, True):
        text = view.render(tmp_path, 'genome', now=T0 + 60, output=output)
        assert machine_syntax(text) == []
    chrome = '══ model claude-haiku-5-5 ══\n' + quote + '\nChanges in git during this wake:\n{"label":"claude"}\n'
    flagged = machine_syntax(chrome)
    assert len(flagged) == 1 and 'line 4' in flagged[0]                      # the same snippet outside the answer
    structure = '══ model x ══\nline one\n{\n  "a": 1\n}\n'
    assert len(machine_syntax(structure)) >= 2                                # structure lines are never prose
    prose = '══ model x ══\nrun .venv/bin/python at 1793818396 with {"k": 1} inline\nMade no commits\n'
    assert machine_syntax(prose) == []                                   # a mind may cite commands, times, snippets
    chrome_epoch = '══ model x ══\nanswer\nMade no commits\nsampled 1793818396 via .venv/bin/python\n'
    assert len(machine_syntax(chrome_epoch)) == 1                         # the renderer's own lines may not


def test_file_stem_is_the_dispatch_key_produced_by_the_real_generator(tmp_path):
    # Reviewer concern: file names and dispatch keys might not be the same string. Tie them to wake._key itself.
    from mishe_tauftauf import wake
    from mishe_tauftauf.plant_view_boundary import wake_key
    key = wake._key('genome', 1793818293409683255)
    assert key == 'wake-genome-1793818293409683255'
    assert wake_key(tmp_path / f'{key}.prompt.out.json') == key == wake_key(tmp_path / f'{key}.prompt.out.jsonl')
    assert yielding.snapshot_path(tmp_path, key).name == f'{key}.snapshot.json'
    assert yielding.receipt_path(tmp_path, key).name == f'{key}.yield.json'
    # end to end: the wake the dispatch record says is running is held back, then printed once it settles
    write_json(yielding.snapshot_path(tmp_path, key), {'role': 'genome', 'snapshot': {}})
    os.utime(yielding.snapshot_path(tmp_path, key), (T0, T0))
    out = tmp_path / f'wakes/{key}.prompt.out.json'
    out.write_text('{"half": "writ')
    dispatch_state(tmp_path, key, 'started')
    held = '\n'.join(view.log_step(tmp_path, 'genome', T0 + 10))
    assert 'writ' not in held and 'wake started' in held
    out.write_text(json.dumps(envelope(result='the settled answer')))
    write_json(yielding.receipt_path(tmp_path, key), {'role': 'genome', 'key': key, 'result': 'changed', 'continue': False, 'settled_at': T0 + 30})
    dispatch_state(tmp_path, key, 'completed')
    assert 'the settled answer' in '\n'.join(view.log_step(tmp_path, 'genome', T0 + 40))


def test_wall_excerpt_and_mail_are_the_minds_prose_but_signal_lines_are_chrome():
    # Found live: the Docs wall cites `.venv/bin/python` in a command; that is prose, not a rendering defect.
    top = '\n'.join([
        'docs — model claude-haiku-5-5 (configured) — view at 22:46Z',
        'Wall (first 5 of 12 lines; the full wall is canonical):',
        '- run `.venv/bin/python -m x` at 1793818396 and read {"a": 1}',
        'Signals RED (1): failed-x (3 min ago)',
        'Next due: 23:00Z (in 14 min) — note',
        'Unread mail for docs: 1',
        '#296500 see .venv/bin/python and 1793818396'])
    assert machine_syntax(top) == []
    leaked = top.replace('Signals RED (1): failed-x (3 min ago)', 'Signals RED (1): source=run:/x/.venv/bin/python observed=1793818396')
    assert len(machine_syntax(leaked)) == 1                               # chrome between the prose blocks is still strict
    dump = top.replace('- run `.venv/bin/python -m x`', '{\n  "a": 1\n}')
    assert len(machine_syntax(dump)) >= 1                                 # a dumped record is flagged even inside the wall


def test_pain_read_of_a_role_shows_the_plant_view_not_the_deleted_renderer_chain(tmp_path):
    # 2026-11-06: `pain read genome --launcher dashboard` printed 'No module named mishe_tauftauf.wall_view' and stale blocks (legacy
    # top-pains launchers); a mind's pane is plant_view, for the dashboard and for `pain read` alike.
    import subprocess, sys
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    home = tmp_path / ".mishe-tauftauf"
    assert subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "init"], capture_output=True, text=True).returncode == 0
    done = subprocess.run([sys.executable, "-m", "mishe_tauftauf", "--home", str(home), "pain", "read", "genome", "--launcher", "dashboard"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("genome \u2014 model ") and "wall_view" not in done.stdout + done.stderr and "renderer exit" not in done.stdout
    assert "Wall UNKNOWN" in done.stdout or "Wall (" in done.stdout            # a missing wall is UNKNOWN, never a renderer failure


def test_omp_tool_updates_replace_their_own_line_and_bookkeeping_is_quiet(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / "wake.prompt.out.jsonl"
    tool = lambda kind, text: {"type": kind, "toolName": "bash", "result": {"content": [{"type": "text", "text": text}]},
                               "partialResult": {"content": [{"type": "text", "text": text}]}}
    events = [{"type": "turn_start"}, {"type": "message_start"},
              {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "echo hi"}},
              tool("tool_execution_update", "hi"), tool("tool_execution_end", "hi\nhi done"),
              {"type": "turn_end"}]
    out.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
    text = output_text(out)
    assert text.count("[tool output]") == 1 and "hi done" in text
    assert "turn_start" not in text and "message_start" not in text and "turn_end" not in text


def test_running_wake_shows_a_progress_block_per_bucket(tmp_path):
    import os, time
    from mishe_tauftauf import plant_tape
    root = tmp_path / "wakes"; root.mkdir()
    (root / "wake-x-1.work-start.json").write_text(json.dumps({"role": "x"}))
    (root / "wake-x-1.prompt.out.jsonl").write_text(json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "still working"}}) + '\n')
    old = time.time() - 3 * plant_tape.PROGRESS_SECONDS
    os.utime(root / "wake-x-1.work-start.json", (old, old))
    cursor, text = plant_tape.wakes_step(tmp_path, None)
    assert "RUNNING x" in text and "still working" in text
    cursor2, text2 = plant_tape.wakes_step(tmp_path, cursor)
    assert "RUNNING" not in text2


def test_finished_omp_wake_reads_like_a_claude_wake(tmp_path):
    from mishe_tauftauf import plant_tape
    root = tmp_path / "wakes"; root.mkdir()
    (root / "wake-x-1.work.json").write_text(json.dumps({"role": "x"}))
    (root / "wake-x-1.yield.json").write_text(json.dumps({"role": "x", "result": "changed"}))
    ev = [{"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls"}},
          {"type": "tool_execution_end", "result": {"content": [{"type": "text", "text": "NOISE"}]}},
          {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Final summary here"}]}},
          {"type": "agent_end"}]
    (root / "wake-x-1.prompt.out.jsonl").write_text('\n'.join(json.dumps(e) for e in ev) + '\n')
    _, text = plant_tape.wakes_step(tmp_path, None)
    assert "Final summary here" in text and "(1 tool calls)" in text and "NOISE" not in text


def test_provider_retry_events_are_read_not_unknown(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_text
    out = tmp_path / "w.prompt.out.jsonl"
    ev = [{"type": "auto_retry_start", "attempt": 1, "errorMessage": "429 limit"},
          {"type": "auto_retry_end", "success": False, "finalError": "429 Go usage limit exceeded\nmore"}]
    out.write_text('\n'.join(json.dumps(e) for e in ev) + '\n')
    text = output_text(out)
    assert "UNKNOWN" not in text and "[provider error] 429 Go usage limit exceeded" in text


def test_running_claude_wake_progress_says_running_not_unknown(tmp_path):
    import os, time
    from mishe_tauftauf import plant_tape
    root = tmp_path / "wakes"; root.mkdir()
    (root / "wake-x-2.work-start.json").write_text(json.dumps({"role": "x"}))
    (root / "wake-x-2.prompt.out.json").write_text('')
    old = time.time() - 3 * plant_tape.PROGRESS_SECONDS
    os.utime(root / "wake-x-2.work-start.json", (old, old))
    _, text = plant_tape.wakes_step(tmp_path, None)
    assert "RUNNING x" in text and "UNKNOWN" not in text


def test_stale_unsettled_wake_record_is_not_shown_as_running(tmp_path):
    import os, time
    from mishe_tauftauf import plant_tape
    root = tmp_path / "wakes"; root.mkdir()
    (root / "wake-x-3.work-start.json").write_text(json.dumps({"role": "x"}))
    (root / "wake-x-3.prompt.out.json").write_text('')
    old = time.time() - 3 * plant_tape.PROGRESS_MAX_AGE
    os.utime(root / "wake-x-3.work-start.json", (old, old))
    _, text = plant_tape.wakes_step(tmp_path, None)
    assert "RUNNING" not in text


def test_progress_block_hides_the_retention_preamble(tmp_path):
    import os, time
    from mishe_tauftauf import plant_tape
    root = tmp_path / "wakes"; root.mkdir()
    (root / "wake-x-4.work-start.json").write_text(json.dumps({"role": "x"}))
    big = json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "x" * 2000}}) + '\n'
    (root / "wake-x-4.prompt.out.jsonl").write_text(big * 1200 + json.dumps({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls"}}) + '\n')
    old = time.time() - 3 * plant_tape.PROGRESS_SECONDS
    os.utime(root / "wake-x-4.work-start.json", (old, old))
    _, text = plant_tape.wakes_step(tmp_path, None)
    assert "RUNNING x" in text and "[tool bash] ls" in text and "retained tail" not in text and "UNKNOWN leading" not in text


def test_wake_without_a_final_message_shows_the_failure_not_the_stream(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_view
    out = tmp_path / "w.prompt.out.jsonl"
    ev = [{"type": "tool_execution_start", "toolName": "read", "args": {"path": "a.py"}},
          {"type": "tool_execution_end", "result": {"content": [{"type": "text", "text": "SOURCE LISTING\n" * 50}]}},
          {"type": "auto_retry_end", "success": False, "finalError": "usage_limit_reached"},
          {"type": "agent_end"}]
    out.write_text('\n'.join(json.dumps(e) for e in ev) + '\n')
    _, text = output_view(out, final=True)
    assert "SOURCE LISTING" not in text and "usage_limit_reached" in text and "without a final message" in text


def test_progress_shows_words_and_last_call_not_tool_output(tmp_path):
    import os, time
    from mishe_tauftauf import plant_tape
    root = tmp_path / "wakes"; root.mkdir()
    (root / "wake-x-5.work-start.json").write_text(json.dumps({"role": "x"}))
    ev = [{"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Checking the router commit"}]}},
          {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "git show --stat HEAD"}},
          {"type": "tool_execution_end", "result": {"content": [{"type": "text", "text": "Wall time: 0.03 seconds\nNOISE LISTING"}]}}]
    (root / "wake-x-5.prompt.out.jsonl").write_text('\n'.join(json.dumps(e) for e in ev) + '\n')
    old = time.time() - 3 * plant_tape.PROGRESS_SECONDS
    os.utime(root / "wake-x-5.work-start.json", (old, old))
    _, text = plant_tape.wakes_step(tmp_path, None)
    assert "says: Checking the router commit" in text and "last call: [tool bash] git show --stat HEAD" in text
    assert "NOISE LISTING" not in text and "Wall time" not in text


def test_terse_closing_line_is_shown_with_what_came_before(tmp_path):
    from mishe_tauftauf.plant_view_boundary import output_view
    out = tmp_path / "w.prompt.out.jsonl"
    msg = lambda t: {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}}
    out.write_text('\n'.join(json.dumps(e) for e in [msg("Found the stale probe and cleared it, evidence in the wall."), {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "x"}}, msg("Yielded verified."), {"type": "agent_end"}]) + '\n')
    _, text = output_view(out, final=True)
    assert "Found the stale probe" in text and "Yielded verified." in text
