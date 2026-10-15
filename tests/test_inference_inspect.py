import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from mishe_tauftauf.inference_effects import EffectBoundary
from mishe_tauftauf.inference_inspect import (
    prepare_exact_inspection, reconcile_caller_journal, run_exact_inspection,
)
from mishe_tauftauf.inference_loop import read_native_journal
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


def test_lost_result_is_reconciled_without_redispatch(tmp_path, monkeypatch):
    import mishe_tauftauf.inference_inspect as inspection

    _, manifest, executor, call = prepare(tmp_path)
    executions = []
    execute = executor.boundary.execute

    def counted(ledger_call):
        executions.append(ledger_call)
        return execute(ledger_call)

    executor.boundary.execute = counted

    class Session:
        def turn(self, context):
            return NativeTurn('done', {'role': 'assistant', 'content': [call],
                                      'stopReason': 'toolUse', 'timestamp': 7})

    @contextmanager
    def open_session():
        yield Session()

    drive = inspection.drive_native

    def interrupt_before_result(session, context, **options):
        record = options['record']

        def interrupted(event):
            if event['kind'] == 'tool_result':
                raise InterruptedError('process lost before receipt')
            record(event)

        options['record'] = interrupted
        return drive(session, context, **options)

    monkeypatch.setattr(inspection, 'drive_native', interrupt_before_result)
    with pytest.raises(InterruptedError, match='process lost'):
        run_exact_inspection(open_session, tmp_path / 'run', manifest, executor,
                             complete=lambda context: False)

    journal = tmp_path / 'run/journal.jsonl'
    recovery = read_native_journal(journal)
    assert recovery['status'] == 'ready'
    assert recovery['context']['messages'][-1]['toolCallId'] == call['id']
    assert len(executions) == 1
    assert reconcile_caller_journal(journal, executor.boundary,
                                   authorized=lambda pending: True) is None
    assert len(executions) == 1


def test_completed_effect_is_not_recovered_after_authority_revocation(
        tmp_path, monkeypatch):
    import mishe_tauftauf.inference_inspect as inspection

    state = {'cancelled': False}
    _, manifest, executor, call = prepare(
        tmp_path, cancelled=lambda: state['cancelled'])
    execute = executor.boundary.execute

    def revoke_after_effect(ledger_call):
        result = execute(ledger_call)
        state['cancelled'] = True
        return result

    executor.boundary.execute = revoke_after_effect

    class Session:
        def turn(self, context):
            return NativeTurn('done', {'role': 'assistant', 'content': [call],
                                      'stopReason': 'toolUse', 'timestamp': 7})

    @contextmanager
    def open_session():
        yield Session()

    drive = inspection.drive_native

    def interrupt_before_result(session, context, **options):
        record = options['record']

        def interrupted(event):
            if event['kind'] == 'tool_result':
                raise InterruptedError()
            record(event)

        options['record'] = interrupted
        return drive(session, context, **options)

    monkeypatch.setattr(inspection, 'drive_native', interrupt_before_result)
    with pytest.raises(InterruptedError):
        run_exact_inspection(open_session, tmp_path / 'run', manifest, executor,
                             complete=lambda context: False)

    recovery = read_native_journal(tmp_path / 'run/journal.jsonl')
    assert recovery['status'] == 'unknown'
    assert recovery['pending_calls'] == [call]
    assert executor.boundary.recover(call)['status'] == 'completed'


def test_revocation_after_durable_start_prevents_inspection(tmp_path):
    state = {'cancelled': False}
    _, _, executor, call = prepare(
        tmp_path, cancelled=lambda: state['cancelled'])
    executor.read = lambda: pytest.fail('revoked inspection reached file read')
    execute = executor.boundary.execute

    def revoke_before_effect(ledger_call):
        state['cancelled'] = True
        return execute(ledger_call)

    executor.boundary.execute = revoke_before_effect
    result = executor(call)

    assert result['status'] == 'unknown'
    assert result['failure'] == 'capability-failed'
    assert executor.boundary.recover(call)['status'] == 'unknown'
    assert executor.boundary.recover(call)['failure'] == 'started-without-outcome'


def test_refused_existing_run_does_not_reconcile_its_journal(tmp_path, monkeypatch):
    import mishe_tauftauf.inference_inspect as inspection

    _, manifest, executor, call = prepare(tmp_path)
    sessions = []

    class Session:
        def turn(self, context):
            return NativeTurn('done', {'role': 'assistant', 'content': [call],
                                      'stopReason': 'toolUse', 'timestamp': 1})

    @contextmanager
    def open_session():
        sessions.append(True)
        yield Session()

    drive = inspection.drive_native

    def lose_receipt(session, context, **options):
        record = options['record']

        def interrupted(event):
            if event['kind'] == 'tool_result':
                raise InterruptedError()
            record(event)

        options['record'] = interrupted
        return drive(session, context, **options)

    monkeypatch.setattr(inspection, 'drive_native', lose_receipt)
    monkeypatch.setattr(inspection, 'reconcile_caller_journal', lambda *a, **k: None)
    with pytest.raises(InterruptedError):
        run_exact_inspection(open_session, tmp_path / 'run', manifest, executor,
                             complete=lambda context: False)
    journal = tmp_path / 'run/journal.jsonl'
    preserved = journal.read_bytes()
    assert read_native_journal(journal)['proposed_call'] == call

    monkeypatch.setattr(
        inspection, 'reconcile_caller_journal',
        reconcile_caller_journal)
    with pytest.raises(FileExistsError):
        run_exact_inspection(open_session, tmp_path / 'run', manifest, executor,
                             complete=lambda context: False)
    assert journal.read_bytes() == preserved
    assert sessions == [True]


