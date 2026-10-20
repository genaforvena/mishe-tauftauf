"""Classification of failed native wake attempts and bounded fresh-wake recovery."""
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import native_recovery as recovery

NETWORK = {'kind': 'network', 'message': 'ECONNRESET: The socket connection was closed unexpectedly.'}
REJECTED = {'kind': 'status', 'status': 400}


def intent(operation_id='op-1'):
    """A valid effect-ledger intent record."""
    return {'record_type': 'intent', 'operation_id': operation_id, 'capability': 'wall_dm',
            'capability_version': '1', 'arguments': {}, 'request_digest': 'digest',
            'authority': 'owned-docs-wake-70040', 'writer_id': 'docs-native-70040',
            'budget': None, 'native_call': None}


def start(operation_id='op-1'):
    """A valid effect-ledger dispatch start record."""
    return start_record(operation_id)


def start_record(operation_id='op-1'):
    """A valid effect-ledger dispatch start record."""
    return {'record_type': 'start', 'operation_id': operation_id,
            'writer_id': 'docs-native-70040', 'started_at': 1.0}


def outcome(operation_id='op-1', status='completed'):
    """A valid effect-ledger outcome record."""
    return {'record_type': 'outcome', 'operation_id': operation_id, 'status': status,
            'result': None, 'failure': None, 'executions': 0, 'reconciliation_ref': None,
            'native_call': None}


