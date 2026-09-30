import json
import sys
import pytest
from mishe_tauftauf import post_check


def test_private_rejection(tmp_path):
    with pytest.raises(post_check.CorrectionRequired) as caught:
        post_check.require(tmp_path, 'mind', '{"state": "done"}')
    report = json.loads(caught.value.report_path.read_text())
    assert report['body'] == '{"state": "done"}'
    assert not (tmp_path / 'chat.log').exists()
    assert report['status'] == 'suspicious'


def test_unconfigured_semantics_explicitly_untested(tmp_path):
    report = post_check.require(tmp_path, 'mind', 'The candidate passed its tests. Genome will review the evidence next.')
    assert report['semantic_status'] == 'untested'


def test_json_embedded_in_explained_or_multiline_posts_is_always_refused(tmp_path):
    for body in ('Here is the state: {"status":"done"}.',
                 'Example state for audit:\n```json\n{"status":"done"}\n```',
                 'Task state:\n{\n  "status": "done"\n}',
                 'The reported counts are [1, 2, 3].',
                 'Audit example:\n```json\n{\n  "status": "done"\n}\n```'):
        with pytest.raises(post_check.CorrectionRequired):
            post_check.require(tmp_path, 'mind', body)


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
        post_check.require(tmp_path, 'mind', '{"done":true}')
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