def test_reconcile_appends_no_success_claim_when_the_receipt_evidence_drifted(
        tmp_path, receipt_recovery_case):
    """A completion recorded from evidence that no longer re-reads is refused.

    The store still holds the receipt; the bytes it named were replaced, so the
    successor writes no tool_result and the call stays pending with the verdict
    preserved (Experiment 4f).
    """
    from mishe_tauftauf.inference_effects import EffectBoundary

    case = receipt_recovery_case(tmp_path)
    case['artifact'].write_bytes(b'replaced bytes\n')
    path = tmp_path / 'drift.jsonl'
    path.write_bytes(case['journal'])

    boundary = EffectBoundary(case['store'], writer_id='loop-3',
                              obligation={'source': 'owned-event'},
                              execute=lambda call: pytest.fail('executed again'),
                              authority='test:authority')
    result = reconcile_caller_journal(path, boundary,
                                      authorized=lambda call: True)

    assert result['status'] == 'unknown'
    assert result['evidence_drift']['call'] == case['call']['id']
    assert result['evidence_drift']['failure'] == 'effect-evidence-mismatch'
    assert result['outcome']['status'] == 'completed'
    assert path.read_bytes() == case['journal']
    assert read_native_journal(path)['pending_calls'] == [case['call']]


def test_reconcile_appends_the_row_for_a_verified_receipt(
        tmp_path, receipt_recovery_case):
    """The same path restores the claim while the evidence still re-reads."""
    from mishe_tauftauf.inference_effects import EffectBoundary

    case = receipt_recovery_case(tmp_path)
    path = tmp_path / 'honest.jsonl'
    path.write_bytes(case['journal'])

    boundary = EffectBoundary(case['store'], writer_id='loop-3',
                              obligation={'source': 'owned-event'},
                              execute=lambda call: pytest.fail('executed again'),
                              authority='test:authority')
    result = reconcile_caller_journal(path, boundary,
                                      authorized=lambda call: True)

    assert result['status'] == 'completed'
    assert read_native_journal(path)['pending_calls'] == []


def _prepared_inspection(tmp_path, name, source, *, max_turns=2, max_calls=2):
    return prepare_exact_inspection(
        tmp_path / name,
        source_path=str(source),
        source_sha=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_bytes=source.stat().st_size, source_id='actual-obligation',
        task='Read the exact owned evidence', selector='synthetic',
        writer_id='actual-caller', cancelled=lambda: False,
        max_turns=max_turns, max_calls=max_calls)


def _inspect_call(call_id, source):
    return {'type': 'toolCall', 'id': call_id, 'name': 'inspect',
            'arguments': {'path': str(source)}}


def _row_for(journal_path, call_id):
    rows = [json.loads(line) for line in
            journal_path.read_text().splitlines()]
    return next(row['message'] for row in rows if row['kind'] == 'tool_result'
                and row['message']['toolCallId'] == call_id)


def _event_for(journal_path, call_id):
    rows = [json.loads(line) for line in
            journal_path.read_text().splitlines()]
    return next(row for row in rows if row['kind'] == 'tool_result'
                and row['message']['toolCallId'] == call_id)


def _comparable(message):
    outcome = json.loads(message['content'][0]['text'])
    return {key: value for key, value in message.items() if key != 'content'}, \
        {key: value for key, value in outcome.items()
         if key not in ('operation_id', 'reconciliation_ref')}


def test_reconciled_multi_call_row_is_the_uninterrupted_writers_row(
        tmp_path, monkeypatch):
    """Experiment 4j (H1): under a multi-call inspection turn the row the
    caller path restores is byte-for-byte the row `drive_native` appends for
    the same call — including the proposing assistant's timestamp. Before the
    fix the positional `messages[-1]` read made this correct only by
    coincidence (the sibling result row carried the same timestamp).
    """
    import mishe_tauftauf.inference_inspect as inspection
    from mishe_tauftauf.inference_loop import drive_native

    source = tmp_path / 'evidence'
    source.write_bytes(b'owned evidence\n')
    manifest, executor = _prepared_inspection(tmp_path, 'crashed', source)
    call_a = _inspect_call('first-provider-id', source)
    call_b = _inspect_call('second-provider-id', source)

    class Session:
        def turn(self, context):
            return NativeTurn('done', {'role': 'assistant',
                                       'content': [call_a, call_b],
                                       'stopReason': 'toolUse',
                                       'timestamp': 7}, 'toolUse')

    @contextmanager
    def open_session():
        yield Session()

    def interrupt_before_second(session, context, **options):
        record = options['record']

        def interrupted(event):
            if (event['kind'] == 'tool_result'
                    and event['message']['toolCallId'] == call_b['id']):
                raise InterruptedError('process lost before receipt')
            record(event)

        options['record'] = interrupted
        return drive_native(session, context, **options)

    monkeypatch.setattr(inspection, 'drive_native', interrupt_before_second)
    with pytest.raises(InterruptedError, match='process lost'):
        run_exact_inspection(open_session, tmp_path / 'crashed', manifest,
                             executor, complete=lambda context: False)

    journal = tmp_path / 'crashed/journal.jsonl'
    row = _row_for(journal, call_b['id'])
    assert row['timestamp'] == 7
    state = read_native_journal(journal)
    assert state['pending_calls'] == []
    assert state['context']['messages'][-1] == row

    monkeypatch.undo()
    reference_manifest, reference_executor = _prepared_inspection(
        tmp_path, 'uninterrupted', source)
    reference = tmp_path / 'uninterrupted/journal.jsonl'

    class Full:
        turns = [NativeTurn('done', {'role': 'assistant',
                                     'content': [call_a, call_b],
                                     'stopReason': 'toolUse',
                                     'timestamp': 7}, 'toolUse'),
                 NativeTurn('done', {'role': 'assistant', 'content': [],
                                     'stopReason': 'stop'}, 'stop')]

        def turn(self, context):
            return self.turns.pop(0)

    @contextmanager
    def full_session():
        yield Full()

    run_exact_inspection(full_session, tmp_path / 'uninterrupted',
                         reference_manifest, reference_executor,
                         complete=lambda context: False)
    expected = _row_for(reference, call_b['id'])
    assert _comparable(row) == _comparable(expected)


