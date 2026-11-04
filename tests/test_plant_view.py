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
