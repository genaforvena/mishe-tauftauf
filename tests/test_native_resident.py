"""Consumer-visible native resident admission, evidence and idle boundaries."""
import io
import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import native_resident as resident
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.inference_effects import EffectBoundary
from mishe_tauftauf.inference_worker import WorkerError, WorkerTurn


@pytest.fixture
def config(tmp_path, monkeypatch):
    for name in ('MISHE_SEED_HOME', 'MISHE_NATIVE_RECOVERY_WAKE', 'MISHE_NATIVE_RECOVERY_ATTEMPT'):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / 'site'
    home.mkdir()
    (home / 'chat.log').touch()
    for directory in ('charters', 'minds', 'journals'):
        (home / directory).mkdir()
    for role in ('custom-mind', 'peer-mind'):
        (home / 'charters' / f'{role}.md').write_text(f'Charter for {role}\n')
        launcher = home / 'minds' / role
        launcher.write_text('#!/bin/sh\nexit 0\n')
        launcher.chmod(0o755)
    modules = tmp_path / 'node_modules'
    for name in ('pi-ai', 'pi-catalog', 'pi-utils', 'pi-coding-agent'):
        package = modules / '@oh-my-pi' / name
        package.mkdir(parents=True)
        (package / 'package.json').write_text('{}')
    return resident.validate_config(home, 'custom-mind', 'provider/custom-model',
                                    Path('/bin/true'), modules, 4, 8, 5)


def pending(config):
    return Feed(config.home).append('seed', f'seed wake {config.role} observation=1\nActual owed work.').sequence


def header(config, wake):
    return f'Current charter and evidence.\nWAKE {wake} for {config.role}; sensor record 1. Read the current dashboard.\nOBLIGATION\nActual owed work.\n'


def call(capability, **arguments):
    return {'type': 'toolCall', 'id': f'call-{capability}', 'name': capability, 'arguments': arguments}


def active(config):
    wake = pending(config)
    attempt = config.directory / 'wakes' / str(wake)
    attempt.mkdir(parents=True)
    return resident.Wake(config, wake, attempt)


@pytest.mark.parametrize('changed', [
    {'model': 'implicit-model'}, {'model': 'provider/model with space'},
    {'max_turns': True}, {'max_calls': 0}, {'timeout': float('inf')},
    {'timeout': 0}, {'reasoning': 'invented'}, {'bun': None},
    {'node_modules': None}, {'bun': Path('relative-bun')}, {'role': 'not-registered'},
])
def test_invalid_configuration_does_not_create_runtime_state(config, changed):
    values = {key: getattr(config, key) for key in (
        'home', 'role', 'model', 'bun', 'node_modules', 'max_turns', 'max_calls', 'timeout', 'reasoning')}
    before = sorted(path.relative_to(config.home) for path in config.home.rglob('*'))
    values.update(changed)
    with pytest.raises(ValueError):
        resident.validate_config(**values)
    assert sorted(path.relative_to(config.home) for path in config.home.rglob('*')) == before


def test_empty_dependency_directory_is_rejected_before_writes(config, tmp_path):
    empty = tmp_path / 'empty-modules'
    empty.mkdir()
    with pytest.raises(ValueError, match='missing required'):
        resident.validate_config(config.home, config.role, config.model, config.bun, empty)
    assert not config.directory.exists()


@pytest.mark.parametrize('submission', [
    'Please do arbitrary work.\n',
    'WAKE 23 for another-role; sensor record 1.\n',
    'WAKE 1 for custom-mind; sensor record 1.\nWAKE 2 for custom-mind; sensor record 1.\n',
])
def test_noncanonical_or_ambiguous_submission_cannot_authorize_work(config, submission):
    pending(config)
    with pytest.raises((ValueError, RuntimeError)):
        resident.run_wake(config, submission, ['never-started'])
    assert not config.busy.exists()
    assert not config.directory.exists()


def test_stale_wake_prompt_cannot_run_new_pending_work(config):
    old = pending(config)
    pending(config)
    with pytest.raises(RuntimeError, match='matching actual pending'):
        resident.run_wake(config, header(config, old), ['never-started'])
    assert not config.directory.exists()