def test_reconciled_row_after_a_legacy_sibling_row_is_the_proposing_timestamp(
        tmp_path):
    """Experiment 4j (H2): the divergence the positional read still carried.

    A sibling `toolResult` row stamped 0 — the pre-4h loop reconciler's writer
    convention, measured in Experiment 4h's before-fix fixture — used to be
    copied onto the restored row. The caller path now restores the pending
    call's proposing assistant's timestamp, equal to the uninterrupted writer's
    row for the same call.
    """
    from mishe_tauftauf.inference_loop import NativeJournal, drive_native

    source = tmp_path / 'evidence'
    source.write_bytes(b'owned evidence\n')
    manifest, executor = _prepared_inspection(tmp_path, 'crashed', source)
    call_a = _inspect_call('first-provider-id', source)
    call_b = _inspect_call('second-provider-id', source)
    journal_path = tmp_path / 'crashed/journal.jsonl'
    with NativeJournal(journal_path) as journal:
        def record(event):
            if event['kind'] != 'tool_result':
                journal(event)
                return
            if event['message']['toolCallId'] == call_b['id']:
                raise InterruptedError('process lost before receipt')
            if event['message']['toolCallId'] == call_a['id']:
                row = json.loads(json.dumps(event))
                row['message']['timestamp'] = 0  # pre-4h writer convention
                journal(row)
                return
            journal(event)

        class Session:
            def turn(self, context):
                return NativeTurn('done', {'role': 'assistant',
                                           'content': [call_a, call_b],
                                           'stopReason': 'toolUse',
                                           'timestamp': 7}, 'toolUse')

        with pytest.raises(InterruptedError, match='process lost'):
            drive_native(Session(), manifest['context'],
                         obligation=manifest['obligation'], max_turns=2,
                         max_calls=2, dispatch=executor, record=record,
                         phase=lambda call: None, cancelled=lambda: False,
                         authorized=lambda call: True,
                         complete=lambda context: False)

    # The positional source is the sibling row stamped 0.
    assert read_native_journal(journal_path)['context']['messages'][-1] \
        ['timestamp'] == 0
    result = reconcile_caller_journal(journal_path, executor.boundary,
                                      authorized=lambda call: True)
    assert result['status'] == 'completed'
    row = _row_for(journal_path, call_b['id'])
    assert row['timestamp'] == 7

    reference_manifest, reference_executor = _prepared_inspection(
        tmp_path, 'uninterrupted', source)
    reference = tmp_path / 'uninterrupted/journal.jsonl'

    class Full:
        turns = [NativeTurn('done', {'role': 'assistant',
                                     'content': [call_a, call_b],
                                     'stopReason': 'toolUse',
                                     'timestamp': 7}, 'toolUse'),
                 NativeTurn('done', {'role': 'assistant', 'content': [],
                                     'stopReason': 'stop'}, 'stop')]

        def turn(self, context):
            return self.turns.pop(0)

    @contextmanager
    def full_session():
        yield Full()

    run_exact_inspection(full_session, tmp_path / 'uninterrupted',
                         reference_manifest, reference_executor,
                         complete=lambda context: False)
    assert _comparable(row) == _comparable(
        _row_for(reference, call_b['id']))


