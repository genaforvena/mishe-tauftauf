from pathlib import Path
import json
import pytest
from mishe_tauftauf import seed,post_check
from mishe_tauftauf.feed import Feed
from tests.test_mind_choice import ready_site,wake
from tests.test_publication_integration import clear_fixture


def setup_wake(home,monkeypatch):
    ready_site(home,monkeypatch)
    attempt=wake(home,'witness')
    handoff=home/'draft.txt'
    handoff.write_text('The read-only check reproduced the missing outside decision. No mutation occurred. Witness will reconcile new evidence when it arrives.')
    return attempt,handoff


def test_handoff_source_changed_during_review_has_no_effect(tmp_path,monkeypatch):
    attempt,handoff=setup_wake(tmp_path,monkeypatch)
    before=Feed(tmp_path).read_bytes()
    original=post_check.require
    def change(home,source,body,context=None,stage='post'):
        report=original(home,source,body,context=context,stage=stage)
        if body.startswith('seed yield '):handoff.write_text('Changed during review.')
        return report
    monkeypatch.setattr(post_check,'require',change)
    with pytest.raises(ValueError,match='handoff changed'):
        seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
    assert Feed(tmp_path).read_bytes()==before
    assert not (tmp_path/'handoffs'/'witness.md').exists()


def test_verified_archive_guard_prevents_false_yield_and_recovers(tmp_path,monkeypatch):
    attempt,handoff=setup_wake(tmp_path,monkeypatch)
    archived=tmp_path/'artifacts'/f'seed-witness-wake-{attempt}.md'
    original=Feed.append
    tampered=[False]
    def append(self,source,body,**kwargs):
        entry=original(self,source,body,**kwargs)
        if body.startswith('[work]') and not tampered[0]:
            tampered[0]=True;archived.write_text('Corrupt after work receipt.')
        return entry
    monkeypatch.setattr(Feed,'append',append)
    with pytest.raises(ValueError,match='handoff.*match|archive.*match'):
        seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
    assert seed._state(tmp_path,'witness')[1]==attempt
    assert not any(e.body.startswith('seed yield ') for e in Feed(tmp_path).entries())
    archived.write_text(handoff.read_text())
    seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
    entries=Feed(tmp_path).entries()
    assert sum(e.body.startswith('[work]') for e in entries)==1
    assert sum(e.body.startswith('seed yield ') for e in entries)==1


def test_clear_prepared_then_observed_context_and_journal(tmp_path,monkeypatch):
    attempt,handoff=setup_wake(tmp_path,monkeypatch)
    seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
    state=clear_fixture(tmp_path,monkeypatch)
    original=post_check.require;contexts=[]
    def capture(home,source,body,context=None,stage='post'):
        if body.startswith('seed clear '):contexts.append(context)
        return original(home,source,body,context=context,stage=stage)
    monkeypatch.setattr(post_check,'require',capture)
    seed.clear(tmp_path,'session','witness')
    phases=[c['proposed']['transaction']['phase'] for c in contexts]
    assert phases[0]=='prepared' and phases[-1]=='verified'
    observed=contexts[-1]['proposed']['transaction']['postconditions']
    assert observed['process_changed'] and observed['pane_alive']
    assert observed['old_pid']=='101' and observed['new_pid']=='102'
    journal=json.loads((tmp_path/'checks'/f'clear-witness-{attempt}.json').read_text())
    assert journal['phase']=='committed' and state['rotations']==1


def test_clear_postcondition_failure_never_commits_receipt(tmp_path,monkeypatch):
    attempt,handoff=setup_wake(tmp_path,monkeypatch)
    seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
    state=clear_fixture(tmp_path,monkeypatch)
    original=seed._tmux
    def dead(*args):
        result=original(*args)
        if state['rotations'] and args[-1]=='#{pane_dead}':result.stdout=b'1'
        return result
    monkeypatch.setattr(seed,'_tmux',dead)
    with pytest.raises(ValueError,match='rotation'):
        seed.clear(tmp_path,'session','witness')
    assert not any(e.body.startswith('seed clear ') for e in Feed(tmp_path).entries())
    assert state['rotations']==1
    with pytest.raises(ValueError):seed.clear(tmp_path,'session','witness')
    assert state['rotations']==1



