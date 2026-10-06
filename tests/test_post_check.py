import json
import sys
import pytest
from mishe_tauftauf import post_check


def test_private_rejection(tmp_path):
    with pytest.raises(post_check.CorrectionRequired) as caught:
        post_check.require(tmp_path, 'mind', '{"status":"done","counts":[1,2,3],"nested":{"a":1}}')
    report = json.loads(caught.value.report_path.read_text())
    assert report['body'] == '{"status":"done","counts":[1,2,3],"nested":{"a":1}}'
    assert not (tmp_path / 'chat.log').exists()
    assert report['status'] == 'suspicious'


def test_unconfigured_semantics_explicitly_untested(tmp_path):
    report = post_check.require(tmp_path, 'mind', 'The candidate passed its tests. Genome will review the evidence next.')
    assert report['semantic_status'] == 'untested'


def test_substantial_json_embedded_in_explained_or_multiline_posts_is_refused(tmp_path):
    for body in ('Here is the state: {"status":"done","counts":[1,2,3],"nested":{"a":1}}.',
                 'Audit example:\n```json\n{\n  "status": "done",\n  "counts": [1, 2, 3],\n  "nested": {"a": 1}\n}\n```'):
        with pytest.raises(post_check.CorrectionRequired):
            post_check.require(tmp_path, 'mind', body)

def test_trivial_json_literals_are_prose_not_state_dumps():
    # D01 refuses substantial structured state, not the trivial literals that
    # are natural in readable prose (coordinates, pid lists, one-key dicts).
    # Measured: 24 prose-stage D01 refusals in 6h across 8 roles, all trivial.
    from mishe_tauftauf import post_check
    trivials = (
        '[]', '[1,1,1]', '[64512,8525073]', '{"target": ".mishe-tauftauf/artifacts/x"}',
        '{}', '["type"]', '{"sites": []}', '["v1"]', '[0,1]', '{"text":"a"}',
        'The counts are [1, 2, 3].', 'no drift: []', 'version list ["v1"]',
        'pids [64512, 8525073] leaked', 'config {"sites": []}', 'triple [1, 1, 1]',
    )
    for body in trivials:
        assert not any(row['id'] == 'D01' for row in post_check._deterministic(body)), body
    dumps = (
        '{"status":"done","counts":[1,2,3],"nested":{"a":1}}',
        'Here is the state: {"status":"done","counts":[1,2,3],"nested":{"a":1}}.',
        '{"sense.proc.cpu-busy":"short-window=0.1s busy=99.4%","sense.proc.loadavg":"19.03 13.24","sense.proc.memory-available":"22991064 kB","sense.disk.free":"171813003264"}',
        'Audit example:\n```json\n{\n  "status": "done",\n  "counts": [1, 2, 3],\n  "nested": {"a": 1}\n}\n```',
        '{"a":{"b":1}}',
        '[[1],[2]]',
        '[{"a":1}]',
    )
    for body in dumps:
        assert any(row['id'] == 'D01' for row in post_check._deterministic(body)), body


def test_checker_code_change_invalidates_previous_clearance(tmp_path):
    clear = "import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'version':1,'input_hash':r['input_hash'],'results':[{'id':q['id'],'verdict':'clear','evidence':[], 'reason':'fixture'} for q in r['questions']]}))"
    configure(tmp_path, clear)
    first = post_check.require(tmp_path, 'mind', 'The candidate is tested; genome will review.')
    configure(tmp_path, clear.replace("'verdict':'clear'", "'verdict':'suspicious'"))
    with pytest.raises(post_check.CorrectionRequired) as caught:
        post_check.require(tmp_path, 'mind', 'The candidate is tested; genome will review.')
    assert caught.value.report['input_hash'] != first['input_hash']


def configure(home, code):
    worker = home / 'worker.py'
    worker.write_text(code)
    (home / 'publication-check.json').write_text(json.dumps({'command': [sys.executable, str(worker)], 'timeout_seconds': 1}))


def test_configured_clear_and_changed_draft(tmp_path):
    configure(tmp_path, "import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'version':1,'input_hash':r['input_hash'],'results':[{'id':q['id'],'verdict':'clear','evidence':[], 'reason':'fixture'} for q in r['questions']]}))")
    first = post_check.require(tmp_path, 'mind', 'Tests passed; Genome will review.', context={'event': {'status': 'done'}})
    second = post_check.require(tmp_path, 'mind', 'Tests failed; Genome will repair.', context={'event': {'status': 'done'}})
    assert first['input_hash'] != second['input_hash']
    assert first['semantic_status'] == 'clear'