def test_reconciled_row_for_an_untimestamped_assistant_is_unchanged(tmp_path):
    """Experiment 4j: no proposing timestamp restores 0, exactly as before."""
    from mishe_tauftauf.inference_loop import NativeJournal, drive_native

    source = tmp_path / 'evidence'
    source.write_bytes(b'owned evidence\n')
    manifest, executor = _prepared_inspection(tmp_path, 'crashed', source)
    call_a = _inspect_call('only-provider-id', source)
    journal_path = tmp_path / 'crashed/journal.jsonl'
    with NativeJournal(journal_path) as journal:
        def record(event):
            if event['kind'] == 'tool_result':
                raise InterruptedError('process lost before receipt')
            journal(event)

        class Session:
            def turn(self, context):
                return NativeTurn('done', {'role': 'assistant',
                                           'content': [call_a],
                                           'stopReason': 'toolUse'},
                                   'toolUse')

        with pytest.raises(InterruptedError, match='process lost'):
            drive_native(Session(), manifest['context'],
                         obligation=manifest['obligation'], max_turns=2,
                         max_calls=2, dispatch=executor, record=record,
                         phase=lambda call: None, cancelled=lambda: False,
                         authorized=lambda call: True,
                         complete=lambda context: False)

    reconcile_caller_journal(journal_path, executor.boundary,
                             authorized=lambda call: True)
    assert _row_for(journal_path, call_a['id'])['timestamp'] == 0


def _multi_call_crashed(tmp_path, monkeypatch, *, interrupt):
    """Run the real caller path into a multi-call interruption and return the
    journal plus the untouched reconcile arguments.

    `interrupt('before_row'|'after_row', call_id)` selects the kill point:
    inside the record callback before the named result row is durable, or once
    it is durable and before any later row.
    """
    import mishe_tauftauf.inference_inspect as inspection
    from mishe_tauftauf.inference_loop import drive_native

    source = tmp_path / 'evidence'
    source.write_bytes(b'owned evidence\n')
    manifest, executor = _prepared_inspection(tmp_path, 'crashed', source)
    call_a = _inspect_call('first-provider-id', source)
    call_b = _inspect_call('second-provider-id', source)

    class Session:
        def turn(self, context):
            return NativeTurn('done', {'role': 'assistant',
                                       'content': [call_a, call_b],
                                       'stopReason': 'toolUse',
                                       'timestamp': 7}, 'toolUse')

    @contextmanager
    def open_session():
        yield Session()

    def interrupting_drive(session, context, **options):
        record = options['record']

        def interrupted(event):
            if event['kind'] == 'tool_result':
                call_id = event['message']['toolCallId']
                if interrupt[0] == 'before_row' and call_id == interrupt[1]:
                    raise InterruptedError('process lost before receipt')
                record(event)
                if interrupt[0] == 'after_row' and call_id == interrupt[1]:
                    raise InterruptedError('process lost after receipt')
                return
            record(event)

        options['record'] = interrupted
        return drive_native(session, context, **options)

    monkeypatch.setattr(inspection, 'drive_native', interrupting_drive)
    with pytest.raises(InterruptedError, match='process lost'):
        run_exact_inspection(open_session, tmp_path / 'crashed', manifest,
                             executor, complete=lambda context: False)
    monkeypatch.undo()

    def authorized(call):
        return True

    return tmp_path / 'crashed/journal.jsonl', executor, authorized, call_b


def test_multi_call_kill_before_result_restores_one_receipt_per_invocation(
        tmp_path, monkeypatch):
    """Experiment 4k (H1/H2): under a multi-call turn the write-ahead writer
    leaves at most one lost receipt per interruption. The caller's `finally`
    reconcile restores it; a second invocation appends nothing and the store
    holds nothing left to restore.
    """
    journal, executor, authorized, _ = _multi_call_crashed(
        tmp_path, monkeypatch, interrupt=('before_row', 'second-provider-id'))
    rows = lambda: [json.loads(line) for line in
                    journal.read_text().splitlines()]
    before = len([row for row in rows() if row['kind'] == 'tool_result'])
    state = read_native_journal(journal)
    assert state['pending_calls'] == []
    assert len([row for row in rows() if row['kind'] == 'tool_result']) == before

    # run_exact_inspection's finally already reconciled once: the restored row
    # is present and the boundary is ready again.
    assert read_native_journal(journal)['status'] == 'ready'
    second = reconcile_caller_journal(journal, executor.boundary,
                                      authorized=authorized)
    assert second is None
    assert len([row for row in rows() if row['kind'] == 'tool_result']) == before


def test_multi_call_kill_before_first_result_restores_only_that_call(
        tmp_path, monkeypatch):
    """Experiment 4k (H1): the other lost-receipt position — the first call's
    dispatch — restores exactly that call, leaves the sibling pending with no
    durable outcome, and a second invocation appends nothing.
    """
    journal, executor, authorized, call_b = _multi_call_crashed(
        tmp_path, monkeypatch, interrupt=('before_row', 'first-provider-id'))
    state = read_native_journal(journal)
    assert [call['id'] for call in state['pending_calls']] == [call_b['id']]
    assert _row_for(journal, 'first-provider-id')['timestamp'] == 7
    # The sibling was never dispatched: no receipt exists to restore, so the
    # reconciler must not synthesize one.
    assert executor.boundary.recover(call_b) is None
    second = reconcile_caller_journal(journal, executor.boundary,
                                      authorized=authorized)
    assert second is None
    assert [call['id'] for call in
            read_native_journal(journal)['pending_calls']] == [call_b['id']]