def test_dashboard_is_required_independently_of_model_tool_offer(config):
    state = active(config)
    with pytest.raises(ValueError, match='dashboard'):
        state.execute(call('write_note', name='unauthorized.md', content='not written'))
    assert not (state.attempt / 'unauthorized.md').exists()


@pytest.mark.parametrize('selection', [
    call('write_note', name='extra.md', content='not written', extra='surprise'),
    call('write_note', name='wrong-type.md', content=42),
    call('read_file', path='README.md', start=True),
    call('exec', cmd='echo never', timeout=181),
    call('wall_dm', to='imaginary-role', content='not sent'),
])
def test_invalid_tool_arguments_are_rejected_before_effects(config, selection):
    state = active(config)
    state.dashboard_read = True
    with pytest.raises(ValueError):
        state.execute(selection)
    assert list(state.attempt.iterdir()) == []


def test_owned_read_failures_remain_unknown_in_effect_evidence(config):
    state = active(config)
    state.dashboard_read = True
    boundary = EffectBoundary(state.attempt / 'effects', writer_id='test-resident',
        obligation={'source': 'seed', 'role': config.role, 'wake': state.wake},
        execute=state.execute, authority='owned-custom-role')
    outcome = boundary(call('read_file', path='missing-local-file.md'))
    # A completed observation of an error must not masquerade as retrieved text.
    assert outcome['status'] == 'completed'
    retained = '\n'.join(path.read_text() for path in (state.attempt / 'effects').rglob('*.json*'))
    assert 'UNKNOWN' in retained
    assert 'FileNotFoundError' in retained
    assert state.read_errors == 1


def test_immutable_note_cannot_overwrite_prior_evidence(config):
    state = active(config)
    state.dashboard_read = True
    state.execute(call('write_note', name='finding.md', content='original finding'))
    with pytest.raises(FileExistsError):
        state.execute(call('write_note', name='finding.md', content='replacement'))
    assert (state.attempt / 'finding.md').read_text() == 'original finding'


def test_pending_change_revokes_effect_authority(config):
    state = active(config)
    state.dashboard_read = True
    pending(config)
    with pytest.raises(RuntimeError, match='wake changed'):
        state.execute(call('write_note', name='stale.md', content='must not write'))
    assert not (state.attempt / 'stale.md').exists()


def test_settled_state_revokes_later_effects_in_same_turn(config):
    state = active(config)
    state.dashboard_read = True
    state.settled = True
    with pytest.raises(RuntimeError, match='already settled'):
        state.execute(call('wall_write', content='must not replace settled wall'))
    assert list(state.attempt.iterdir()) == []


def test_cancellation_and_deadline_refuse_new_effects(config):
    state = active(config)
    state.dashboard_read = True
    config.cancel.touch()
    with pytest.raises(RuntimeError, match='stopped'):
        state.execute(call('write_note', name='cancelled.md', content='must not write'))
    config.cancel.unlink()
    state.deadline = time.monotonic() - 1
    with pytest.raises(RuntimeError, match='stopped'):
        state.execute(call('write_note', name='expired.md', content='must not write'))
    assert list(state.attempt.iterdir()) == []


def shell_proxy(config, monkeypatch):
    directory = config.root / 'test-bin'
    directory.mkdir()
    proxy = directory / 'rtk'
    proxy.write_text('#!/bin/sh\n[ \"$1\" = proxy ] || exit 90\nshift\nexec \"$@\"\n')
    proxy.chmod(0o755)
    monkeypatch.setenv('PATH', str(directory) + os.pathsep + os.environ['PATH'])


def test_shell_retains_full_output_and_nonzero_exit_without_retry(config, monkeypatch):
    shell_proxy(config, monkeypatch)
    state = active(config)
    state.dashboard_read = True
    result = state.execute(call('exec', cmd="printf '%040000d' 0; printf failure >&2; exit 7"))['result']
    assert result['returncode'] == 7
    assert result['stdout'] == '0' * 32000
    assert result['stderr'] == 'failure'
    assert result['truncated'] is True
    prefix = config.root / result['full_output_prefix']
    assert Path(f'{prefix}.stdout').read_text() == '0' * 40000
    assert Path(f'{prefix}.stderr').read_text() == 'failure'


