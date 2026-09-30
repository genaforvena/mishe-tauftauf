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


def test_refused_ordinary_post_has_private_report_and_no_sequence(tmp_path):
    Feed(tmp_path).append('operator', 'A scoped repair is ready for independent review.')
    checker(tmp_path)
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(CorrectionRequired) as caught:
        Feed(tmp_path).append('genome', '[reject] This draft makes an unsupported completion claim.')
    assert Feed(tmp_path).read_bytes() == before
    report = json.loads(caught.value.report_path.read_text())
    assert report['body'].startswith('[reject]')
    assert report['status'] == 'suspicious'
    posted = Feed(tmp_path).append('genome', 'The candidate is tested; independent review is still required before landing.')
    assert posted.sequence == 2


def test_raw_json_is_refused_through_public_feed_boundary(tmp_path):
    with pytest.raises(CorrectionRequired):
        Feed(tmp_path).append('genome', '{"status": "done"}')
    assert Feed(tmp_path).tail_sequence() == 0


def test_generated_task_transition_rejection_does_not_close(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    checker(tmp_path)
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(CorrectionRequired):
        task_state.finish(tmp_path, 'repair', 'genome', '[reject] Claimed done without satisfying acceptance.', proof)
    assert task_state.registry(Feed(tmp_path).entries())['repair'].status == 'ready'
    assert Feed(tmp_path).read_bytes() == before


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


def test_handoff_violation_leaves_wake_and_prior_handoff_unchanged(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'witness')
    task_state.claim(tmp_path, 'repair', 'witness', attempt, 'Reproduce and repair the scoped failure.', proof)
    current = tmp_path / 'handoffs' / 'witness.md'
    current.parent.mkdir()
    current.write_text('Prior checked handoff remains authoritative.\n')
    draft = tmp_path / 'draft.txt'
    draft.write_text('[reject] The task is done because it was routed.\nNext: clear context.\n')
    checker(tmp_path, 'handoff')
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(CorrectionRequired):
        seed.yield_wake(tmp_path, 'witness', attempt, draft, result='changed')
    assert Feed(tmp_path).read_bytes() == before
    assert current.read_text() == 'Prior checked handoff remains authoritative.\n'
    assert seed._state(tmp_path, 'witness')[1] == attempt
    assert not (tmp_path / 'artifacts' / f'seed-witness-wake-{attempt}.md').exists()


def test_corrected_handoff_passes_without_repeating_action(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'witness')
    task_state.claim(tmp_path, 'repair', 'witness', attempt, 'Reproduce the scoped failure.', proof)
    task_state.wait_for(tmp_path, 'repair', 'witness', 'Resume when exact producer evidence arrives.',
                        'The required external evidence is missing.', proof, retry_event='exact-evidence-ready')
    checker(tmp_path, 'handoff')
    draft = tmp_path / 'draft.txt'
    draft.write_text('The scoped check confirmed missing producer evidence; no mutation was performed.\n'
                     'Next: resume only when exact-evidence-ready fires.\n')
    seed.yield_wake(tmp_path, 'witness', attempt, draft, result='blocked')
    assert seed._state(tmp_path, 'witness')[1] is None
    assert task_state.registry(Feed(tmp_path).entries())['repair'].status == 'waiting'


def test_final_reference_bearing_receipt_is_checked_before_handoff_effects(tmp_path, monkeypatch):
    _, proof = ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'witness')
    current = tmp_path / 'handoffs' / 'witness.md'
    current.parent.mkdir()
    current.write_text('Prior verified handoff.\n')
    draft = tmp_path / 'draft.txt'
    draft.write_text('The scoped investigation is complete and evidence is recorded.\nNext: review the useful candidate.\n')
    checker(tmp_path, 'post', '[record]')
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(CorrectionRequired):
        seed.yield_wake(tmp_path, 'witness', attempt, draft, result='verified')
    assert Feed(tmp_path).read_bytes() == before
    assert current.read_text() == 'Prior verified handoff.\n'
    assert not (tmp_path / 'artifacts' / f'seed-witness-wake-{attempt}.md').exists()
    assert seed._state(tmp_path, 'witness')[1] == attempt


def test_concurrent_posts_review_current_history(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    worker = tmp_path / "checker.py"
    worker.write_text("import json,sys,time\nr=json.load(sys.stdin)\ntime.sleep(.1)\n"
                      "prior=r['context']['question_episodes']['R08']['previous_same_source']\n"
                      "bad=r['body'] in prior\n"
                      "print(json.dumps({'version':1,'input_hash':r['input_hash'],'results':[{'id':q['id'],'verdict':'suspicious' if bad else 'clear','reason':'Repeated unchanged notice.' if bad else 'First meaningful notice.','evidence':[]} for q in r['questions']]}))")
    (tmp_path / "publication-check.json").write_text(json.dumps({'command':[sys.executable,str(worker)],'timeout_seconds':2}))
    def post():
        try:
            return Feed(tmp_path).append('genome', 'The checked candidate is ready for independent review.').sequence
        except CorrectionRequired:
            return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: post(), range(2)))
    assert sorted(results, key=str) == [1, 'refused']
    assert Feed(tmp_path).tail_sequence() == 1


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
    monkeypatch.setattr(seed, 'owns_session', lambda *args: True)
    monkeypatch.setattr(seed, '_mind_idle', lambda *args: True)
    monkeypatch.setattr(seed, '_mind_launch_argv', lambda *args: ['mind'])
    monkeypatch.setattr(seed.time, 'sleep', lambda *args: None)
    return state


def test_rejected_clear_cannot_rotate_mind(tmp_path, monkeypatch):
    ready_site(tmp_path, monkeypatch)
    attempt = wake(tmp_path, 'witness')
    draft = tmp_path / 'draft.txt'
    draft.write_text('The investigation is checked; review the evidence next.')
    seed.yield_wake(tmp_path, 'witness', attempt, draft, result='verified')
    state = clear_fixture(tmp_path, monkeypatch)
    checker(tmp_path, 'post', 'seed clear')
    before = Feed(tmp_path).read_bytes()
    for _ in range(2):
        with pytest.raises(CorrectionRequired):
            seed.clear(tmp_path, 'session', 'witness')
    assert state['rotations'] == 0
    assert Feed(tmp_path).read_bytes() == before


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
