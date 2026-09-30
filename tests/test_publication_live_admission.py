import hashlib
import json
from datetime import datetime, timezone
from mishe_tauftauf import seed, post_check
from mishe_tauftauf.coordination_checks import episode
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.records import payload
from tests.test_task_scheduler import supervisor


def test_ordinary_prose_has_truthful_applicability_and_cross_source_history(tmp_path):
    evidence = Feed(tmp_path).append_record('seed', 'The witness check is unresolved; witness will inspect the unavailable probe.', {'role':'witness', 'state':'UNKNOWN'}, kind='observation')
    ep = episode(tmp_path, 'operator', 'Please keep the gate strict. Witness should inspect the unresolved probe.')['question_episodes']['R06']
    assert ep['structured_event_applicability']['applicable'] is False
    assert ep['proposed'] == {}
    assert any(e['sequence'] == evidence.sequence and e['payload']['state'] == 'UNKNOWN' for e in ep['recent_canonical_events'])
    assert f'chat.log sequence {evidence.sequence}' in ep['evidence_references']


def test_missing_known_event_payload_is_not_ordinary_prose(tmp_path):
    for body in ('seed clear witness after=1\nThe process rotation succeeded.', 'seed redeliver witness wake=1\nThe notification succeeded.', 'Verified event.\n[record] records/missing.json sha256='+'a'*64):
        ep = episode(tmp_path,'seed',body)['question_episodes']['R06']
        assert ep['structured_event_applicability']['applicable'] is True
        assert ep['structured_event_applicability']['supplied'] is False


def test_observation_records_exact_scoped_snapshot_prior_state_and_safe_prose(tmp_path,monkeypatch):
    supervisor(tmp_path,monkeypatch)
    first = 'CI: PASS exact main CI\nCOORDINATION: RED missing probe\nSTATE: UNKNOWN\nLATEST CHAT.LOG TEXT\nExample {"status":"done"}\nSTATE: UNKNOWN'
    second = first.replace('RED missing probe','PASS probe restored').replace('STATE: UNKNOWN','STATE: GREEN')
    from mishe_tauftauf import dashboard
    current = [first]
    monkeypatch.setattr(dashboard,'read',lambda *args:(current[0],True))
    seed.tick(tmp_path,'session','witness')
    current[0]=second
    seed.tick(tmp_path,'session','witness')
    obs=[e for e in Feed(tmp_path).entries() if e.body.startswith('seed observation witness')]
    data=payload(obs[-1]);before=payload(obs[0])
    assert data['snapshot']['meaningful_text']==seed._observation_text('witness',second)
    assert hashlib.sha256(data['snapshot']['meaningful_text'].encode()).hexdigest()==data['digest']
    assert data['previous']['state']=='STATE: UNKNOWN'
    assert data['previous']['sequence']==obs[0].sequence
    assert data['previous']['snapshot']==before['snapshot']
    assert data['snapshot']['lease_checked'] and data['snapshot']['origin']=='dashboard.read'
    assert 'witness' in obs[-1].body and 'GREEN' in obs[-1].body and 'UNKNOWN' in obs[-1].body
    assert 'No task completion' in obs[-1].body
    assert post_check._deterministic(obs[-1].body)==[]
    assert '{"status"' not in obs[-1].body


