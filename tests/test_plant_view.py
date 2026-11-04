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