def test_kill_between_a_result_and_the_next_proposal_restores_nothing(
        tmp_path, monkeypatch):
    """Experiment 4k (H2): a kill in the window between a durable result row
    and the next call's proposal loses no receipt. The reconciler appends
    nothing on every invocation, the journal stays readable, and the stranded
    sibling stays visibly pending for the successor.
    """
    journal, executor, authorized, call_b = _multi_call_crashed(
        tmp_path, monkeypatch, interrupt=('after_row', 'first-provider-id'))
    rows_before = journal.read_bytes()
    assert reconcile_caller_journal(journal, executor.boundary,
                                    authorized=authorized) is None
    assert journal.read_bytes() == rows_before
    state = read_native_journal(journal)
    assert state['status'] == 'unknown'
    assert state['proposed_call'] is None
    assert [call['id'] for call in state['pending_calls']] == [call_b['id']]
    assert executor.boundary.recover(call_b) is None


def test_receipt_for_a_call_without_a_proposal_row_is_never_restored(
        tmp_path, monkeypatch):
    """Experiment 4k (H4, unreachable shape): a completed effect for a call
    with no proposal row exists only under a writer that dispatches a sibling
    before recording the earlier call's result — no current writer does. The
    reconciler's one-row-per-invocation shape bounds that limit to the safe
    direction: nothing is appended, the journal stays valid, the receipt is
    left for the successor to see.
    """
    import mishe_tauftauf.inference_inspect as inspection
    from mishe_tauftauf.inference_loop import drive_native

    source = tmp_path / 'evidence'
    source.write_bytes(b'owned evidence\n')
    manifest, executor = _prepared_inspection(tmp_path, 'crashed', source)
    call_a = _inspect_call('first-provider-id', source)
    call_b = _inspect_call('second-provider-id', source)
    seeded = _inspect_call('seeded-provider-id', source)

    class Session:
        def turn(self, context):
            return NativeTurn('done', {'role': 'assistant',
                                       'content': [call_a, call_b],
                                       'stopReason': 'toolUse',
                                       'timestamp': 7}, 'toolUse')

    @contextmanager
    def open_session():
        yield Session()

    def seeding_drive(session, context, **options):
        record = options['record']

        def interrupted(event):
            if (event['kind'] == 'tool_result'
                    and event['message']['toolCallId'] == call_a['id']):
                record(event)
                # A writer that dispatches before the next proposal row: no
                # current writer produces this shape.
                executor.boundary(seeded)
                raise InterruptedError('process lost after receipt')
            record(event)

        options['record'] = interrupted
        return drive_native(session, context, **options)

    monkeypatch.setattr(inspection, 'drive_native', seeding_drive)
    with pytest.raises(InterruptedError, match='process lost'):
        run_exact_inspection(open_session, tmp_path / 'crashed', manifest,
                             executor, complete=lambda context: False)
    monkeypatch.undo()

    journal = tmp_path / 'crashed/journal.jsonl'
    rows_before = journal.read_bytes()
    assert reconcile_caller_journal(journal, executor.boundary,
                                    authorized=lambda call: True) is None
    assert journal.read_bytes() == rows_before
    # The journal still reads, and the stranded receipt is visible in the store.
    assert read_native_journal(journal)['status'] == 'unknown'
    assert executor.boundary.recover(seeded)['status'] == 'completed'


def test_kill_after_the_last_result_row_restores_nothing(tmp_path, monkeypatch):
    """Experiment 4k (H1/H2): the ready-boundary interruption point. The last
    call's result row is already durable, so the caller's `finally` reconcile
    appends nothing, every later invocation stays silent, and the journal stays
    a ready boundary holding no pending call — nothing was lost, so nothing is
    restored and no pending call is left without its receipt.
    """
    journal, executor, authorized, _ = _multi_call_crashed(
        tmp_path, monkeypatch, interrupt=('after_row', 'second-provider-id'))
    preserved = journal.read_bytes()
    state = read_native_journal(journal)
    assert state['status'] == 'ready'
    assert state['proposed_call'] is None
    assert state['pending_calls'] == []
    assert state['calls'] == 2

    for _ in range(2):
        assert reconcile_caller_journal(journal, executor.boundary,
                                        authorized=authorized) is None
        assert journal.read_bytes() == preserved
        state = read_native_journal(journal)
        assert state['status'] == 'ready'
        assert state['pending_calls'] == []
        assert state['calls'] == 2


def _inspection_turns(source):
    """One scripted session: one call per turn, then a stop turn.

    Shared by the interrupted run and its uninterrupted reference so both arms
    select the same calls with the same timestamps in the same order.
    """
    return [
        NativeTurn('done', {'role': 'assistant',
                            'content': [_inspect_call('first-provider-id',
                                                      source)],
                            'stopReason': 'toolUse', 'timestamp': 7}, 'toolUse'),
        NativeTurn('done', {'role': 'assistant',
                            'content': [_inspect_call('second-provider-id',
                                                      source)],
                            'stopReason': 'toolUse', 'timestamp': 7}, 'toolUse'),
        NativeTurn('done', {'role': 'assistant', 'content': [],
                            'stopReason': 'stop'}, 'stop')]


class _Scripted:
    """A session replaying pre-written turns; no provider is involved."""

    def __init__(self, turns):
        self.turns = list(turns)

    def turn(self, context):
        return self.turns.pop(0)