def attempt(tmp_path, *, settled=False, status='unknown', turns=1, calls=0,
            failure=NETWORK, intents=(), starts=(), outcomes=(), journal=None,
            summary=True, name='70040', ledger=True, failure_record=True):
    """Build one attempt directory with the recorded bytes of a failed wake."""
    directory = tmp_path / 'wakes' / name
    (directory / 'effects').mkdir(parents=True)
    if ledger:
        for filename, rows in (('intents', intents), ('starts', starts), ('outcomes', outcomes)):
            (directory / 'effects' / f'{filename}.jsonl').write_text(
                ''.join(json.dumps(row) + '\n' for row in rows))
    if summary:
        ledger_rows = []
        if failure is not None:
            ledger_rows.append({'allowance': 1, 'workClass': '', 'dispatches': 1,
                                 'upstream_failure': failure})
        (directory / 'result.json').write_text(json.dumps({
            'wake': 70040, 'settled': settled, 'status': status, 'turns': turns,
            'calls': calls, 'fetch_ledger': ledger_rows}, indent=2))
    if journal is None:
        journal = [{'kind': 'model_failure', 'fetch_ledger':
                    [{'upstream_failure': failure}]}] if failure else [{'kind': 'stop'}]
    (directory / 'journal.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in journal))
    if failure_record:
        # The failed wake's own traceback: the record that its writer ended.
        (directory / recovery.FAILURE_RECORD).write_text(
            'Traceback (most recent call last):\n'
            '  File "worker.py", line 1, in run\n'
            'ConnectionError: ECONNRESET\n')
    return directory


def test_effect_free_transient_failure_is_recoverable(tmp_path):
    directory = attempt(tmp_path)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RETRYABLE
    assert result.recoverable is True
    assert result.dispatched == 0
    assert 'result.json' in ' '.join(result.evidence)


def test_dispatched_effect_requires_reconciliation(tmp_path):
    directory = attempt(tmp_path, intents=[intent()], starts=[start()], outcomes=[outcome()])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RECONCILE
    assert result.recoverable is False
    assert result.dispatched == 2
    assert result.reserved == 1


def test_started_without_outcome_requires_reconciliation(tmp_path):
    # A dispatch start without an outcome means the effect may have happened.
    directory = attempt(tmp_path, intents=[intent()], starts=[start()])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RECONCILE
    assert result.recoverable is False


def test_outcome_without_start_requires_reconciliation(tmp_path):
    directory = attempt(tmp_path, intents=[intent()], outcomes=[outcome()])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RECONCILE
    assert result.recoverable is False


def test_reserved_intent_without_dispatch_is_not_an_effect(tmp_path):
    directory = attempt(tmp_path, intents=[intent()])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RETRYABLE
    assert result.dispatched == 0
    assert result.reserved == 1


def test_proposal_row_requires_reconciliation(tmp_path):
    directory = attempt(tmp_path, journal=[{'kind': 'proposal'}, {'kind': 'stop'}])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RECONCILE
    assert result.recoverable is False


def test_tool_result_row_requires_reconciliation(tmp_path):
    directory = attempt(tmp_path, journal=[{'kind': 'tool_result', 'call_id': 'c1'}])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RECONCILE
    assert result.recoverable is False


def test_malformed_journal_is_unknown(tmp_path):
    directory = attempt(tmp_path)
    (directory / 'journal.jsonl').write_text('{"kind": "proposal"\n')
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_truncated_journal_is_unknown(tmp_path):
    directory = attempt(tmp_path)
    (directory / 'journal.jsonl').write_text('{"kind": "stop"}')
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_misfiled_start_record_in_the_intent_journal_is_unknown(tmp_path):
    # A start filed in the wrong journal is corrupt evidence, not a reservation.
    directory = attempt(tmp_path, intents=[start()])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_malformed_ledger_record_is_unknown(tmp_path):
    directory = attempt(tmp_path, intents=[{'operation_id': 'op-1'}])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_truncated_ledger_record_is_unknown(tmp_path):
    directory = attempt(tmp_path, intents=[intent()])
    path = directory / 'effects' / 'intents.jsonl'
    path.write_text(json.dumps(intent()))
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_missing_effect_ledger_is_unknown(tmp_path):
    directory = attempt(tmp_path, ledger=False)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_a_live_writer_in_the_store_is_unknown(tmp_path):
    directory = attempt(tmp_path)
    lock = directory / 'effects' / recovery.STORE_LOCK
    lock.touch()
    with lock.open('r+') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert 'writer' in result.reason
    assert result.recoverable is False


def test_missing_summary_falls_back_to_the_journals_own_failure(tmp_path):
    # A wake that died before writing its summary still recorded its failure.
    directory = attempt(tmp_path, summary=False)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RETRYABLE
    assert result.recoverable is True


def test_missing_summary_without_a_recorded_failure_is_unknown(tmp_path):
    directory = attempt(tmp_path, summary=False, failure=None, journal=[{'kind': 'stop'}])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_missing_attempt_directory_is_unknown(tmp_path):
    result = recovery.classify_attempt(tmp_path / 'absent')
    assert result.verdict == recovery.UNKNOWN


def test_settled_attempt_needs_no_recovery(tmp_path):
    directory = attempt(tmp_path, settled=True, status='complete', turns=7, calls=14)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.SETTLED
    assert result.recoverable is False


def test_completed_loop_without_a_confirmed_settlement_requires_reconciliation(tmp_path):
    directory = attempt(tmp_path, settled=False, status='complete')
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RECONCILE
    assert result.recoverable is False


def test_deterministic_failure_is_not_a_retry_candidate(tmp_path):
    directory = attempt(tmp_path, failure=REJECTED)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_a_terminal_rejection_is_not_masked_by_an_earlier_transient_failure(tmp_path):
    directory = attempt(
        tmp_path, failure=REJECTED,
        journal=[{'kind': 'model_output', 'response': {'fetch_ledger':
                    [{'upstream_failure': NETWORK}]}},
                 {'kind': 'model_failure', 'fetch_ledger': [{'upstream_failure': REJECTED}]}])
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_transient_server_status_is_a_retry_candidate(tmp_path):
    directory = attempt(tmp_path, failure={'kind': 'status', 'status': 503})
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.RETRYABLE
    assert result.recoverable is True


@pytest.mark.parametrize('status,expected', [
    (408, True), (429, True), (503, True), (400, False), (None, False), ('503', False),
])
def test_transient_status_classification(status, expected):
    assert recovery.transient_status(status) is expected


def test_fresh_attempt_is_bounded_and_never_reused(tmp_path):
    wakes = tmp_path / 'wakes'
    first = recovery.fresh_attempt(wakes, 70040)
    assert first.name == '70040-retry-1'
    first.mkdir(parents=True)
    assert recovery.fresh_attempt(wakes, 70040).name == '70040-retry-2'
    assert recovery.fresh_attempt(wakes, 70040, limit=1) is None
    with pytest.raises(ValueError):
        recovery.fresh_attempt(wakes, 70040, limit=0)


def test_busy_marker_is_released_only_for_its_own_wake(tmp_path):
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    assert recovery.release_busy(busy, 70041) is False
    assert busy.exists()
    assert recovery.release_busy(busy, 70040) is True
    assert not busy.exists()
    assert recovery.release_busy(busy, 70040) is False


def test_busy_marker_replaced_before_removal_is_kept(tmp_path, monkeypatch):
    # Another wake replaces the marker just before it is claimed: the claim
    # must read the replacement and put it back, not remove it.
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    real_rename = os.rename

    def replacing(source, target):
        if Path(source) == busy:
            busy.write_text('70041')
        return real_rename(source, target)

    monkeypatch.setattr(recovery.os, 'rename', replacing)
    assert recovery.release_busy(busy, 70040) is False
    assert busy.read_text() == '70041'


def test_a_marker_published_while_a_mismatch_is_restored_survives(tmp_path, monkeypatch):
    # Another wake publishes its own marker while the claim is being read: the
    # refused release must not replace it with the older marker.
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70041')
    real_read_bytes = Path.read_bytes

    def publishing(self):
        data = real_read_bytes(self)
        if self.name.startswith('docs-native.busy.claimed-'):
            replacement = tmp_path / 'published'
            replacement.write_text('70042')
            os.replace(replacement, busy)
        return data

    monkeypatch.setattr(Path, 'read_bytes', publishing)
    assert recovery.release_busy(busy, 70040) is False
    assert busy.read_text() == '70042'


def test_busy_marker_with_extra_bytes_is_not_released(tmp_path):
    busy = tmp_path / 'docs-native.busy'
    busy.write_bytes(b'70040' + b' ' * 4091 + b'70041')
    assert recovery.release_busy(busy, 70040) is False
    assert busy.exists()


def test_evidence_that_moves_while_it_is_classified_is_unknown(tmp_path, monkeypatch):
    directory = attempt(tmp_path)
    real_read = recovery._read_evidence

    def moving(attempt_path):
        files, error = real_read(attempt_path)
        if files.get('result.json') is not None:
            (Path(attempt_path) / 'result.json').write_bytes(b'{')
        return files, error

    monkeypatch.setattr(recovery, '_read_evidence', moving)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_evidence_that_moves_after_the_last_read_is_unknown(tmp_path, monkeypatch):
    # A writer that admits an effect after the protected reads still leaves the
    # verdict UNKNOWN: the final read sees the moved bytes.
    directory = attempt(tmp_path)
    real_read = recovery._read_evidence
    reads = []

    def moving(attempt_path):
        files, error = real_read(attempt_path)
        reads.append(files)
        if len(reads) == 2:
            start = Path(attempt_path) / 'effects' / 'starts.jsonl'
            start.write_bytes(json.dumps(start_record()).encode() + b'\n')
        return files, error

    monkeypatch.setattr(recovery, '_read_evidence', moving)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


@pytest.mark.parametrize('record', [b'', b'Traceback (most recent call last):\n', b'\xff'])
def test_an_incomplete_failure_record_is_unknown(tmp_path, record):
    directory = attempt(tmp_path)
    (directory / recovery.FAILURE_RECORD).write_bytes(record)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_a_failure_record_that_changes_while_classifying_is_unknown(tmp_path, monkeypatch):
    directory = attempt(tmp_path)
    real_read = recovery._read_evidence
    reads = []

    def moving(attempt_path):
        files, error = real_read(attempt_path)
        reads.append(files)
        if len(reads) == 2:
            (Path(attempt_path) / recovery.FAILURE_RECORD).write_bytes(b'\xff')
        return files, error

    monkeypatch.setattr(recovery, '_read_evidence', moving)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_an_attempt_without_its_writers_failure_record_is_unknown(tmp_path):
    # Nothing in the bytes says the writer ended, so the attempt is not a
    # retry candidate even though it looks effect-free and transient.
    directory = attempt(tmp_path, failure_record=False)
    result = recovery.classify_attempt(directory)
    assert result.verdict == recovery.UNKNOWN
    assert result.recoverable is False


def test_a_writer_holding_the_store_is_detected(tmp_path):
    directory = attempt(tmp_path)
    lock = directory / 'effects' / recovery.STORE_LOCK
    lock.touch()
    with lock.open('r+') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        assert recovery._writer_active(directory / 'effects') is True
    assert recovery._writer_active(directory / 'effects') is False


def _cli(*args):
    return subprocess.run([sys.executable, '-m', 'mishe_tauftauf.native_recovery', *args],
                          capture_output=True, text=True,
                          env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
                               'PATH': '/usr/bin:/bin'})