@pytest.mark.parametrize('operation', ['yield', 'clear'])
def test_committed_receipt_recovers_journal_failure_without_repeating(tmp_path,monkeypatch,operation):
    attempt,handoff=setup_wake(tmp_path,monkeypatch)
    if operation=='clear':
        seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
        state=clear_fixture(tmp_path,monkeypatch)
    original=post_check._save;failed=[False]
    def save(path,data):
        if path.name==f'{operation}-witness-{attempt}.json' and data.get('phase')=='committed' and not failed[0]:
            failed[0]=True;raise OSError('Crash after canonical receipt')
        return original(path,data)
    monkeypatch.setattr(post_check,'_save',save)
    def invoke():
        return seed.clear(tmp_path,'session','witness') if operation=='clear' else seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
    with pytest.raises(OSError,match='Crash'):invoke()
    before=Feed(tmp_path).read_bytes()
    invoke()
    assert Feed(tmp_path).read_bytes()==before
    assert json.loads((tmp_path/'checks'/f'{operation}-witness-{attempt}.json').read_text())['phase']=='committed'
    if operation=='clear':assert state['rotations']==1



def test_clear_live_process_changed_during_final_review_refuses_receipt(tmp_path,monkeypatch):
    attempt,handoff=setup_wake(tmp_path,monkeypatch)
    seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
    state=clear_fixture(tmp_path,monkeypatch)
    original=post_check.require
    def change(home,source,body,context=None,stage='post'):
        report=original(home,source,body,context=context,stage=stage)
        if body.startswith('seed clear ') and '[record]' in body:state['pid']='999'
        return report
    monkeypatch.setattr(post_check,'require',change)
    with pytest.raises(ValueError,match='postconditions'):
        seed.clear(tmp_path,'session','witness')
    assert not any(e.body.startswith('seed clear ') for e in Feed(tmp_path).entries())
    assert state['rotations']==1


def test_yield_archive_changed_during_final_review_refuses_settlement(tmp_path,monkeypatch):
    attempt,handoff=setup_wake(tmp_path,monkeypatch)
    archive=tmp_path/'artifacts'/f'seed-witness-wake-{attempt}.md'
    original=post_check.require
    def change(home,source,body,context=None,stage='post'):
        report=original(home,source,body,context=context,stage=stage)
        if body.startswith('seed yield ') and context['proposed']['transaction']['phase']=='verified':
            archive.write_text('Changed while reviewing final settlement.')
        return report
    monkeypatch.setattr(post_check,'require',change)
    with pytest.raises(ValueError,match='handoff.*match|archive.*match'):
        seed.yield_wake(tmp_path,'witness',attempt,handoff,result='blocked')
    assert seed._state(tmp_path,'witness')[1]==attempt
    assert not any(e.body.startswith('seed yield ') for e in Feed(tmp_path).entries())


@pytest.mark.parametrize('operation', ['work', 'yield', 'clear'])
def test_final_feed_review_cannot_commit_changed_guards(tmp_path, monkeypatch, operation):
    attempt, handoff = setup_wake(tmp_path, monkeypatch)
    if operation == 'clear':
        seed.yield_wake(tmp_path, 'witness', attempt, handoff, result='blocked')
        state = clear_fixture(tmp_path, monkeypatch)
    prefix = '[work]' if operation == 'work' else 'seed '+operation+' '
    original = post_check.require
    verified_calls = [0]
    def change(home, source, body, context=None, stage='post'):
        report = original(home, source, body, context=context, stage=stage)
        transaction = (context or {}).get('proposed', {}).get('transaction', {})
        if body.startswith(prefix) and transaction.get('phase') == 'verified':
            verified_calls[0] += 1
            if verified_calls[0] == 2:
                if operation != 'clear':
                    (tmp_path/'artifacts'/f'seed-witness-wake-{attempt}.md').write_text('Changed in the final feed review.')
                else:
                    state['pid'] = 'unexpected-process'
        return report
    monkeypatch.setattr(post_check, 'require', change)
    with pytest.raises(post_check.CorrectionRequired) as caught:
        if operation != 'clear':seed.yield_wake(tmp_path, 'witness', attempt, handoff, result='blocked')
        else:seed.clear(tmp_path, 'session', 'witness')
    assert caught.value.report['results'][0]['id'] == 'D03'
    assert caught.value.report['status'] == 'unknown'
    assert verified_calls[0] == 2
    assert not any(e.body.startswith(prefix) for e in Feed(tmp_path).entries())
    if operation != 'clear':assert seed._state(tmp_path, 'witness')[1] == attempt
    else:assert state['rotations'] == 1
    monkeypatch.setattr(post_check, 'require', original)
    if operation != 'clear':
        (tmp_path/'artifacts'/f'seed-witness-wake-{attempt}.md').write_bytes(handoff.read_bytes())
        seed.yield_wake(tmp_path, 'witness', attempt, handoff, result='blocked')
        assert sum(e.body.startswith('[work]') for e in Feed(tmp_path).entries()) == 1
    else:
        state['pid'] = '102'
        seed.clear(tmp_path, 'session', 'witness')
        assert state['rotations'] == 1
    assert sum(e.body.startswith(prefix) for e in Feed(tmp_path).entries()) == 1