def test_shell_is_bounded_by_whole_wake_deadline_and_retains_partial_output(config, monkeypatch):
    shell_proxy(config, monkeypatch)
    state = active(config)
    state.dashboard_read = True
    state.deadline = time.monotonic() + 1
    with pytest.raises(RuntimeError, match='partial effects UNKNOWN'):
        state.execute(call('exec', cmd='printf started; sleep 20; printf must-not-finish', timeout=180))
    prefix = state.attempt / ('exec-' + b'call-exec'.hex())
    assert Path(f'{prefix}.stdout').read_text() == 'started'
    assert Path(f'{prefix}.stderr').read_text() == ''


def test_policy_observation_names_actual_lifecycle_and_configured_budgets(config):
    state = active(config)
    decision = resident.turn_policy(state, {'remaining_turns': 3, 'remaining_calls': 7})
    assert decision['tools'] == ['pain_read']
    assert f'actual wake={state.wake}; current pending={state.wake}' in decision['notice']
    assert '4 turns/8 calls/5s' in decision['notice']
    assert str(config.cancel) in decision['notice']
    pending(config)
    with pytest.raises(RuntimeError, match='pending wake changed'):
        resident.turn_policy(state, {'remaining_turns': 2, 'remaining_calls': 7})


def test_recovery_requires_exact_explicit_wake_and_unique_attempt(config, monkeypatch):
    wake = pending(config)
    ordinary = resident._attempt(config, wake)
    ordinary.mkdir(parents=True)
    # An attempt directory with no recorded evidence cannot be read as effect-free.
    with pytest.raises(RuntimeError, match='reconciliation'):
        resident.run_wake(config, header(config, wake), ['never-started'])
    monkeypatch.setenv('MISHE_NATIVE_RECOVERY_WAKE', str(wake + 1))
    with pytest.raises(ValueError, match='exact actual'):
        resident._attempt(config, wake)
    monkeypatch.setenv('MISHE_NATIVE_RECOVERY_WAKE', str(wake))
    recovery = resident._attempt(config, wake)
    assert recovery.name == f'{wake}-recovery-1'
    recovery.mkdir()
    with pytest.raises(FileExistsError):
        resident.run_wake(config, header(config, wake), ['never-started'])
    assert not config.busy.exists()