def test_cli_applies_recovery_only_for_an_effect_free_transient_attempt(tmp_path):
    directory = attempt(tmp_path)
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply')
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report['applied'] is True
    assert report['fresh_attempt'].endswith('70040-retry-1')
    assert not busy.exists()


def test_cli_refuses_recovery_when_an_effect_was_dispatched(tmp_path):
    attempt(tmp_path, intents=[intent()], starts=[start()])
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply')
    assert result.returncode == 1
    assert json.loads(result.stdout)['applied'] is False
    assert busy.exists()


def test_cli_refuses_a_busy_marker_naming_another_wake(tmp_path):
    attempt(tmp_path)
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70041')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply')
    assert result.returncode == 1
    assert busy.exists()


def test_cli_recovers_a_wake_whose_first_fresh_attempt_also_failed(tmp_path):
    attempt(tmp_path)
    attempt(tmp_path, name='70040-retry-1')
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply')
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report['applied'] is True
    assert report['fresh_attempt'].endswith('70040-retry-2')
    assert not busy.exists()


def test_cli_refuses_when_an_earlier_attempt_admitted_an_effect(tmp_path):
    attempt(tmp_path, intents=[intent()], starts=[start()])
    attempt(tmp_path, name='70040-retry-1')
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply')
    assert result.returncode == 1
    assert 'requires-reconciliation' in json.loads(result.stdout)['reason']
    assert busy.exists()


