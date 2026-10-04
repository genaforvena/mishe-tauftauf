import json
from mishe_tauftauf import seed
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


def test_canonical_observation_preserves_unknown_as_sensor_evidence(tmp_path, monkeypatch):
    supervisor(tmp_path, monkeypatch)
    from mishe_tauftauf import dashboard
    frame = 'CI: UNKNOWN source unavailable\nSTATE: UNKNOWN'
    monkeypatch.setattr(dashboard, 'read', lambda *args: (frame, False))
    seed.tick(tmp_path, 'session', 'witness')
    observation = next(e for e in Feed(tmp_path).entries() if e.body.startswith('seed observation witness'))
    data = payload(observation)
    assert data['snapshot'] == frame
    assert data['command_ok'] is False
    assert data['notify'] is True
    assert 'renderer command failed' in observation.body
    assert not any(e.body.startswith('[work]') for e in Feed(tmp_path).entries())


def test_canonical_transport_busy_mind_never_sends_keys(tmp_path, monkeypatch):
    from mishe_tauftauf import wall
    sent = []
    monkeypatch.setattr(seed, '_mind_ready', lambda *args: False)
    monkeypatch.setattr(seed, '_send', lambda *args: sent.append(args))
    assert 'busy' in wall.deliver(tmp_path, 'session', 'witness', 1, None, 'Investigate.')
    assert sent == []
    assert not (tmp_path / 'checks/wall-send-witness-1.json').exists()


def test_canonical_transport_failure_is_visible_and_not_immediately_repeated(tmp_path, monkeypatch):
    import pytest
    from mishe_tauftauf import wall
    sent = []
    monkeypatch.setattr(seed, '_mind_ready', lambda *args: True)
    def fail(*args):
        sent.append(args)
        raise RuntimeError('Transport unavailable')
    monkeypatch.setattr(seed, '_send', fail)
    with pytest.raises(RuntimeError, match='Transport unavailable'):
        wall.deliver(tmp_path, 'session', 'witness', 1, None, 'Investigate.')
    journal = json.loads((tmp_path / 'checks/wall-send-witness-1.json').read_text())
    assert journal['phase'] == 'send-failed'
    assert journal['error'] == 'Transport unavailable'
    assert 'held' in wall.deliver(tmp_path, 'session', 'witness', 1, None, 'Investigate.')
    assert len(sent) == 1
    assert not any(e.body.startswith('[work]') for e in Feed(tmp_path).entries())
