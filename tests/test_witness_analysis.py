import json
import sys
from pathlib import Path

import pytest

from mishe_tauftauf.feed import Feed


def site(tmp_path):
    home = tmp_path / 'site'
    home.mkdir()
    Feed(home).append('operator', '[wish] Investigate forgotten requests')
    wake = Feed(home).append('seed', 'seed wake witness observation=1\nReal obligation.')
    return home, wake.sequence


def configure(home, tmp_path, response=None):
    worker = tmp_path / 'worker.py'
    calls = tmp_path / 'calls'
    worker.write_text('import sys,json\nfrom pathlib import Path\n'
                      f'p=Path({str(calls)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x")\n'
                      'request=json.load(sys.stdin)\n'
                      f'print(json.dumps({response or {"selection": "ownership-review", "probabilities": {"ownership-review": 1.0}, "model": {"name": "test-model"}}!r}))\n')
    (home / 'analysis-advisor.json').write_text(json.dumps({'command': [sys.executable, str(worker)]}))
    return calls


def test_explicit_missing_config_never_falls_back_to_site_worker(tmp_path):
    from mishe_tauftauf.witness_analysis import advise, status
    home, wake = site(tmp_path)
    calls = configure(home, tmp_path)
    missing = tmp_path / 'missing.json'
    report = advise(home, wake, config_path=missing)
    assert report['state'] == 'unavailable'
    assert report['selection'] == 'unknown'
    assert 'error' in report
    assert not calls.exists()
    assert 'UNKNOWN' in status(home, config_path=missing)
    assert advise(home, wake) == report
    assert not calls.exists()


def test_real_wake_advice_is_cached_and_feedback_preserves_original(tmp_path):
    from mishe_tauftauf.witness_analysis import advise, feedback, status
    home, wake = site(tmp_path)
    calls = configure(home, tmp_path)
    before = (home / 'chat.log').read_bytes()
    first = advise(home, wake)
    assert first['selection'] == 'ownership-review'
    assert first['state'] == 'suggested'
    assert first['request']['state']['entries'][0]['sequence'] == 1
    assert advise(home, wake) == first
    assert calls.read_text() == 'x'
    result = feedback(home, wake, 'evidence-audit', 'useful', 'Conflicting claim checked in artifact.')
    assert result['advice_sha256']
    assert 'used=evidence-audit outcome=useful' in status(home)
    assert advise(home, wake) == first
    assert feedback(home, wake, 'evidence-audit', 'useful', 'Conflicting claim checked in artifact.') == result
    with pytest.raises(ValueError, match='already recorded'):
        feedback(home, wake, 'ownership-review', 'routine', 'Changed label')
    assert (home / 'chat.log').read_bytes() == before


def test_invalid_wake_cannot_run_model(tmp_path):
    from mishe_tauftauf.witness_analysis import advise
    home, wake = site(tmp_path)
    calls = configure(home, tmp_path)
    with pytest.raises(ValueError, match='witness wake'):
        advise(home, 1)
    assert not calls.exists()


def test_snapshot_preserves_source_hashes_and_explicit_omissions(tmp_path):
    from mishe_tauftauf.witness_analysis import snapshot
    home, wake = site(tmp_path)
    for i in range(12):
        Feed(home).append('genome', '[task] task'+str(i)+' owner=genome '+('proof '*250))
    state = snapshot(home, Feed(home).entries()[-1].sequence)
    assert state['context_complete'] is False
    assert state['omitted_entries'] > 0
    assert all(e['body_sha256'] and e['omitted_characters'] > 0 for e in state['entries'])


def test_failed_worker_is_visible_and_does_not_suppress_obligation(tmp_path):
    from mishe_tauftauf.witness_analysis import advise, status
    home, wake = site(tmp_path)
    configure(home, tmp_path, {'selection': 'made-up', 'model': {'name': 'bad'}})
    result = advise(home, wake)
    assert result['state'] == 'unavailable'
    assert result['selection'] == 'unknown'
    assert 'UNKNOWN' in status(home)
    assert result['wake'] == wake


def test_timeout_and_disabled_are_observable(tmp_path):
    from mishe_tauftauf.witness_analysis import advise, status
    home, wake = site(tmp_path)
    assert 'disabled' in status(home)
    assert advise(home, wake)['state'] == 'disabled'
    (home / 'analysis-advisor.json').write_text(json.dumps({'command': [sys.executable, '-c', 'import time;time.sleep(2)'], 'timeout_seconds': .05}))
    result = advise(home, wake)
    assert result['state'] == 'unavailable'
    assert result['error'] == 'worker timeout'


def test_worker_budget_failure_is_unknown_in_pane(tmp_path):
    from mishe_tauftauf.witness_analysis import advise, status
    home, wake = site(tmp_path)
    configure(home, tmp_path, {'selection': 'unknown', 'model': {'name': 'laya'}, 'available': False, 'error': 'context exceeds model budget'})
    assert advise(home, wake)['state'] == 'unavailable'
    assert 'UNKNOWN' in status(home)