def test_cli_refuses_when_an_earlier_attempt_has_unknown_evidence(tmp_path):
    # The earlier attempt holds a valid start but a torn outcome journal: its
    # own evidence is unknown, so the wake cannot authorize a fresh attempt.
    directory = attempt(tmp_path, intents=[intent()], starts=[start()])
    (directory / 'effects' / 'outcomes.jsonl').write_bytes(b'{')
    attempt(tmp_path, name='70040-retry-1')
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply')
    assert result.returncode == 1
    assert 'unknown' in json.loads(result.stdout)['reason']
    assert busy.exists()


def test_cli_refuses_when_an_earlier_attempt_already_settled(tmp_path):
    # A settled wake needs no further attempt: only a retryable attempt
    # authorizes a recovery.
    attempt(tmp_path, settled=True, status='complete')
    attempt(tmp_path, name='70040-retry-1')
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply')
    assert result.returncode == 1
    assert 'settled' in json.loads(result.stdout)['reason']
    assert busy.exists()


def test_cli_orders_fresh_attempts_numerically(tmp_path):
    attempt(tmp_path)
    for number in range(1, 10):
        attempt(tmp_path, name=f'70040-retry-{number}')
    attempt(tmp_path, name='70040-retry-10', failure=REJECTED)
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply', '--limit', '12')
    assert result.returncode == 1
    assert busy.exists()


def test_cli_refuses_when_the_fresh_attempt_bound_is_exhausted(tmp_path):
    attempt(tmp_path)
    attempt(tmp_path, name='70040-retry-1')
    attempt(tmp_path, name='70040-retry-2')
    attempt(tmp_path, name='70040-retry-3')
    busy = tmp_path / 'docs-native.busy'
    busy.write_text('70040')
    result = _cli('--attempts', str(tmp_path / 'wakes'), '--wake', '70040',
                  '--busy', str(busy), '--apply')
    assert result.returncode == 1
    assert 'exhausted' in json.loads(result.stdout)['reason']
    assert busy.exists()


def test_cli_reports_every_attempt_without_applying(tmp_path):
    attempt(tmp_path, name='70040')
    attempt(tmp_path, name='70041', intents=[intent()], starts=[start()])
    result = _cli('--attempts', str(tmp_path / 'wakes'))
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert [row['verdict'] for row in report['results']] == [
        recovery.RETRYABLE, recovery.RECONCILE]


def test_disposition_starts_a_wake_that_has_no_attempt(tmp_path):
    disposition = recovery.attempt_disposition(tmp_path / 'wakes', 70040)
    assert disposition.action == 'start'
    assert disposition.attempt == tmp_path / 'wakes' / '70040'
    assert disposition.recoverable is False


def test_disposition_authorizes_a_fresh_attempt_after_an_effect_free_failure(tmp_path):
    attempt(tmp_path)
    disposition = recovery.attempt_disposition(tmp_path / 'wakes', 70040)
    assert disposition.action == 'fresh-attempt'
    assert disposition.attempt == tmp_path / 'wakes' / '70040-retry-1'
    assert disposition.recoverable is True