def test_work_creation_and_yield_settlement_have_distinct_admission_guards(tmp_path, monkeypatch):
    attempt, handoff = setup_wake(tmp_path, monkeypatch)
    original = post_check.require
    transactions = []
    def capture(home, source, body, context=None, stage='post'):
        transaction = (context or {}).get('proposed', {}).get('transaction')
        if body.startswith(('[work]', 'seed yield ')) and transaction:
            transactions.append((body.startswith('[work]'), transaction))
        return original(home, source, body, context=context, stage=stage)
    monkeypatch.setattr(post_check, 'require', capture)
    seed.yield_wake(tmp_path, 'witness', attempt, handoff, result='blocked')
    work = [t for is_work, t in transactions if is_work]
    yields = [t for is_work, t in transactions if not is_work]
    assert work and yields
    assert all(t['event'] == 'work' and t['expected_effect']['creates_work_receipt'] for t in work)
    assert all('one matching canonical work receipt exists' not in t['required_commit_guards'] for t in work)
    assert all(t['event'] == 'yield' and 'one matching canonical work receipt exists' in t['required_commit_guards'] for t in yields)
    assert any(t['phase'] == 'verified' and t['postconditions']['work_receipt_matches'] for t in yields)


def test_yield_receipt_preserves_known_task_and_action(tmp_path, monkeypatch):
    from mishe_tauftauf import task_state
    attempt, handoff = setup_wake(tmp_path, monkeypatch)
    evidence = tmp_path/'proof.txt'
    task_state.claim(tmp_path, 'repair', 'witness', attempt, 'Inspect the source evidence read-only for this task.', evidence)
    next_step = 'Review the saved report against the exact candidate source before landing.'
    task_state.set_step(tmp_path, 'repair', 'witness', next_step, 'The read-only source checks passed; the report is saved.', evidence)
    seed.yield_wake(tmp_path, 'witness', attempt, handoff, result='verified', continue_task=True)
    receipt = next(e for e in Feed(tmp_path).entries() if e.body.startswith('seed yield '))
    assert 'Task repair continues after context clear.' in receipt.body
    assert next_step in receipt.body


def test_yield_preflight_plans_effect_and_final_receipt_uses_observations(tmp_path, monkeypatch):
    attempt, handoff = setup_wake(tmp_path, monkeypatch)
    original = post_check.require
    drafts = []
    def capture(home, source, body, context=None, stage='post'):
        transaction = (context or {}).get('proposed', {}).get('transaction', {})
        if body.startswith('seed yield '):drafts.append((transaction.get('phase'), body))
        return original(home, source, body, context=context, stage=stage)
    monkeypatch.setattr(post_check, 'require', capture)
    seed.yield_wake(tmp_path, 'witness', attempt, handoff, result='blocked')
    prepared = [body for phase, body in drafts if phase == 'prepared']
    verified = [body for phase, body in drafts if phase == 'verified']
    assert prepared and verified
    assert all('plans to settle' in body and 'This receipt settles' not in body for body in prepared)
    assert all('This receipt settles' in body for body in verified)
    assert next(e for e in Feed(tmp_path).entries() if e.body.startswith('seed yield ')).body in verified