def test_unavailable_answer_forces_unknown_and_invalid_flag_is_rejected(tmp_path):
    from mishe_tauftauf.witness_analysis import advise
    home, wake = site(tmp_path)
    configure(home, tmp_path, {'selection': 'evidence-audit', 'model': {'name': 'laya'}, 'available': False})
    assert advise(home, wake)['selection'] == 'unknown'


def test_deeply_nested_worker_json_leaves_visible_failure(tmp_path):
    from mishe_tauftauf.witness_analysis import advise
    home, wake = site(tmp_path)
    (home / 'analysis-advisor.json').write_text(json.dumps({'command': [sys.executable, '-c', 'print("["*31000+"0"+"]"*31000)']}))
    assert advise(home, wake)['state'] == 'unavailable'
    assert (home / 'analysis-advice' / f'{wake}.json').exists()


def test_invalid_available_type_is_rejected(tmp_path):
    from mishe_tauftauf.witness_analysis import advise
    home, wake = site(tmp_path)
    configure(home, tmp_path, {'selection': 'evidence-audit', 'model': {'name': 'laya'}, 'available': 'false'})
    assert advise(home, wake)['state'] == 'unavailable'


def test_wall_outcome_replaces_legacy_without_using_mutable_handoff(tmp_path):
    from mishe_tauftauf.wall import outcome
    from mishe_tauftauf.witness_analysis import snapshot
    home, _ = site(tmp_path)
    legacy = Feed(home).append('seed', '[work] channel=witness wake=2\nHANDOFF:\nOld analysis')
    evidence = home / 'evidence.txt'
    evidence.write_text('measured result')
    report = outcome(home, 'witness', 'accepted', 'Current finding ' * 30, evidence)
    external = Feed(home).append('genome', 'New question')
    (home / 'handoffs').mkdir()
    (home / 'handoffs/witness.md').write_text('Mutable unrelated text')
    Feed(home).append('seed', 'seed yield witness wake=2\nTransport only')
    state = snapshot(home, external.sequence + 1)
    previous = state['previous_context']
    assert previous['sequence'] == report.sequence
    assert previous['status'] == 'verified-author-report'
    assert previous['semantic_acceptance'] == 'UNKNOWN'
    assert previous['text'] == ('Current finding ' * 30).strip()[:240]
    assert previous['omitted_characters'] == len(('Current finding ' * 30).strip()) - 240
    assert [e['sequence'] for e in state['entries']] == [external.sequence]
    assert snapshot(home, legacy.sequence)['previous_context']['text'] == 'Old analysis'
    assert snapshot(home, legacy.sequence)['previous_context']['type'] == 'legacy-work'


@pytest.mark.parametrize('failure', ['missing-reference', 'corrupt-record', 'missing-record', 'missing-evidence', 'changed-evidence', 'outside-evidence'])
def test_invalid_latest_outcome_is_unknown_not_stale_legacy(tmp_path, failure):
    from mishe_tauftauf.wall import outcome
    from mishe_tauftauf.witness_analysis import snapshot
    home, _ = site(tmp_path)
    Feed(home).append('seed', '[work] channel=witness wake=2\nHANDOFF:\nOld analysis')
    evidence = home / 'evidence.txt'
    evidence.write_text('original result')
    latest = outcome(home, 'witness', 'hypothesis-changed', 'New finding', evidence)
    if failure == 'missing-reference':
        latest = Feed(home).append('witness', 'Wall outcome accepted by witness\nUnbound claim')
    elif failure in {'corrupt-record', 'missing-record'}:
        record = home / latest.body.splitlines()[-1].split()[1]
        if failure == 'missing-record':
            record.unlink()
        else:
            record.chmod(0o600)
            record.write_text('{}')
    elif failure == 'missing-evidence':
        evidence.unlink()
    elif failure == 'changed-evidence':
        evidence.write_text('changed result')
    else:
        import hashlib
        outside = tmp_path / 'outside'
        outside.write_text('outside')
        latest = Feed(home).append_record('witness', 'Wall outcome accepted by witness\nOutside',
            {'role': 'witness', 'kind': 'accepted', 'text': 'Outside',
             'evidence': {'path': str(outside), 'sha256': hashlib.sha256(outside.read_bytes()).hexdigest()}},
            kind='wall-outcome')
    if failure in {'corrupt-record', 'missing-record'}:
        # Canonical Feed rejects a broken immutable record before selection.
        with pytest.raises(ValueError, match='immutable record checksum mismatch|record unavailable'):
            snapshot(home, latest.sequence)
        return
    state = snapshot(home, latest.sequence)
    assert state['previous_context']['sequence'] == latest.sequence
    assert state['previous_context']['status'] == 'UNKNOWN'
    assert state['previous_context']['text'] == ''
    assert state['since_context_sequence'] == 0
    assert state['entries'][0]['text'] == '[wish] Investigate forgotten requests'


def test_settlement_alone_is_not_historical_analysis(tmp_path):
    from mishe_tauftauf.witness_analysis import snapshot
    home, _ = site(tmp_path)
    transport = Feed(home).append('seed', 'seed yield witness wake=2\nTransport only')
    state = snapshot(home, transport.sequence)
    assert state['previous_context'] is None
    assert state['since_context_sequence'] == 0