def test_disposition_holds_a_wake_whose_attempt_dispatched_an_effect(tmp_path):
    attempt(tmp_path, intents=[intent()], starts=[start()], outcomes=[outcome()])
    disposition = recovery.attempt_disposition(tmp_path / 'wakes', 70040)
    assert disposition.action == 'reconcile'
    assert disposition.attempt is None
    assert disposition.verdict == recovery.RECONCILE
    assert disposition.recoverable is False


def test_disposition_holds_a_wake_when_one_of_its_attempts_is_unknown(tmp_path):
    attempt(tmp_path)
    attempt(tmp_path, name='70040-retry-1', failure_record=False)
    disposition = recovery.attempt_disposition(tmp_path / 'wakes', 70040)
    assert disposition.action == 'reconcile'
    assert disposition.verdict == recovery.UNKNOWN


def test_disposition_stops_at_the_bounded_number_of_fresh_attempts(tmp_path):
    for number in range(1, 4):
        attempt(tmp_path, name=f'70040-retry-{number}')
    disposition = recovery.attempt_disposition(tmp_path / 'wakes', 70040)
    assert disposition.action == 'reconcile'
    assert 'bounded fresh attempt' in disposition.reason


def test_disposition_leaves_a_wake_whose_writer_is_active(tmp_path):
    directory = attempt(tmp_path)
    lock = directory / 'effects' / recovery.STORE_LOCK
    lock.touch()
    with lock.open('r+') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        disposition = recovery.attempt_disposition(tmp_path / 'wakes', 70040)
    assert disposition.action == 'live'
    assert disposition.recoverable is False


def test_blocked_state_records_the_held_wake(tmp_path):
    path = recovery.write_blocked_state(tmp_path, 'docs', 70477, verdict=recovery.RECONCILE,
                                        reason='an earlier attempt dispatched an effect')
    assert path == tmp_path / 'journals' / 'docs-native.blocked.json'
    record, reason = recovery.read_blocked_state(tmp_path, 'docs')
    assert reason is None
    assert record['wake'] == 70477
    assert record['verdict'] == recovery.RECONCILE
    assert record['reason'] == 'an earlier attempt dispatched an effect'


def test_blocked_state_is_cleared_when_the_wake_moves(tmp_path):
    recovery.write_blocked_state(tmp_path, 'docs', 70477, verdict=recovery.RECONCILE, reason='held')
    assert recovery.clear_blocked_state(tmp_path, 'docs') is True
    assert recovery.clear_blocked_state(tmp_path, 'docs') is False
    assert recovery.read_blocked_state(tmp_path, 'docs') == (None, None)


def test_blocked_state_is_read_per_role(tmp_path):
    recovery.write_blocked_state(tmp_path, 'docs', 70477, verdict=recovery.RECONCILE, reason='held')
    assert recovery.read_blocked_state(tmp_path, 'genome') == (None, None)


def test_blocked_state_that_cannot_be_decoded_stays_visible(tmp_path):
    path = recovery.blocked_state_path(tmp_path, 'docs')
    path.parent.mkdir(parents=True)
    path.write_text('{not json', encoding='utf-8')
    record, reason = recovery.read_blocked_state(tmp_path, 'docs')
    assert record is None
    assert reason and 'malformed' in reason


def test_disposition_counts_the_operators_recovery_attempt_of_the_wake(tmp_path):
    # An effect an operator's recovery attempt dispatched must block a later
    # attempt of the same wake.
    attempt(tmp_path, name='70040-recovery-1', intents=[intent()], starts=[start()],
            outcomes=[outcome()])
    disposition = recovery.attempt_disposition(tmp_path / 'wakes', 70040)
    assert disposition.action == 'reconcile'
    assert disposition.verdict == recovery.RECONCILE


def test_disposition_needs_no_attempt_for_a_settled_wake(tmp_path):
    attempt(tmp_path, settled=True, status='complete')
    disposition = recovery.attempt_disposition(tmp_path / 'wakes', 70040)
    assert disposition.action == 'settled'
    assert disposition.attempt is None
    assert disposition.settled is True


