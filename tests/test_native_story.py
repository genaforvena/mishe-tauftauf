import copy
import io
import os
import select
import termios

import pytest

from mishe_tauftauf.native_story import NativeStory, quiet_input


def call(name, **arguments):
    return {'type': 'toolCall', 'id': 'opaque-provider-id',
            'name': name, 'arguments': arguments}


def outcome(value, *, status='completed', tool_status='completed', **fields):
    return {'status': status, 'result': {'status': tool_status, 'result': value}, **fields}


def story(tmp_path, **kwargs):
    stream = io.StringIO()
    return NativeStory(stream, role='docs', wake=58491, evidence_dir=tmp_path, **kwargs), stream


def test_file_story_preserves_range_content_and_only_visible_narrative(tmp_path):
    view, stream = story(tmp_path)
    read = call('read_file', path='docs/日本語.md', start=10, count=21)
    events = [
        {'kind': 'checkpoint', 'state': {'context': 'SECRET_CHECKPOINT'}},
        {'kind': 'model_input', 'turn': 0, 'context': {'systemPrompt': 'SECRET_CONTEXT'}},
        {'kind': 'model_output', 'turn': 0, 'response': {
            'terminal': 'done', 'assistant': {'role': 'assistant', 'content': [
                {'type': 'thinking', 'thinking': 'SECRET_THINKING'},
                {'type': 'text', 'text': 'I will inspect the relevant document.'},
                read], 'providerPayload': {'private': 'SECRET_PROVIDER'}}}},
        {'kind': 'proposal', 'turn': 0, 'call': read},
        {'kind': 'tool_result', 'message': {'content': [
            {'type': 'text', 'text': 'SECRET_OPAQUE_MESSAGE'}]}},
    ]
    result = outcome('10: A useful 日本語 sentence.\n30: End of requested range.\n[total lines: 70]')
    original = copy.deepcopy((events, read, result))
    for event in events:
        view.event(event)
    before_execution = stream.getvalue()
    assert 'waiting' in before_execution.lower()
    assert 'I will inspect' in before_execution
    assert 'docs/日本語.md' in before_execution and '10-30' in before_execution
    assert 'not executed' in before_execution.lower()
    assert 'completed effect' not in before_execution.lower()
    view.start(read)
    view.result(read, result)
    rendered = stream.getvalue()
    assert '10: A useful 日本語 sentence.' in rendered
    assert '30: End of requested range.' in rendered
    assert 'SECRET_' not in rendered
    assert 'opaque-provider-id' not in rendered
    assert (events, read, result) == original


def test_nonzero_command_keeps_exit_stderr_and_full_evidence_when_truncated(tmp_path):
    view, stream = story(tmp_path, max_chars=100)
    command = call('exec', cmd='rtk proxy failing-command')
    prefix = tmp_path / 'exec-retained'
    view.start(command)
    view.result(command, outcome({
        'returncode': 7, 'stdout': 'large output ' * 200, 'stderr': 'permission denied',
        'truncated': True, 'full_output_prefix': str(prefix),
    }))
    rendered = stream.getvalue()
    assert 'rtk proxy failing-command' in rendered
    assert 'FAILED' in rendered and 'exit 7' in rendered
    assert 'stderr' in rendered and 'permission denied' in rendered
    assert 'truncated' in rendered.lower()
    assert str(prefix) + '.stdout' in rendered
    assert str(prefix) + '.stderr' in rendered
    assert 'large output ' * 200 not in rendered


@pytest.mark.parametrize(('status', 'tool_status'), [
    ('partial', 'completed'), ('unknown', 'completed'), ('not-started', 'completed'),
    ('completed', 'unknown'), ('future-status', 'completed'),
])
def test_unknown_effects_never_become_success_and_future_tools_remain_readable(
        tmp_path, status, tool_status):
    view, stream = story(tmp_path)
    future = call('future_tool', resource='owned/path', mode={'purpose': 'inspect'})
    value = outcome({'finding': 'retained partial evidence'}, status=status,
                    tool_status=tool_status, failure='capability interrupted',
                    reconciliation_ref='/evidence/original-intent.json')
    before = copy.deepcopy((future, value))
    view.start(future)
    view.result(future, value)
    rendered = stream.getvalue()
    assert 'UNKNOWN' in rendered
    assert status in rendered and tool_status in rendered
    assert 'future_tool' in rendered and 'owned/path' in rendered and 'inspect' in rendered
    assert 'retained partial evidence' in rendered
    assert 'capability interrupted' in rendered
    assert '/evidence/original-intent.json' in rendered
    assert 'Completed effect' not in rendered
    assert (future, value) == before