class TransientFailureSession:
    """A provider turn that dies with a transient upstream failure."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def turn(self, context):
        failure = WorkerError('upstream socket closed unexpectedly')
        failure.fetch_ledger = {'allowance': 1, 'dispatches': 1,
                                'upstream_failure': {'kind': 'network',
                                                    'message': 'ECONNRESET'}}
        raise failure


def _recorded_attempt(config, wake, *, dispatched=0, transient=True):
    """Write the recorded bytes of one failed attempt for this wake."""
    attempt = config.directory / 'wakes' / str(wake)
    (attempt / 'effects').mkdir(parents=True)
    rows = [{'kind': 'model_failure', 'error': {'type': 'WorkerError', 'message': 'socket closed'},
             'fetch_ledger': {'upstream_failure': {'kind': 'network', 'message': 'ECONNRESET'}}}]
    (attempt / 'journal.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    for name in ('intents', 'starts', 'outcomes'):
        (attempt / 'effects' / f'{name}.jsonl').write_text(
            ''.join(json.dumps({'operation_id': f'op-{index}'}) + '\n'
                    for index in range(dispatched if name != 'intents' else 0)))
    (attempt / 'failure.txt').write_text(
        'Traceback (most recent call last):\n  File "worker.py", line 1, in run\nWorkerError: socket closed\n')
    (attempt / 'result.json').write_text(json.dumps({
        'wake': wake, 'settled': False, 'status': 'unknown',
        'fetch_ledger': ([{'upstream_failure': {'kind': 'network', 'message': 'ECONNRESET'}}]
                         if transient else [])}))
    return attempt


def test_transient_failure_releases_the_busy_wedge(config, monkeypatch):
    wake = pending(config)
    monkeypatch.setattr(resident, 'NativeSession', TransientFailureSession)
    with pytest.raises(WorkerError):
        resident.run_wake(config, header(config, wake), ['unused-provider'])
    attempt = config.directory / 'wakes' / str(wake)
    assert (attempt / 'failure.txt').exists()
    # No effect was admitted, so the wedge would strand the role on nothing.
    assert not config.busy.exists()


def test_effect_free_transient_attempt_allows_a_bounded_fresh_attempt(config, monkeypatch):
    wake = pending(config)
    _recorded_attempt(config, wake)
    monkeypatch.setattr(resident, 'NativeSession', TransientFailureSession)
    with pytest.raises(WorkerError):
        resident.run_wake(config, header(config, wake), ['unused-provider'])
    fresh = config.directory / 'wakes' / f'{wake}-retry-1'
    assert fresh.is_dir()
    # The earlier journal is retained and never reused.
    earlier = config.directory / 'wakes' / str(wake)
    assert (earlier / 'journal.jsonl').exists()
    assert not (fresh / 'journal.jsonl').read_text() == (earlier / 'journal.jsonl').read_text()
    assert not config.busy.exists()


def test_effect_present_attempt_refuses_a_fresh_attempt(config, monkeypatch):
    wake = pending(config)
    _recorded_attempt(config, wake, dispatched=2)
    monkeypatch.setattr(resident, 'NativeSession', TransientFailureSession)
    with pytest.raises(RuntimeError, match='reconciliation'):
        resident.run_wake(config, header(config, wake), ['unused-provider'])
    assert not (config.directory / 'wakes' / f'{wake}-retry-1').exists()
    assert not config.busy.exists()


def test_recovery_is_bounded(config, monkeypatch):
    wake = pending(config)
    _recorded_attempt(config, wake)
    monkeypatch.setattr(resident, 'NativeSession', TransientFailureSession)
    for number in range(1, 4):
        with pytest.raises(WorkerError):
            resident.run_wake(config, header(config, wake), ['unused-provider'])
        assert (config.directory / 'wakes' / f'{wake}-retry-{number}').is_dir()
    with pytest.raises(RuntimeError, match='exhausted'):
        resident.run_wake(config, header(config, wake), ['unused-provider'])


def test_incomplete_model_terminal_retains_busy_and_failure_evidence(config, monkeypatch):
    wake = pending(config)

    class IncompleteSession:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def turn(self, context):
            return WorkerTurn('done', {'role': 'assistant', 'stopReason': 'stop',
                                       'content': [{'type': 'text', 'text': 'All done without settlement.'}]})

    monkeypatch.setattr(resident, 'NativeSession', IncompleteSession)
    with pytest.raises(RuntimeError, match='incomplete'):
        resident.run_wake(config, header(config, wake), ['unused-provider'])
    attempt = config.directory / 'wakes' / str(wake)
    assert config.busy.read_text() == str(wake)
    result = json.loads((attempt / 'result.json').read_text())
    assert result['status'] == 'incomplete'
    assert result['settled'] is False
    assert result['cancel_path'] == str(config.cancel)
    assert 'incomplete' in (attempt / 'failure.txt').read_text()
    with pytest.raises(RuntimeError, match='reconciliation'):
        resident.run_wake(config, header(config, wake), ['must-not-retry'])


def test_stale_busy_refuses_even_clear_input(config):
    config.busy.write_text('old-wake')
    with pytest.raises(RuntimeError, match='reconciliation'):
        resident.serve(config, io.StringIO('/clear\n'))
    assert config.busy.read_text() == 'old-wake'
    assert not config.directory.exists()


def _launch(config, *, terminal=None):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(resident.__file__).resolve().parents[1])
    return subprocess.Popen([sys.executable, '-m', 'mishe_tauftauf.native_resident',
        '--home', str(config.home), '--role', config.role, '--model', config.model,
        '--bun', str(config.bun), '--node-modules', str(config.node_modules)],
        stdin=subprocess.PIPE if terminal is None else terminal,
        stdout=subprocess.PIPE if terminal is None else terminal,
        stderr=subprocess.STDOUT, env=env)


def _wait_ready(process, config):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if resident.is_ready(config.home, config.role):
            return
        if process.poll() is not None:
            pytest.fail(process.stdout.read().decode())
        time.sleep(.01)
    pytest.fail('resident never became ready')


def _read_until(process, needle):
    value = b''
    end = time.monotonic() + 5
    while needle not in value and time.monotonic() < end:
        if select.select([process.stdout], [], [], .05)[0]:
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            value += chunk
    assert needle in value, value.decode(errors='replace')
    return value


def test_real_cli_idle_clear_unknown_input_and_cleanup(config):
    process = _launch(config)
    try:
        _wait_ready(process, config)
        marker = json.loads(config.ready.read_text())
        assert marker['pid'] == process.pid
        assert marker['prepared'] is True
        process.stdin.write(b'/clear\n')
        process.stdin.flush()
        _read_until(process, b'Context cleared; previous story retained')
        _wait_ready(process, config)
        assert resident.is_ready(config.home, config.role)
        process.stdin.write(b' /clear \n')
        process.stdin.flush()
        process.wait(timeout=5)
        assert process.returncode == 1
        assert not resident.is_ready(config.home, config.role)
        assert not config.ready.exists()
        assert [path.read_text() for path in (config.directory / 'inputs').glob('*.txt')] == [
            ' /clear \n']
        assert 'Context cleared; previous story retained' in (config.directory / 'story.log').read_text()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_malformed_readiness_marker_is_unavailable_not_an_exception(config):
    config.directory.mkdir(parents=True)
    config.ready.write_text('[]\n')
    assert resident.is_ready(config.home, config.role) is False


def test_live_readiness_rejects_busy_wrong_identity_and_dead_process(config):
    process = _launch(config)
    try:
        _wait_ready(process, config)
        original = config.ready.read_text()
        marker = json.loads(original)
        config.busy.write_text('unresolved')
        assert not resident.is_ready(config.home, config.role)
        config.busy.unlink()
        marker['start'] = 'wrong-start-identity'
        config.ready.write_text(json.dumps(marker) + '\n')
        assert not resident.is_ready(config.home, config.role)
        config.ready.write_text(original)
        config.cancel.touch()
        assert not resident.is_ready(config.home, config.role)
        config.cancel.unlink()
        process.kill()
        process.wait(timeout=5)
        assert config.ready.exists()  # SIGKILL cannot perform cleanup.
        assert not resident.is_ready(config.home, config.role)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_real_tty_renegotiates_bracketed_paste_before_second_ready(config):
    master, slave = pty.openpty()
    process = _launch(config, terminal=slave)
    data = b''
    ready = f'{config.role} ready | internal loop |'.encode()

    def read_ready(count):
        nonlocal data
        end = time.monotonic() + 5
        while data.count(ready) < count and time.monotonic() < end:
            if select.select([master], [], [], .05)[0]:
                data += os.read(master, 65536)
        assert data.count(ready) >= count, data.decode(errors='replace')

    try:
        read_ready(1)
        assert resident.is_ready(config.home, config.role)
        os.write(master, b'\x15/clear\n')
        read_ready(2)
        second_ready = data.index(ready, data.index(ready) + len(ready))
        assert (data.rfind(b'\x1b[?2004h', 0, second_ready) >
                data.rfind(b'\x1b[?2004l', 0, second_ready))
        assert resident.is_ready(config.home, config.role)
        os.write(master, b'\x15\x1b[200~unexpected\nmultiline\x1b[201~\n')
        process.wait(timeout=5)
        assert process.returncode == 1
        assert not config.ready.exists()
        assert [path.read_text() for path in (config.directory / 'inputs').glob('*.txt')] == [
            'unexpected\nmultiline\n']
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        os.close(master)
        os.close(slave)