def _two_turn_crashed(tmp_path, monkeypatch):
    """Lose the process before the second turn's call result row (Experiment
    4k's two-turn arm) and return the journal plus the reconcile arguments.
    """
    import mishe_tauftauf.inference_inspect as inspection
    from mishe_tauftauf.inference_loop import drive_native

    source = tmp_path / 'evidence'
    source.write_bytes(b'owned evidence\n')
    manifest, executor = _prepared_inspection(tmp_path, 'crashed', source,
                                              max_turns=3)
    call_b = _inspect_call('second-provider-id', source)

    @contextmanager
    def open_session():
        yield _Scripted(_inspection_turns(source))

    drive = inspection.drive_native

    def interrupting_drive(session, context, **options):
        record = options['record']

        def interrupted(event):
            if (event['kind'] == 'tool_result'
                    and event['message']['toolCallId'] == call_b['id']):
                raise InterruptedError('process lost before receipt')
            record(event)

        options['record'] = interrupted
        return drive(session, context, **options)

    monkeypatch.setattr(inspection, 'drive_native', interrupting_drive)
    with pytest.raises(InterruptedError, match='process lost'):
        run_exact_inspection(open_session, tmp_path / 'crashed', manifest,
                             executor, complete=lambda context: False)
    monkeypatch.undo()

    def authorized(call):
        return True

    return tmp_path / 'crashed/journal.jsonl', executor, authorized


def test_two_turn_kill_before_result_restores_that_call_as_the_writers_row(
        tmp_path, monkeypatch):
    """Experiment 4k (H1) across a turn boundary: the second turn's call is
    the one whose receipt is lost, the first reconcile restores exactly it as
    the row `drive_native` would have appended, a second invocation appends
    nothing, and the journal reaches a ready boundary holding no pending call.
    """
    journal, executor, authorized = _two_turn_crashed(tmp_path, monkeypatch)
    state = read_native_journal(journal)
    assert state['status'] == 'ready'
    assert state['pending_calls'] == []
    assert state['calls'] == 2
    assert state['turns'] == 2
    row = _row_for(journal, 'second-provider-id')
    assert _event_for(journal, 'second-provider-id')['turn'] == 1
    assert row['timestamp'] == 7

    preserved = journal.read_bytes()
    assert reconcile_caller_journal(journal, executor.boundary,
                                    authorized=authorized) is None
    assert journal.read_bytes() == preserved

    source = tmp_path / 'evidence'
    reference_manifest, reference_executor = _prepared_inspection(
        tmp_path, 'uninterrupted', source, max_turns=3)
    reference = tmp_path / 'uninterrupted/journal.jsonl'

    @contextmanager
    def full_session():
        yield _Scripted(_inspection_turns(source))

    outcome = run_exact_inspection(full_session, tmp_path / 'uninterrupted',
                                   reference_manifest, reference_executor,
                                   complete=lambda context: False)
    assert outcome['status'] == 'incomplete'
    assert outcome['calls'] == 2
    assert _comparable(row) == _comparable(
        _row_for(reference, 'second-provider-id'))


def test_resume_from_a_multi_call_journal_matches_the_reader_state(
        tmp_path, monkeypatch):
    """Experiment 4k: a successor driving a NEW journal from the interrupted
    one sees exactly the reconstructed reader state. A stranded pending sibling
    stops `unknown` with that sibling visibly pending (never lost, never
    redispatch — no proposal or result row in the successor's journal); a ready
    boundary continues to the scripted terminator with both calls accounted.
    """
    from mishe_tauftauf.inference_loop import NativeJournal, drive_native

    def resume(journal_path, executor, name):
        state = read_native_journal(journal_path)
        target = tmp_path / name / 'journal.jsonl'
        target.parent.mkdir(parents=True, exist_ok=True)
        with NativeJournal(target) as journal:
            outcome = drive_native(
                _Scripted([NativeTurn('done', {'role': 'assistant',
                                               'content': [],
                                               'stopReason': 'stop'}, 'stop')]),
                state['context'], obligation=state['obligation'],
                max_turns=state['budgets']['turns'],
                max_calls=state['budgets']['calls'], dispatch=executor,
                record=journal, phase=lambda call: None,
                cancelled=lambda: False, authorized=lambda call: True,
                complete=lambda context: False, resume_from=journal_path)
        return outcome, target

    arm = tmp_path / 'stranded'
    arm.mkdir()
    stranded, executor, _, _ = _multi_call_crashed(
        arm, monkeypatch, interrupt=('after_row', 'first-provider-id'))
    call_b = _inspect_call('second-provider-id', arm / 'evidence')

    arm = tmp_path / 'ready'
    arm.mkdir()
    ready, executor, _, _ = _multi_call_crashed(
        arm, monkeypatch, interrupt=('after_row', 'second-provider-id'))

    outcome, target = resume(stranded, executor, 'stranded-resume')
    assert outcome['status'] == 'unknown'
    assert [call['id'] for call in outcome['pending_calls']] == [call_b['id']]
    assert outcome['calls'] == 1
    rows = [json.loads(line) for line in
            target.read_text().splitlines()]
    assert [row['kind'] for row in rows] == ['checkpoint', 'stop']

    outcome, target = resume(ready, executor, 'ready-resume')
    assert outcome['status'] == 'incomplete'
    assert outcome['calls'] == 2
    assert outcome['turns'] == 2
    rows = [json.loads(line) for line in
            target.read_text().splitlines()]
    assert [row['kind'] for row in rows] == ['checkpoint', 'model_input',
                                             'model_output', 'stop']


