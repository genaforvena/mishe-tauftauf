"""Tests for the one-shot Option A omp seam adapter.

No model call: `run` is a fake returning recorded outcomes. Stream fixtures
mirror the raw artifacts captured on the live nvidia route
(artifacts/inference-research-optiona-json-20261016.json and the
optiona-errmodel stderr), reduced to the fields the adapter reads.
"""
import subprocess
from pathlib import Path

import pytest

from mishe_tauftauf.inference_optiona import optiona_call

SUCCESS_STREAM = b"""{"type":"session","version":3,"id":"s1"}
{"type":"agent_start"}
{"type":"turn_start"}
{"type":"message_start","message":{"role":"user","content":[{"type":"text","text":"Reply with exactly the single word: seam-ok"}]}}
{"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"Reply with exactly the single word: seam-ok"}]}}
{"type":"message_start","message":{"role":"assistant","content":[{"type":"text","text":"partial"}],"provider":"nvidia","model":"z-ai/glm-5.3"}}
{"type":"message_update","assistantMessageEvent":{"type":"text_delta","contentIndex":0,"delta":"seam-ok"}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"seam-ok"}],"provider":"nvidia","model":"z-ai/glm-5.3","usage":{"input":3044,"output":5,"totalTokens":3049},"stopReason":"stop","responseId":"chatcmpl-bf0a8daa-75b3-4b42-9976-cc451b908375","credentialId":4}}
{"type":"turn_end","message":{"role":"assistant","usage":{"input":3044,"output":5,"totalTokens":3049}}}
{"type":"agent_end","isTerminal":true,"yielded":true}
"""

TWO_ASSISTANT_STREAM = b"""{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"first answer"}],"stopReason":"stop"}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"second answer"}],"stopReason":"length","usage":{"input":10,"output":20},"provider":"nvidia","model":"z-ai/glm-5.3","responseId":"r2"}}
"""

ERR_STDERR = (b'Model "nvidia/no-such-model-xyz" not found\n\n'
              b'Run `omp models find <pattern>` to search, or `omp models` '
              b'to list all.\n')


