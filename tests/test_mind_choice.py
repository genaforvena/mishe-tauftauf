from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from mishe_tauftauf import seed, task_state
from mishe_tauftauf.feed import Feed
from tests.test_task_scheduler import supervisor


def ready_site(home, monkeypatch):
    sent = supervisor(home, monkeypatch)
    (home / 'charters').mkdir()
    (home / 'charters' / 'genome.md').write_text('Advance useful work.')
    (home / 'charters' / 'witness.md').write_text('Investigate coordination.')
    proof = home / 'proof.txt'
    proof.write_text('A scoped useful step is available; no effects performed yet.')
    task_state.add_task(home, 'repair', 'genome', 'Repair the reproduced failure.',
                        'The failure prevents useful work.', proof)
    return sent, proof


def wake(home, owner):
    observation = Feed(home).append('seed', f'seed observation {owner} sha256=' + 'a'*64 +
                                   '\nThe live check changed and needs investigation.')
    return Feed(home).append('seed', f'seed wake {owner} observation={observation.sequence}' +
                            '\nChoose useful work from the shared board and claim it before acting.').sequence


def test_mind_can_claim_another_roles_ready_work_without_helper_offer(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'witness')
    task_state.claim(tmp_path, 'repair', 'witness', attempt,
                     'I can reproduce and repair this coordination fault.', proof)
    entries = Feed(tmp_path).entries()
    assert task_state.pending_tasks(entries) == {('witness', attempt): 'repair'}
    state = task_state.states(entries)['repair']
    assert state.owner == 'witness'
    assert state.attempt_wake == attempt
    assert state.status == 'waiting'
    assert state.activity == 'taking'


def test_competing_claims_have_one_winner_and_do_not_partially_transfer(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    attempts = {role: wake(tmp_path, role) for role in ('genome', 'witness')}
    def take(role):
        try:
            task_state.claim(tmp_path, 'repair', role, attempts[role],
                             'A scoped repair is useful for this charter.', proof)
            return role
        except ValueError:
            return None
    with ThreadPoolExecutor(2) as workers:
        outcomes = list(workers.map(take, attempts))
    winners = [role for role in outcomes if role]
    assert len(winners) == 1
    entries = Feed(tmp_path).entries()
    assert task_state.states(entries)['repair'].owner == winners[0]
    assert len(task_state.pending_tasks(entries)) == 1
    assert len([e for e in entries if e.body.startswith('[task-claim]')]) == 1


def test_waiting_and_terminal_tasks_cannot_be_claimed(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    task_state.wait_for(tmp_path, 'repair', 'genome', 'Resume after producer evidence arrives.',
                        'The producer has not supplied the required evidence.', proof,
                        retry_event='producer-ready')
    attempt = wake(tmp_path, 'witness')
    before = Feed(tmp_path).tail_sequence()
    with pytest.raises(ValueError, match='prerequisite|retry'):
        task_state.claim(tmp_path, 'repair', 'witness', attempt, 'Investigate the repair.', proof)
    assert Feed(tmp_path).tail_sequence() == before
    task_state.finish(tmp_path, 'repair', 'genome', 'Verified the required outcome in the report.', proof)
    before = Feed(tmp_path).tail_sequence()
    with pytest.raises(ValueError, match='terminal|complete'):
        task_state.claim(tmp_path, 'repair', 'witness', attempt, 'Investigate the repair.', proof)
    assert Feed(tmp_path).tail_sequence() == before


def test_claim_requires_matching_active_wake_and_survives_restart(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'genome')
    with pytest.raises(ValueError, match='wake'):
        task_state.claim(tmp_path, 'repair', 'witness', attempt, 'Check the repair.', proof)
    task_state.claim(tmp_path, 'repair', 'genome', attempt, 'Check the reproduced failure.', proof)
    # Replaying from disk rather than a process lease preserves the reservation.
    assert task_state.pending_tasks(Feed(tmp_path).entries()) == {('genome', attempt): 'repair'}
    with pytest.raises(ValueError, match='active|claimed|reserved'):
        task_state.claim(tmp_path, 'repair', 'genome', attempt, 'Check the failure again.', proof)


def test_failed_claim_append_leaves_owner_and_attempt_unchanged(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'witness')
    original = Feed.append_task_control
    def reject(self, source, body, **kwargs):
        if body.startswith('[task-claim]'):
            raise ValueError('Publication rejected; correct the private draft.')
        return original(self, source, body, **kwargs)
    monkeypatch.setattr(Feed, 'append_task_control', reject)
    before = Feed(tmp_path).tail_sequence()
    with pytest.raises(ValueError, match='Publication rejected'):
        task_state.claim(tmp_path, 'repair', 'witness', attempt, 'Check the repair.', proof)
    state = task_state.states(Feed(tmp_path).entries())['repair']
    assert state.owner == 'genome' and state.attempt_wake is None
    assert Feed(tmp_path).tail_sequence() == before


def test_already_completed_dependency_remains_visible_and_admissible(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    task_state.add_task(tmp_path, 'producer', 'genome', 'Produce the repair evidence.',
                        'The consumer needs evidence.', proof)
    task_state.finish(tmp_path, 'producer', 'genome', 'Produced and checked the evidence.', proof)
    task_state.wait_for(tmp_path, 'repair', 'genome', 'Verify completed producer evidence.',
                        'Reconcile the finished producer before acting.', proof, retry_task='producer')
    assert task_state.registry(Feed(tmp_path).entries())['producer'].status == 'done'
    assert 'producer' in '\n'.join(task_state.board(Feed(tmp_path).entries()))
    attempt = wake(tmp_path, 'witness')
    task_state.claim(tmp_path, 'repair', 'witness', attempt, 'Verify the completed producer evidence.', proof)