# Experiment 4l (wake 50866): the caller-fed mutating capability, pinned
# zero-provider. The live arms are scratch evidence; these tests keep the
# exactly-once-mutation invariant permanently observable.

_MARKER_LINE = 'one marker line'


class MarkerRefused(RuntimeError):
    pass


class _AppendMarker:
    """The mutating twin of ExactInspect: one caller-frozen line append."""

    def __init__(self, path, store, *, obligation, writer_id, cancelled):
        self.path, self.line = path, _MARKER_LINE
        self.cancelled = cancelled
        self.boundary = EffectBoundary(
            store, writer_id=writer_id, obligation=obligation,
            authority=f'append-marker:{path}:1',
            capability_version='marker-append-test-v1', execute=self.execute)

    def validate(self, call):
        if self.cancelled():
            raise MarkerRefused('cancelled')
        if type(call) is not dict or call.get('type') != 'toolCall':
            raise MarkerRefused('call-shape')
        if type(call.get('id')) is not str or not call['id']:
            raise MarkerRefused('call-id')
        if call.get('name') != 'append_marker':
            raise MarkerRefused('capability')
        args = call.get('arguments')
        if type(args) is not dict or set(args) != {'line'}:
            raise MarkerRefused('arguments-schema')
        if type(args['line']) is not str or args['line'] != self.line:
            raise MarkerRefused('line-scope')

    def append(self):
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            data = (self.line + '\n').encode('utf-8')
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        return {'path': self.path, 'line': self.line, 'appended': 1,
                'bytes': os.stat(self.path).st_size,
                'sha256': hashlib.sha256(Path(self.path).read_bytes()).hexdigest()}

    def execute(self, ledger_call):
        self.validate(ledger_call['native_call'])
        return self.append()

    def __call__(self, call):
        try:
            self.validate(call)
        except MarkerRefused as exc:
            return {'status': 'not-started', 'result': {'refusal': str(exc)}}
        return self.boundary(call)


def _prepare_marker(tmp_path, name):
    marker = tmp_path / f'{name}-marker.txt'
    marker.write_bytes(b'')
    directory = tmp_path / name
    directory.mkdir()
    obligation = {'source_id': 'marker-obligation', 'path': str(marker),
                  'reason': 'Append exactly one owned marker line',
                  'omissions': ['other effects']}
    executor = _AppendMarker(str(marker), directory / 'effects',
                             obligation=obligation, writer_id='marker-caller',
                             cancelled=lambda: False)
    tool = {'name': 'append_marker',
            'description': 'Append exactly one caller-frozen marker line.',
            'parameters': {'type': 'object', 'properties': {
                'line': {'type': 'string', 'enum': [_MARKER_LINE]}},
                'required': ['line'], 'additionalProperties': False}}
    context = {'messages': [{'role': 'user', 'content': 'Append one marker '
                             'line, once.', 'timestamp': 1}], 'tools': [tool]}
    manifest = {'obligation': obligation, 'context': context,
                'selector': 'synthetic', 'writer_id': 'marker-caller',
                'capability_version': 'marker-append-test-v1',
                'max_turns': 2, 'max_calls': 1, 'limits': []}
    return marker, manifest, executor


def _marker_call(call_id):
    return {'type': 'toolCall', 'id': call_id, 'name': 'append_marker',
            'arguments': {'line': _MARKER_LINE}}


def _marker_turns():
    return [
        NativeTurn('done', {'role': 'assistant',
                            'content': [_marker_call('marker-provider-id')],
                            'stopReason': 'toolUse', 'timestamp': 7}, 'toolUse'),
        NativeTurn('done', {'role': 'assistant', 'content': [],
                            'stopReason': 'stop'}, 'stop')]


def _marker_comparable(message):
    outcome = json.loads(message['content'][0]['text'])
    return ({key: value for key, value in message.items() if key != 'content'},
            {key: value for key, value in outcome.items()
             if key not in ('operation_id', 'reconciliation_ref', 'result')},
            {key: value for key, value in outcome['result'].items()
             if key not in ('mtime_ns', 'sha256', 'path')})


def test_out_of_schema_marker_selection_never_starts(tmp_path):
    """Experiment 4l: the executor's typed parameter checks refuse a foreign
    capability, an altered line and extra arguments before any effect."""
    marker, _, executor = _prepare_marker(tmp_path, 'refused')
    for change in ('capability', 'line', 'extra'):
        call = _marker_call('refused-id')
        if change == 'capability':
            call['name'] = 'inspect'
        elif change == 'line':
            call['arguments']['line'] = 'model-owned line'
        else:
            call['arguments']['count'] = 2
        assert executor(call)['status'] == 'not-started'
        assert executor.boundary.recover(call) is None
    assert marker.read_bytes() == b''


def test_mutating_caller_path_appends_exactly_one_marker_per_call(tmp_path):
    """Experiment 4l (H1 baseline): the uninterrupted scripted caller path
    executes the mutation exactly once, records one execution, and stops
    complete with the frozen line appended.
    """
    marker, manifest, executor = _prepare_marker(tmp_path, 'uninterrupted')

    class Session:
        def __init__(self):
            self.turns = _marker_turns()

        def turn(self, context):
            return self.turns.pop(0)

    @contextmanager
    def open_session():
        yield Session()

    result = run_exact_inspection(open_session, tmp_path / 'uninterrupted',
                                  manifest, executor,
                                  complete=lambda context: True)
    assert result['status'] == 'complete'
    assert result['calls'] == 1
    assert marker.read_bytes() == (_MARKER_LINE + '\n').encode('utf-8')
    outcomes = [json.loads(line) for line in
                (tmp_path / 'uninterrupted/effects/outcomes.jsonl')
                .read_text().splitlines()]
    assert [row['status'] for row in outcomes] == ['completed']
    assert [row['executions'] for row in outcomes] == [1]