def test_controls_are_inert_in_headers_arguments_results_and_errors(tmp_path):
    stream = io.StringIO()
    hostile = '\x1b[2J\x1b[H\r\b\x00\x9b2J\u202e'
    view = NativeStory(stream, role='docs' + hostile, wake=58491, evidence_dir=tmp_path)
    command = call('exec', cmd='rtk echo café\t日本語' + hostile)
    view.start(command)
    view.result(command, outcome({'returncode': 0, 'stdout': hostile + '\nvisible\ttext',
                                  'stderr': hostile, 'truncated': False}))
    view.failure(command, RuntimeError('timed out ' + hostile))
    view.event({'kind': 'model_failure', 'error': {'message': hostile}})
    rendered = stream.getvalue()
    for char in ('\x1b', '\r', '\b', '\x00', '\x9b', '\u202e'):
        assert char not in rendered
    assert '\\x1b[2J' in rendered
    assert 'café\t日本語' in rendered and '\nvisible\ttext' in rendered
    assert 'RuntimeError' in rendered and 'timed out' in rendered
    assert 'UNKNOWN' in rendered and 'FAILED' in rendered


def test_provider_terminal_error_retains_reason_without_private_payload(tmp_path):
    view, stream = story(tmp_path)
    view.event({'kind': 'model_output', 'turn': 2, 'response': {
        'terminal': 'error', 'assistant': {
            'role': 'assistant', 'content': [], 'stopReason': 'error',
            'errorMessage': 'ECONNRESET: socket closed\x1b[2J',
            'providerPayload': {'private': 'SECRET_PROVIDER'}},
        'checkpoint': 'SECRET_CHECKPOINT'}})
    view.event({'kind': 'stop', 'status': 'unknown', 'turns': 3, 'calls': 6})
    rendered = stream.getvalue()
    assert 'ECONNRESET: socket closed' in rendered
    assert '\\x1b[2J' in rendered and '\x1b' not in rendered
    assert 'UNKNOWN' in rendered and 'not observed' in rendered
    assert 'SECRET_' not in rendered


def test_only_completed_seed_effect_records_settlement_not_selection_or_loop_stop(tmp_path):
    view, stream = story(tmp_path)
    settle = call('seed_yield', result='verified', continue_work=True)
    view.event({'kind': 'proposal', 'call': settle})
    view.start(settle)
    view.result(settle, outcome('ambiguous CLI output', status='partial'))
    view.event({'kind': 'stop', 'status': 'complete', 'turns': 1, 'calls': 1})
    before = stream.getvalue()
    assert 'verified' in before and 'continue_work=true' in before
    assert 'not observed' in before
    assert 'Canonical wake settlement observed' not in before
    view.result(settle, outcome('CLI settlement evidence'))
    view.event({'kind': 'stop', 'status': 'complete', 'turns': 2, 'calls': 2})
    after = stream.getvalue()[len(before):]
    assert 'settlement observed' in after
    assert 'CLI settlement evidence' in after
    assert 'not observed' not in after


def test_quiet_input_disables_only_echo_and_restores_after_exception():
    master, slave = os.openpty()
    try:
        with os.fdopen(slave, 'r', encoding='utf-8') as stream:
            original = termios.tcgetattr(stream.fileno())
            original[3] |= termios.ECHO
            termios.tcsetattr(stream.fileno(), termios.TCSANOW, original)
            with pytest.raises(RuntimeError, match='leave input lifetime'):
                with quiet_input(stream):
                    quiet = termios.tcgetattr(stream.fileno())
                    expected = original.copy()
                    expected[3] &= ~termios.ECHO
                    assert quiet == expected
                    os.write(master, b'private wake packet\n')
                    assert select.select([stream], [], [], 1)[0]
                    assert os.read(stream.fileno(), 4096) == b'private wake packet\n'
                    assert not select.select([master], [], [], 0.05)[0]
                    raise RuntimeError('leave input lifetime')
            assert termios.tcgetattr(stream.fileno()) == original
            os.write(master, b'visible input\n')
            assert select.select([master], [], [], 1)[0]
            assert b'visible input' in os.read(master, 4096)
    finally:
        os.close(master)
    non_tty = io.StringIO('unchanged prompt\n')
    with quiet_input(non_tty) as stream:
        assert stream.read() == 'unchanged prompt\n'
