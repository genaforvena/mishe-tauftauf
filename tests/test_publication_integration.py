import json
import sys

import pytest

from mishe_tauftauf import seed, task_state
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.post_check import CorrectionRequired
from tests.test_mind_choice import ready_site, wake


def checker(home, bad_stage='post', bad_word='[reject]'):
    worker = home / 'checker.py'
    worker.write_text('import json,sys\nr=json.load(sys.stdin)\n'
                      f'bad=r["stage"]=={bad_stage!r} and {bad_word!r} in r["body"]\n'
                      'print(json.dumps({"version":1,"input_hash":r["input_hash"],"results":'
                      '[{"id":q["id"],"verdict":"suspicious" if bad else "clear",'
                      '"reason":"Fixture detects the stated violation.","evidence":[]} for q in r["questions"]]}))\n')
    (home / 'publication-check.json').write_text(json.dumps({'command': [sys.executable, str(worker)], 'timeout_seconds': 2}))


def test_raw_json_is_refused_through_public_feed_boundary(tmp_path):
    with pytest.raises(CorrectionRequired):
        Feed(tmp_path).append('genome', '{"status":"done","counts":[1,2,3],"nested":{"a":1}}')
    assert Feed(tmp_path).tail_sequence() == 0


def test_retry_episode_includes_registered_wait_beyond_recent_chat(tmp_path, monkeypatch):
    from mishe_tauftauf.coordination_checks import episode
    _, proof = ready_site(tmp_path, monkeypatch)
    registered = task_state.wait_for(tmp_path, 'repair', 'genome', 'Deploy after final CI passes.',
                                     'Exact main CI is pending.', proof, retry_event='delivery-repair-updated')
    for number in range(12):
        Feed(tmp_path).append('operator', f'Unrelated checked progress sample {number}: preserve the registered CI wait.')
    body = '[task-event] delivery-repair-updated\nThe registered author wait now has a checked CI transition.'
    first = episode(tmp_path, 'delivery', body, context={'reason': 'The registered wait may proceed.'})
    assert registered.sequence not in first['question_episodes']['R06']['history_scope']['included_sequences']
    for key in ('R06', 'R08'):
        target = first['question_episodes'][key]['retry_targets'][0]
        assert target['registration']['sequence'] == registered.sequence
        assert target['task']['retry_event'] == 'delivery-repair-updated'
        assert target['task']['next_step'] == 'Deploy after final CI passes.'
        assert target['eligible_before_post'] is False
    task_state.signal(tmp_path, 'delivery-repair-updated', 'delivery', proof, 'Checked final CI releases the author wait.')
    second = episode(tmp_path, 'delivery', body, context={'reason': 'The registered wait may proceed.'})
    assert second['question_episodes']['R08']['retry_targets'][0]['eligible_before_post'] is True


def test_task_transition_distinguishes_registration_from_prior_prose(tmp_path, monkeypatch):
    from dataclasses import asdict, replace
    from mishe_tauftauf.coordination_checks import episode
    _, proof = ready_site(tmp_path, monkeypatch)
    current = task_state.registry(Feed(tmp_path).entries())['repair']
    Feed(tmp_path).append('genome', 'I will register a wait for checked final CI before deploying.')
    proposed = replace(current, status='waiting', retry_event='final-ci-passed', reason='Final CI is pending.')
    context = episode(tmp_path, 'genome', '[task-state] repair\nRegister the final CI wait.', context=asdict(proposed))
    for key in ('R06', 'R08'):
        transition = context['question_episodes'][key]['task_transition']
        assert transition['current']['status'] == 'ready'
        assert transition['proposed']['status'] == 'waiting'
        assert 'status' in transition['changed_fields']
        assert transition['registration']['sequence'] == current.sequence
    receipt = task_state.wait_for(tmp_path, 'repair', 'genome', 'Deploy after final CI.', 'Final CI is pending.', proof,
                                  retry_event='final-ci-passed')
    assert 'moves from ready to waiting' in receipt.body


def test_selection_pitfall_is_returned_before_claim_effect(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'witness')
    checker(tmp_path, 'selection')
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(CorrectionRequired):
        task_state.claim(tmp_path, 'repair', 'witness', attempt, '[reject] The chosen action belongs to a different task.', proof)
    assert Feed(tmp_path).read_bytes() == before
    assert task_state.registry(Feed(tmp_path).entries())['repair'].owner == 'genome'
    assert not task_state.pending_tasks(Feed(tmp_path).entries())


def clear_fixture(home, monkeypatch):
    from types import SimpleNamespace
    state = {'pid':'101', 'rotations':0}
    def tmux(*args):
        if args[0] == 'respawn-pane':
            state['rotations'] += 1
            state['pid'] = str(101 + state['rotations'])
            return SimpleNamespace(stdout=b'')
        return SimpleNamespace(stdout=(state['pid'] if args[-1] == '#{pane_pid}' else '0').encode())
    monkeypatch.setattr(seed, '_tmux', tmux)
    from mishe_tauftauf import tmux as tmux_module
    monkeypatch.setattr(tmux_module, 'owns_session', lambda *args: True)
    monkeypatch.setattr(seed, 'owns_session', lambda *args: True)
    monkeypatch.setattr(seed, '_mind_idle', lambda *args: True)
    monkeypatch.setattr(seed, '_mind_launch_argv', lambda *args: ['mind'])
    monkeypatch.setattr(seed.time, 'sleep', lambda *args: None)
    return state


def test_clear_recovers_completed_effect_without_rotating_twice(tmp_path, monkeypatch):
    ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'witness')
    draft = tmp_path / 'draft.txt'
    draft.write_text('The investigation is checked; review the evidence next.')
    seed.yield_wake(tmp_path, 'witness', attempt, draft, result='verified')
    state = clear_fixture(tmp_path, monkeypatch)
    original = Feed.append
    def interrupted(self, source, body, **kwargs):
        if body.startswith('seed clear'):
            raise OSError('Interrupted before receipt commit')
        return original(self, source, body, **kwargs)
    monkeypatch.setattr(Feed, 'append', interrupted)
    with pytest.raises(OSError):
        seed.clear(tmp_path, 'session', 'witness')
    assert state['rotations'] == 1
    monkeypatch.setattr(Feed, 'append', original)
    seed.clear(tmp_path, 'session', 'witness')
    assert state['rotations'] == 1
    assert seed._state(tmp_path, 'witness')[3] is not None


def test_publication_status_reads_saved_private_verdict_without_draft(tmp_path, capsys):
    from mishe_tauftauf import cli
    (tmp_path / 'publication-check.json').write_text('{}')
    reports = tmp_path / 'post-checks'
    reports.mkdir()
    (reports / 'saved.json').write_text(json.dumps({'status': 'suspicious', 'stage': 'post', 'source': 'witness', 'body': 'private draft must not be displayed'}))
    assert cli.main(['--home', str(tmp_path), 'publication', 'status']) == 0
    output = capsys.readouterr().out
    assert 'suspicious stage=post source=witness' in output
    assert 'private draft must not be displayed' not in output


def test_publication_status_distinguishes_in_progress_private_review(tmp_path, capsys):
    from mishe_tauftauf import cli
    (tmp_path / 'publication-check.json').write_text('{}')
    reports = tmp_path / 'post-checks'
    reports.mkdir()
    (reports / 'saved.json').write_text(json.dumps({'stage': 'post', 'source': 'seed', 'semantic_status': 'untested', 'clear': False}))
    assert cli.main(['--home', str(tmp_path), 'publication', 'status']) == 0
    assert 'pending stage=post source=seed' in capsys.readouterr().out