def test_attempt_summary_names_the_capabilities_an_attempt_dispatched(tmp_path):
    directory = attempt(tmp_path, intents=[intent()], starts=[start()], outcomes=[outcome()])
    summary, error = recovery.attempt_summary(directory)
    assert error is None
    assert summary['capabilities'] == ['wall_dm']
    assert summary['settled'] is False and summary['status'] == 'unknown'


def test_attempt_summary_reports_why_an_attempt_cannot_be_read(tmp_path):
    directory = attempt(tmp_path, ledger=False)
    summary, error = recovery.attempt_summary(directory)
    assert summary == {} and error


def test_attempt_summary_rejects_a_torn_dispatch_journal(tmp_path):
    """Corrupt dispatch evidence must not read as a clean attempt."""
    directory = attempt(tmp_path, intents=[intent()], starts=[start()], outcomes=[outcome()])
    outcomes = directory / 'effects' / 'outcomes.jsonl'
    outcomes.write_text(outcomes.read_text()[:-12])  # a torn record
    summary, error = recovery.attempt_summary(directory)
    assert summary == {} and error and 'outcomes.jsonl' in error
    # The classifier keeps the same verdict for the same bytes.
    assert recovery.classify_attempt(directory).verdict == recovery.UNKNOWN


def test_settling_a_wake_clears_its_hold(tmp_path):
    """A settled wake is not held: its record must not refuse the next wake."""
    import json as _json
    from datetime import datetime, timedelta, timezone
    from mishe_tauftauf import wall
    from mishe_tauftauf.feed import Feed
    (tmp_path / 'coordination-mode.json').write_text(_json.dumps(
        {'mode': 'wall', 'until': (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()}))
    recovery.write_blocked_state(tmp_path, 'genome', 1, verdict=recovery.RECONCILE,
                                reason='an earlier attempt dispatched an effect')
    wake = Feed(tmp_path).append('seed', 'seed wake genome observation=1\nRead your wall.')
    wall.settle(tmp_path, 'genome', wake.sequence, 'The wake is done.', False, 'changed')
    record, reason = recovery.read_blocked_state(tmp_path, 'genome')
    assert record is None and reason is None


def test_attempt_summary_rejects_a_malformed_loop_journal(tmp_path):
    """A corrupt loop journal must not read as a clean attempt."""
    directory = attempt(tmp_path)
    (directory / 'journal.jsonl').write_text('{')
    summary, error = recovery.attempt_summary(directory)
    assert summary == {} and error and 'journal.jsonl' in error
    assert recovery.classify_attempt(directory).verdict == recovery.UNKNOWN


def test_clearing_a_hold_keeps_another_wakes_hold(tmp_path):
    recovery.write_blocked_state(tmp_path, 'genome', 7, verdict=recovery.RECONCILE, reason='held')
    assert recovery.clear_blocked_state(tmp_path, 'genome', 6) is False
    record, reason = recovery.read_blocked_state(tmp_path, 'genome')
    assert record is not None and record['wake'] == 7
    assert recovery.clear_blocked_state(tmp_path, 'genome', 7) is True


def test_settling_a_wake_again_clears_a_hold_it_could_not_clear_before(tmp_path, monkeypatch):
    """The idempotent settlement path must clean up too."""
    import json as _json
    from datetime import datetime, timedelta, timezone
    from mishe_tauftauf import wall
    from mishe_tauftauf.feed import Feed
    (tmp_path / 'coordination-mode.json').write_text(_json.dumps(
        {'mode': 'wall', 'until': (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()}))
    wake = Feed(tmp_path).append('seed', 'seed wake genome observation=1\nRead your wall.')
    recovery.write_blocked_state(tmp_path, 'genome', wake.sequence, verdict=recovery.RECONCILE,
                                reason='an earlier attempt dispatched an effect')
    real_unlink = Path.unlink
    refused = []

    def refuse_once(self, *args, **kwargs):
        if self.name == 'genome-native.blocked.json' and not refused:
            refused.append(True)
            raise OSError('cannot remove')
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', refuse_once)
    wall.settle(tmp_path, 'genome', wake.sequence, 'The wake is done.', False, 'changed')
    assert recovery.read_blocked_state(tmp_path, 'genome')[0] is not None
    # The retry takes the idempotent path and must still clean up.
    result = wall.settle(tmp_path, 'genome', wake.sequence, 'The wake is done.', False, 'changed')
    assert 'already settled' in result
    assert recovery.read_blocked_state(tmp_path, 'genome')[0] is None