def test_kill_after_the_mutating_outcome_restores_without_a_second_append(
        tmp_path):
    """Experiment 4l (H1/H2/H3): losing the caller before its result row
    restores the receipt once — structurally the row the uninterrupted writer
    appends — executes the mutation exactly once, and leaves the marker at one
    line with every later invocation silent.
    """
    marker, manifest, executor = _prepare_marker(tmp_path, 'crashed')

    class Session:
        def __init__(self):
            self.turns = _marker_turns()

        def turn(self, context):
            return self.turns.pop(0)

    @contextmanager
    def open_session():
        yield Session()

    import mishe_tauftauf.inference_inspect as inspection
    from mishe_tauftauf.inference_loop import drive_native

    original = inspection.drive_native

    def interrupting_drive(session, context, **options):
        record = options['record']

        def interrupted(event):
            if event['kind'] == 'tool_result':
                raise InterruptedError('process lost before receipt')
            record(event)

        options['record'] = interrupted
        return original(session, context, **options)

    inspection.drive_native = interrupting_drive
    try:
        with pytest.raises(InterruptedError, match='process lost'):
            run_exact_inspection(open_session, tmp_path / 'crashed', manifest,
                                 executor, complete=lambda context: True)
    finally:
        inspection.drive_native = original

    journal = tmp_path / 'crashed/journal.jsonl'
    rows = lambda: [json.loads(line) for line in journal.read_text().splitlines()]
    assert marker.read_bytes() == (_MARKER_LINE + '\n').encode('utf-8')
    outcomes = [json.loads(line) for line in
                (tmp_path / 'crashed/effects/outcomes.jsonl')
                .read_text().splitlines()]
    assert [row['executions'] for row in outcomes] == [1]
    restored = _row_for(journal, 'marker-provider-id')
    assert restored['isError'] is False
    assert restored['timestamp'] == 7
    assert list(_event_for(journal, 'marker-provider-id')) == [
        'kind', 'turn', 'call', 'message', 'obligation']
    # run_exact_inspection's finally already restored once; a second
    # invocation appends nothing and re-executes nothing.
    assert len([row for row in rows() if row['kind'] == 'proposal']) == 1
    assert len([row for row in rows() if row['kind'] == 'tool_result']) == 1
    assert reconcile_caller_journal(journal, executor.boundary,
                                    authorized=lambda call: True) is None
    assert len([row for row in rows() if row['kind'] == 'tool_result']) == 1
    assert marker.read_bytes() == (_MARKER_LINE + '\n').encode('utf-8')

    reference_marker, reference_manifest, reference_executor = _prepare_marker(
        tmp_path, 'reference')

    @contextmanager
    def open_reference():
        yield Session()

    run_exact_inspection(open_reference, tmp_path / 'reference',
                         reference_manifest, reference_executor,
                         complete=lambda context: True)
    reference = _row_for(tmp_path / 'reference/journal.jsonl',
                         'marker-provider-id')
    assert _marker_comparable(restored) == _marker_comparable(reference)
    assert list(_event_for(tmp_path / 'reference/journal.jsonl',
                           'marker-provider-id')) == [
        'kind', 'obligation', 'turn', 'call', 'message']


def test_kill_before_the_mutating_dispatch_leaves_no_effect(tmp_path):
    """Experiment 4l (control): a caller lost before the boundary binds has no
    durable state. The fresh-process recovery finds nothing to restore, appends
    nothing, and the marker was never touched.
    """
    marker, manifest, executor = _prepare_marker(tmp_path, 'control')
    directory = tmp_path / 'control'

    class Killer:
        """Stands in for the process death at the pre-dispatch seam."""

        def __init__(self, inner):
            self.inner = inner

        def __call__(self, call):
            raise InterruptedError('process lost before dispatch')

        def validate(self, call):
            return self.inner.validate(call)

        @property
        def cancelled(self):
            return self.inner.cancelled

        @property
        def boundary(self):
            return self.inner.boundary

    class Session:
        def __init__(self):
            self.turns = _marker_turns()

        def turn(self, context):
            return self.turns.pop(0)

    @contextmanager
    def open_session():
        yield Session()

    with pytest.raises(InterruptedError, match='process lost before dispatch'):
        run_exact_inspection(open_session, directory, manifest,
                             Killer(executor), complete=lambda context: True)

    journal = directory / 'journal.jsonl'
    rows_before = journal.read_bytes()
    assert marker.read_bytes() == b''
    outcomes = (directory / 'effects/outcomes.jsonl')
    assert not outcomes.exists() or outcomes.read_text() == ''
    for _ in range(2):
        assert reconcile_caller_journal(journal, executor.boundary,
                                        authorized=lambda call: True) is None
        assert journal.read_bytes() == rows_before