def redelivery_site(home, monkeypatch):
    from datetime import timedelta
    wake=Feed(home).append('seed','seed wake witness observation=1\nWitness must investigate the unavailable probe.',reserved=True)
    class Later(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.now(timezone.utc)+timedelta(seconds=61)
    monkeypatch.setattr(seed,'datetime',Later)
    monkeypatch.setattr(seed,'_mind_ready',lambda *args:True)
    return wake


def test_redelivery_refused_plan_never_sends_keys(tmp_path,monkeypatch):
    wake=redelivery_site(tmp_path,monkeypatch);sent=[]
    monkeypatch.setattr(seed,'_send',lambda *args:sent.append(args))
    original=post_check.require
    def reject(home,source,body,context=None,stage='post'):
        if body.startswith('seed redeliver '):raise ValueError('Plan is not admitted.')
        return original(home,source,body,context=context,stage=stage)
    monkeypatch.setattr(post_check,'require',reject)
    import pytest
    with pytest.raises(ValueError,match='Plan is not admitted'):seed._redeliver_pending(tmp_path,'session','witness',wake.sequence)
    assert sent==[]
    assert Feed(tmp_path).tail_sequence()==wake.sequence


def test_redelivery_receipt_binds_observed_readiness_and_send_without_work_claim(tmp_path,monkeypatch):
    wake=redelivery_site(tmp_path,monkeypatch);sent=[]
    monkeypatch.setattr(seed,'_send',lambda *args:sent.append(args))
    seed._redeliver_pending(tmp_path,'session','witness',wake.sequence)
    record=next(e for e in Feed(tmp_path).entries() if e.body.startswith('seed redeliver '));data=payload(record)
    assert data['wake']==wake.sequence and data['before']['pending_wake_matches']
    assert data['before']['mind_ready_observed'] and data['notification']['phase']=='verified'
    assert data['notification']['send_commands_returned'] is True
    assert data['notification']['mind_started_work_verified'] is False
    assert len(sent)==1


def test_completed_redelivery_recovery_never_repeats_send(tmp_path,monkeypatch):
    import pytest
    wake=redelivery_site(tmp_path,monkeypatch);sent=[]
    monkeypatch.setattr(seed,'_send',lambda *args:sent.append(args))
    original=post_check.require
    def reject(home,source,body,context=None,stage='post'):
        notification=(context or {}).get('proposed',context or {}).get('notification',{})
        if body.startswith('seed redeliver ') and notification.get('phase')=='verified':raise ValueError('Final model unavailable.')
        return original(home,source,body,context=context,stage=stage)
    monkeypatch.setattr(post_check,'require',reject)
    with pytest.raises(ValueError,match='Final model unavailable'):seed._redeliver_pending(tmp_path,'session','witness',wake.sequence)
    assert len(sent)==1 and Feed(tmp_path).tail_sequence()==wake.sequence
    monkeypatch.setattr(post_check,'require',original)
    monkeypatch.setattr(seed,'_mind_ready',lambda *args:False)
    seed._redeliver_pending(tmp_path,'session','witness',wake.sequence)
    assert len(sent)==1
    assert sum(e.body.startswith('seed redeliver ') for e in Feed(tmp_path).entries())==1


def test_uncertain_redelivery_is_not_sent_again(tmp_path,monkeypatch):
    import pytest
    wake=redelivery_site(tmp_path,monkeypatch);sent=[]
    def uncertain(*args):sent.append(args);raise RuntimeError('send outcome unavailable')
    monkeypatch.setattr(seed,'_send',uncertain)
    with pytest.raises(RuntimeError):seed._redeliver_pending(tmp_path,'session','witness',wake.sequence)
    with pytest.raises(ValueError,match='uncertain'):seed._redeliver_pending(tmp_path,'session','witness',wake.sequence)
    assert len(sent)==1 and Feed(tmp_path).tail_sequence()==wake.sequence


def test_redelivery_rechecks_readiness_after_restore_preparation(tmp_path, monkeypatch):
    import pytest
    wake=redelivery_site(tmp_path,monkeypatch);sent=[];ready=[True]
    monkeypatch.setattr(seed,'_mind_ready',lambda *args:ready[0])
    def prepare(*args,**kwargs):
        ready[0]=False
        return 'Restore instructions'
    monkeypatch.setattr(seed,'_restore_text',prepare)
    monkeypatch.setattr(seed,'_send',lambda *args:sent.append(args))
    with pytest.raises(ValueError,match='readiness changed'):
        seed._redeliver_pending(tmp_path,'session','witness',wake.sequence)
    assert not sent
    assert not list((tmp_path/'checks').glob('redeliver-*.json'))


def test_redelivery_rechecks_readiness_after_task_attempt_gate(tmp_path, monkeypatch):
    import pytest
    from mishe_tauftauf import post_check
    from datetime import timedelta
    Feed(tmp_path).append('operator','[task] repair owner=witness source=test acceptance=checked retry=event')
    wake=Feed(tmp_path).append('seed','seed wake witness observation=1 task=repair\nInvestigate the unavailable probe.',reserved=True)
    class Later(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.now(timezone.utc)+timedelta(seconds=61)
    monkeypatch.setattr(seed,'datetime',Later)
    sent=[];ready=[True];original=post_check.require
    monkeypatch.setattr(seed,'_mind_ready',lambda *args:ready[0])
    monkeypatch.setattr(seed,'_send',lambda *args:sent.append(args))
    def admission(home,source,body,**kwargs):
        if body.startswith('[task-state]'):ready[0]=False
        return original(home,source,body,**kwargs)
    monkeypatch.setattr(post_check,'require',admission)
    with pytest.raises(ValueError,match='readiness changed'):
        seed._redeliver_pending(tmp_path,'session','witness',wake.sequence)
    assert not ready[0] and not sent
    assert not list((tmp_path/'checks').glob('redeliver-*.json'))


def test_observation_without_prior_snapshot_establishes_baseline_not_claimed_change(tmp_path,monkeypatch):
    supervisor(tmp_path,monkeypatch)
    from mishe_tauftauf import dashboard
    prior=Feed(tmp_path).append_record('seed','seed observation witness\nEarlier state recorded.',{'role':'witness','state':'STATE: UNKNOWN','digest':'old'},kind='observation')
    monkeypatch.setattr(dashboard,'read',lambda *args:('CI: PASS exact CI\nANOMALY: UNKNOWN missing probe\nSTATE: UNKNOWN',True))
    seed.tick(tmp_path,'session','witness')
    observation=next(e for e in reversed(Feed(tmp_path).entries()) if e.body.startswith('seed observation witness'))
    data=payload(observation)
    assert data['previous']['sequence']==prior.sequence and not data['previous']['snapshot_available']
    assert data['change']=='current scoped baseline; prior semantic comparison unavailable'
    assert 'No semantic change from the earlier observation is verified' in observation.body
    assert 'changed checks' not in observation.body
    wake=next(e for e in reversed(Feed(tmp_path).entries()) if e.body.startswith('seed wake witness'))
    assert 'current scoped observation baseline' in payload(wake)['reason']