@pytest.mark.parametrize('code', ["print('{}')", "import time; time.sleep(2)", "import sys; sys.exit(3)"])
def test_configured_unavailable_rejects(tmp_path, code):
    configure(tmp_path, code)
    with pytest.raises(post_check.CorrectionRequired):
        post_check.require(tmp_path, 'mind', 'Tests passed; Genome will review.')


def test_hash_only_rejects(tmp_path):
    with pytest.raises(post_check.CorrectionRequired):
        post_check.require(tmp_path, 'mind', 'Evidence: ' + 'a'*64)


@pytest.mark.parametrize('verdict', ['unknown', 'suspicious'])
def test_explicit_nonclear_refused(tmp_path, verdict):
    configure(tmp_path, "import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'version':1,'input_hash':r['input_hash'],'results':[{'id':q['id'],'verdict':" + repr(verdict) + ", 'evidence':[], 'reason':'fixture'} for q in r['questions']]}))")
    with pytest.raises(post_check.CorrectionRequired) as error:
        post_check.require(tmp_path, 'mind', 'Tests passed; Genome will review.')
    assert error.value.report['status'] == verdict


def test_skipped_questions_refused(tmp_path):
    configure(tmp_path, "import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'version':1,'input_hash':r['input_hash'],'results':[]}))")
    report = post_check.review(tmp_path, 'mind', 'Tests passed; Genome will review.')
    assert report['status'] == 'unknown'


def test_corrected_draft_rechecked(tmp_path):
    with pytest.raises(post_check.CorrectionRequired):
        post_check.require(tmp_path, 'mind', '{"status":"done","counts":[1,2,3],"nested":{"a":1}}')
    report = post_check.require(tmp_path, 'mind', 'The candidate tests passed; Genome will review the test artifact next.')
    assert report['clear']
    assert len(list((tmp_path/'post-checks').glob('*.json'))) == 2



def test_explained_code_example_is_not_state_dump(tmp_path):
    report = post_check.require(tmp_path, 'mind', 'This example shows the expected record format. Use it to inspect the parser.\n```python\nstate = dict(status="done")\n```')
    assert report['clear']


def test_output_budget_refuses(tmp_path):
    configure(tmp_path, "print('x' * 2100000)")
    with pytest.raises(post_check.CorrectionRequired) as caught:
        post_check.require(tmp_path, 'mind', 'Tests passed; Genome will review.')
    assert 'budget' in caught.value.report['error']


def test_worker_transport_keeps_complete_questions_and_private_full_audit(tmp_path, monkeypatch):
    configure(tmp_path, '')
    episodes = {q['id']: {'context_complete': True, 'evidence_references': ['proof.txt'], 'fact': 'complete evidence ' + q['id']} for q in post_check.questions('post')}
    context = {'question_episodes': episodes, 'history': 'full private audit ' * 20000}
    captured = []
    def worker(command, encoded, timeout):
        request = json.loads(encoded)
        captured.append(request)
        audit = json.loads(__import__('pathlib').Path(request['audit_reference']['path']).read_text())
        assert audit['context'] == context
        assert audit['input_hash'] == request['audit_reference']['input_hash'] == request['input_hash']
        assert request['context'] == {'question_episodes': episodes}
        assert len(encoded) < 10000
        return json.dumps({'version': 1, 'input_hash': request['input_hash'], 'results': [{'id': q['id'], 'verdict': 'clear', 'evidence': [], 'reason': 'fixture'} for q in request['questions']]}).encode()
    monkeypatch.setattr(post_check, '_worker', worker)
    first = post_check.require(tmp_path, 'mind', 'The source checks passed; Genome will inspect the report.', context=context)
    changed = dict(context, history=context['history']+'additional audit fact')
    context = changed
    second = post_check.require(tmp_path, 'mind', 'The source checks passed; Genome will inspect the report.', context=changed)
    assert first['input_hash'] != second['input_hash']
    assert len(captured) == 2


def test_transport_refuses_missing_question_episode_before_worker(tmp_path, monkeypatch):
    configure(tmp_path, '')
    def worker(*args):
        pytest.fail('missing scoped input must not invoke the reviewer')
    monkeypatch.setattr(post_check, '_worker', worker)
    report = post_check.review(tmp_path, 'mind', 'The source checks passed; Genome will inspect the report.', context={'question_episodes': {'R01': {'context_complete': True, 'evidence_references': []}}})
    assert report['status'] == 'unknown'
    assert 'question episode' in report['error']