class Completed:
    def __init__(self, *, returncode=0, stdout=b'', stderr=b''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRun:
    def __init__(self, outcome):
        self.outcome = outcome
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def call(fake, **kwargs):
    defaults = dict(prompt='hello', model='nvidia/z-ai/glm-5.3',
                   config=Path('/abs/overlay.yml'),
                   cwd=Path('/repo'))
    defaults.update(kwargs)
    return optiona_call(defaults.pop('prompt'), run=fake, **defaults), fake


def test_success_extracts_last_assistant_message_end():
    result, fake = call(FakeRun(Completed(stdout=SUCCESS_STREAM)))
    assert result['ok'] is True
    assert result['kind'] == 'ok'
    assert result['text'] == 'seam-ok'
    assert result['stop_reason'] == 'stop'
    assert result['usage'] == {'input': 3044, 'output': 5,
                               'totalTokens': 3049}
    assert result['provider'] == 'nvidia'
    assert result['model'] == 'z-ai/glm-5.3'
    assert result['response_id'] == 'chatcmpl-bf0a8daa-75b3-4b42-9976-cc451b908375'
    assert result['returncode'] == 0
    assert result['stderr_tail'] is None
    assert fake.commands[0][0] == 'omp'


def test_last_assistant_message_end_wins():
    result, _ = call(FakeRun(Completed(stdout=TWO_ASSISTANT_STREAM)))
    assert result['ok'] is True
    assert result['text'] == 'second answer'
    assert result['stop_reason'] == 'length'
    assert result['response_id'] == 'r2'


def test_model_not_found_failure():
    result, _ = call(FakeRun(Completed(returncode=1, stderr=ERR_STDERR)))
    assert result['ok'] is False
    assert result['kind'] == 'provider'
    assert result['text'] is None
    assert result['returncode'] == 1
    assert 'not found' in result['stderr_tail']
    assert 'no-such-model-xyz' in result['stderr_tail']


def test_rc0_without_assistant_message_end_is_failure():
    stream = b'{"type":"session","id":"s1"}\n{"type":"turn_start"}\n'
    result, _ = call(FakeRun(Completed(stdout=stream)))
    assert result['ok'] is False
    assert result['kind'] == 'no-assistant-message'
    assert result['text'] is None
    assert result['returncode'] == 0


def test_malformed_line_is_failure_with_line_preserved():
    stream = (SUCCESS_STREAM.splitlines()[0] + b'\n' +
              b'{"type":"message_end","message":{"role":"assistant",')
    result, _ = call(FakeRun(Completed(stdout=stream)))
    assert result['ok'] is False
    assert result['kind'] == 'malformed-stream'
    assert b'"role":"assistant"' in result['malformed']
    assert result['returncode'] == 0


def test_user_message_end_alone_is_not_success():
    stream = b'{"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"hi"}]}}\n'
    result, _ = call(FakeRun(Completed(stdout=stream)))
    assert result['ok'] is False
    assert result['kind'] == 'no-assistant-message'


def test_timeout_is_reported_not_raised():
    exc = subprocess.TimeoutExpired(cmd='omp', timeout=30,
                                   stderr=b'partial stderr')
    result, _ = call(FakeRun(exc))
    assert result['ok'] is False
    assert result['kind'] == 'timeout'
    assert result['text'] is None


def test_oversized_stdout_is_failure():
    result, _ = call(FakeRun(Completed(stdout=b'x' * 33)),
                     max_output=32)
    assert result['ok'] is False
    assert result['kind'] == 'output-limit'


def test_argv_flags_and_absolute_config():
    _, fake = call(FakeRun(Completed(stdout=SUCCESS_STREAM)))
    command = fake.commands[0]
    assert '--model' in command and 'nvidia/z-ai/glm-5.3' in command
    for flag in ('--thinking', '--mode', 'json', '--print', '--no-tools',
                 '--no-session', '--no-prewalk'):
        assert flag in command, command
    assert command[-3] == '--cwd'
    assert command[-2] == '/repo'
    assert command[-1] == 'hello'
    config_index = command.index('--config')
    assert command[config_index + 1] == '/abs/overlay.yml'


def test_relative_config_is_rejected():
    with pytest.raises(ValueError, match='absolute'):
        optiona_call('p', model='m', config=Path('rel/overlay.yml'),
                     cwd=Path('/repo'), run=FakeRun(Completed()))


def test_no_config_omits_flag():
    _, fake = call(FakeRun(Completed(stdout=SUCCESS_STREAM)), config=None)
    assert '--config' not in fake.commands[0]


def test_rc_nonzero_ignores_valid_assistant_stdout():
    # A provider failure must not become success even when stdout carries a
    # complete assistant message_end (witness reading, gap 1).
    result, _ = call(FakeRun(Completed(returncode=1, stdout=SUCCESS_STREAM,
                                       stderr=ERR_STDERR)))
    assert result['ok'] is False
    assert result['kind'] == 'provider'
    assert result['text'] is None
    assert result['returncode'] == 1


def test_valid_json_non_object_line_is_malformed_not_raised():
    # Witness Finding 1: json.loads accepts null/list/scalars; those are
    # malformed stream lines and must fail honestly, not raise AttributeError.
    for raw in (b'null', b'[1,2,3]', b'"hi"', b'42'):
        stream = SUCCESS_STREAM + raw + b'\n'
        result, _ = call(FakeRun(Completed(stdout=stream)))
        assert result['ok'] is False, raw
        assert result['kind'] == 'malformed-stream', raw
        assert result['malformed'] == raw, raw


def test_absent_usage_stop_reason_response_id_stay_none():
    # Witness reading, gap 2: absent fields stay None (UNKNOWN), never zero.
    stream = (b'{"type":"message_end","message":{"role":"assistant",'
              b'"content":[{"type":"text","text":"bare"}]}}\n')
    result, _ = call(FakeRun(Completed(stdout=stream)))
    assert result['ok'] is True
    assert result['text'] == 'bare'
    assert result['stop_reason'] is None
    assert result['usage'] is None
    assert result['response_id'] is None


def test_oversized_stdout_failure_keeps_returncode_and_stderr():
    # Witness reading, gap 3: distinguish the size guard from a mere parse
    # failure by asserting the failure is attributed to the size limit.
    result, _ = call(FakeRun(Completed(returncode=0, stdout=b'x' * 33,
                                       stderr=b'big')),
                     max_output=32)
    assert result['ok'] is False
    assert result['kind'] == 'output-limit'
    assert result['returncode'] == 0
    assert 'malformed' not in result
    assert result['stderr_tail'] == 'big'
