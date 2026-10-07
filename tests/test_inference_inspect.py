import hashlib
from contextlib import contextmanager

import pytest

from mishe_tauftauf.inference_inspect import (
    prepare_exact_inspection, run_exact_inspection,
)
from mishe_tauftauf.inference_transport import NativeTurn


def prepare(tmp_path, **changes):
    source = tmp_path / 'evidence'
    source.write_bytes(b'owned evidence\n')
    options = dict(source_path=str(source),
                   source_sha=hashlib.sha256(source.read_bytes()).hexdigest(),
                   source_bytes=source.stat().st_size, source_id='actual-obligation',
                   task='Read the exact owned evidence', selector='synthetic',
                   writer_id='actual-caller', cancelled=lambda: False)
    options.update(changes)
    manifest, executor = prepare_exact_inspection(tmp_path / 'run', **options)
    call = {'type': 'toolCall', 'id': 'original-provider-id', 'name': 'inspect',
            'arguments': {'path': str(source)}}
    return source, manifest, executor, call


@pytest.mark.parametrize('replacement', ['changed', 'symlink', 'directory'])
def test_evidence_replacement_after_preflight_discloses_nothing(tmp_path, replacement):
    source, _, executor, call = prepare(tmp_path)
    if replacement == 'changed':
        source.write_bytes(b'other evidence\n')
    else:
        source.unlink()
        if replacement == 'directory':
            source.mkdir()
        else:
            other = tmp_path / 'other'
            other.write_bytes(b'owned evidence\n')
            source.symlink_to(other)
    result = executor(call)
    assert result['status'] == 'unknown'
    assert result['result'] is None
    # Restoring valid bytes cannot replay an already-started uncertain read.
    if replacement == 'directory':
        source.rmdir()
    elif replacement == 'symlink':
        source.unlink()
    source.write_bytes(b'owned evidence\n')
    assert executor(call)['status'] == 'unknown'


@pytest.mark.parametrize('violation', ['ungranted-path', 'extra-field'])
def test_out_of_schema_selection_never_starts(tmp_path, violation):
    _, _, executor, call = prepare(tmp_path)
    if violation == 'extra-field':
        call['arguments']['sha256'] = 'model-owned'
    else:
        call['arguments']['path'] = '/ungranted'
    assert executor(call)['status'] == 'not-started'
    assert executor.boundary.recover(call) is None


def test_cancelled_proposal_and_exclusive_journal_prevent_later_replay(tmp_path):
    state = {'cancelled': False}
    _, manifest, executor, call = prepare(tmp_path, cancelled=lambda: state['cancelled'])

    class Session:
        calls = 0

        def turn(self, context):
            self.calls += 1
            state['cancelled'] = True
            return NativeTurn('done', {'role': 'assistant', 'content': [call],
                                      'stopReason': 'toolUse', 'timestamp': 1})

    sessions = []

    @contextmanager
    def open_session():
        session = Session()
        sessions.append(session)
        yield session

    result = run_exact_inspection(open_session, tmp_path / 'run', manifest, executor,
                                  complete=lambda context: False)
    assert result['status'] == 'cancelled'
    assert result['calls'] == 0
    assert executor.boundary.recover(call) is None
    journal = (tmp_path / 'run/journal.jsonl').read_bytes()
    state['cancelled'] = False
    with pytest.raises(FileExistsError):
        run_exact_inspection(open_session, tmp_path / 'run', manifest, executor,
                             complete=lambda context: False)
    assert len(sessions) == 1
    assert sessions[0].calls == 1
    assert (tmp_path / 'run/journal.jsonl').read_bytes() == journal


def test_expected_byte_count_is_caller_authority(tmp_path):
    with pytest.raises(ValueError, match='byte count mismatch'):
        prepare(tmp_path, source_bytes=len(b'owned evidence\n') + 1)
    # Failed preparation is diagnosis evidence, not an automatically reusable run.
    assert (tmp_path / 'run').is_dir()
    assert not (tmp_path / 'run/manifest.json').exists()


@pytest.mark.parametrize('failure', ['construction', 'turn'])
def test_failed_session_keeps_reservation_and_cannot_restart(tmp_path, failure):
    _, manifest, executor, _ = prepare(tmp_path)
    attempts = []
    closed = []

    class Session:
        def turn(self, context):
            raise RuntimeError('turn failed')

    @contextmanager
    def open_session():
        attempts.append('construction')
        if failure == 'construction':
            raise RuntimeError('construction failed')
        try:
            yield Session()
        finally:
            closed.append(True)

    with pytest.raises(RuntimeError, match=failure + ' failed'):
        run_exact_inspection(open_session, tmp_path / 'run', manifest, executor,
                             complete=lambda context: False)
    journal = (tmp_path / 'run/journal.jsonl').read_bytes()
    if failure == 'construction':
        assert journal == b''
        assert closed == []
    else:
        assert closed == [True]
    with pytest.raises(FileExistsError):
        run_exact_inspection(open_session, tmp_path / 'run', manifest, executor,
                             complete=lambda context: False)
    assert attempts == ['construction']
    assert (tmp_path / 'run/journal.jsonl').read_bytes() == journal
