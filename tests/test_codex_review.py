from unittest.mock import patch
import pytest
from mishe_tauftauf import codex_review


def request():
    return {'version':1,'input_hash':'fixture','source':'mind','stage':'post','body':'The tests passed; genome will review.','context':{'semantic_episode':{'context_complete':True,'evidence_references':['fixture:tests']}},'questions':[{'id':'R06','question':'Does the draft agree with the event?'}]}


def test_unverified_cli_never_launches_model():
    with patch('subprocess.Popen') as spawn:
        result=codex_review.review(request())
    spawn.assert_not_called()
    assert result['results'][0]['verdict']=='unknown'
    assert 'tool-free' in result['results'][0]['reason']


def test_missing_episode_refuses_without_launch():
    req=request();req['context']={}
    result=codex_review.review(req)
    assert 'incomplete' in result['results'][0]['reason']


def test_question_schema_has_exact_ids_and_supplied_evidence():
    schema=codex_review.response_schema(request())
    row=schema['properties']['results']['items']
    assert row['properties']['id']['enum']==['R06']
    assert row['properties']['evidence']['items']['enum']==['fixture:tests']
    assert schema['additionalProperties'] is False


def test_invented_evidence_is_not_accepted():
    result={'version':1,'input_hash':'fixture','results':[{'id':'R06','verdict':'clear','reason':'claim','evidence':['invented:file']}]}
    with pytest.raises(ValueError,match='evidence'):
        codex_review.validate_response(request(),result)


def test_skipped_and_duplicate_questions_cannot_pass():
    for rows in [[],[{'id':'R06','verdict':'clear','reason':'claim','evidence':[]}]*2]:
        with pytest.raises(ValueError):
            codex_review.validate_response(request(),{'version':1,'input_hash':'fixture','results':rows})


def test_output_schema_clearance_is_not_capability_clearance():
    result={'version':1,'input_hash':'fixture','results':[{'id':'R06','verdict':'clear','reason':'record matches','evidence':['fixture:tests']}]}
    assert codex_review.validate_response(request(),result)==result
    assert codex_review.review(request())['results'][0]['verdict']=='unknown'



def fake_probe(tmp_path):
    import hashlib,json
    cli=tmp_path/'codex';cli.write_text('fixture cli')
    probe=tmp_path/'probe.json'
    probe.write_text(json.dumps({'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'returncode':0,'sentinel_unchanged':True,'stderr':'code-mode host is disabled','command':codex_review.command(tmp_path/'schema',tmp_path,tmp_path/'instructions',str(cli))}))
    return cli,probe


def clear_output(req):
    import json
    answer={'version':1,'input_hash':req['input_hash'],'results':[{'id':q['id'],'verdict':'clear','reason':'fixture','evidence':[]} for q in req['questions']]}
    return (json.dumps({'type':'turn.started'})+'\n'+json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(answer)}})+'\n'+json.dumps({'type':'turn.completed'})).encode()


def test_expected_disabled_host_stderr_requires_one_matching_notice(tmp_path):
    import json
    cli,probe=fake_probe(tmp_path)
    notice=json.dumps({'type':'item.completed','item':{'type':'error','message':codex_review.INITIALIZATION_NOTICE}}).encode()+b'\n'
    stderr=b'2026-09-30T12:53:27.957439Z '+codex_review.DISABLED_HOST_STDERR
    outputs=[(notice+clear_output(request()),'clear'),(notice+notice+clear_output(request()),'unknown'),(clear_output(request()),'unknown')]
    for output,expected in outputs:
        with patch.object(codex_review,'_invoke',return_value=(output,stderr)):
            assert codex_review.review(request(),probe_path=probe,cli=str(cli))['results'][0]['verdict']==expected


def test_unexpected_stderr_errors_refuse(tmp_path):
    cli,probe=fake_probe(tmp_path)
    stderr_values=[
        b'ERROR codex_core::server: request failed',
        b'ERROR plugin: earlier failure ERROR codex_core::tools::router: error=code-mode host is disabled',
    ]
    for stderr in stderr_values:
        with patch.object(codex_review,'_invoke',return_value=(clear_output(request()),stderr)):
            assert codex_review.review(request(),probe_path=probe,cli=str(cli))['results'][0]['verdict']=='unknown'

def test_model_tool_event_refuses(tmp_path):
    import json
    cli,probe=fake_probe(tmp_path)
    output=json.dumps({'type':'item.started','item':{'type':'file_change'}}).encode()+b'\n'+clear_output(request())
    with patch.object(codex_review,'_invoke',return_value=(output,b'')):
        assert codex_review.review(request(),probe_path=probe,cli=str(cli))['results'][0]['verdict']=='unknown'


def test_parent_cache_tracks_explicit_cli_and_config(tmp_path,monkeypatch):
    import json,sys
    from mishe_tauftauf import post_check
    cli,probe=fake_probe(tmp_path)
    config=tmp_path/'runtime.toml';config.write_text('model = "fixture"')
    argv=[sys.executable,'-m','mishe_tauftauf.codex_review','--cli',str(cli),'--runtime-config',str(config),'--probe-report',str(probe)]
    (tmp_path/'publication-check.json').write_text(json.dumps({'command':argv}))
    calls=[]
    def worker(command,encoded,timeout):
        req=json.loads(encoded);calls.append(req)
        with patch.object(codex_review,'_invoke',return_value=(clear_output(req),b'')):
            return json.dumps(codex_review.review(req,probe_path=probe,cli=str(cli))).encode()
    monkeypatch.setattr(post_check,'_worker',worker)
    context=request()['context']
    first=post_check.require(tmp_path,'mind',request()['body'],context=context)
    config.write_text('model = "changed-fixture"')
    second=post_check.require(tmp_path,'mind',request()['body'],context=context)
    assert second['input_hash']!=first['input_hash'] and len(calls)==2
    cli.write_text('changed cli')
    with pytest.raises(post_check.CorrectionRequired) as caught:
        post_check.require(tmp_path,'mind',request()['body'],context=context)
    assert caught.value.report['input_hash']!=second['input_hash']
    assert 'CLI changed' in caught.value.report['results'][0]['reason']



def test_post_check_worker_round_trip_refuses_complete_oversized_request(tmp_path, monkeypatch):
    import hashlib, json, os, sys
    from mishe_tauftauf import post_check
    cli=tmp_path/'codex';cli.write_text('fixture cli')
    probe=tmp_path/'probe.json'
    probe.write_text(json.dumps({'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'returncode':0,'sentinel_unchanged':True,'stderr':'code-mode host is disabled','command':codex_review.command(tmp_path/'schema',tmp_path,tmp_path/'instructions',str(cli))}))
    config=tmp_path/'config.toml';config.write_text('model = "fixture"')
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    argv=[sys.executable,'-m','mishe_tauftauf.codex_review','--cli',str(cli),'--runtime-config',str(config),'--probe-report',str(probe)]
    (tmp_path/'publication-check.json').write_text(json.dumps({'command':argv,'timeout_seconds':10}))
    report=post_check.review(tmp_path,'mind','ordinary review draft '+('x'*121000))
    assert report['semantic_status']=='unknown'
    assert report['status']=='unknown' and report['clear'] is False
    assert report['input_hash']
    assert {row['id'] for row in report['results']}=={q['id'] for q in post_check.questions('post')}
    assert all(row['verdict']=='unknown' for row in report['results'])
    assert not report['untested_questions']
    assert 'no truncation permitted' in report['results'][0]['reason']


def test_disabled_host_notice_is_exactly_once_and_pre_turn(tmp_path):
    import json
    cli,probe=fake_probe(tmp_path)
    notice=json.dumps({'type':'item.completed','item':{'type':'error','message':codex_review.INITIALIZATION_NOTICE}}).encode()+b'\n'
    outputs=[
        (notice+clear_output(request()),'clear'),
        (notice+notice+clear_output(request()),'unknown'),
        (b'{"type":"turn.started"}\n'+notice+clear_output(request()),'unknown'),
    ]
    for output,expected in outputs:
        with patch.object(codex_review,'_invoke',return_value=(output,b'')):
            assert codex_review.review(request(),probe_path=probe,cli=str(cli))['results'][0]['verdict']==expected



def test_parent_timeout_terminates_nested_cli_group(tmp_path):
    import os,sys,time,subprocess
    from mishe_tauftauf.post_check import _worker
    pidfile=tmp_path/'nested.pid'
    parent=tmp_path/'adapter.py'
    child="import os,time; from pathlib import Path; Path("+repr(str(pidfile))+").write_text(str(os.getpid())); time.sleep(30)"
    parent.write_text("from mishe_tauftauf.codex_review import _invoke\n_invoke("+repr([sys.executable,'-c',child])+", b'', 20)\n")
    with pytest.raises(subprocess.TimeoutExpired):
        _worker([sys.executable,str(parent)],b'',.3)
    assert pidfile.exists()
    pid=int(pidfile.read_text())
    # The probe loop runs after the group kill, so the task can be reaped between
    # iterations. A missing /proc entry raises FileNotFoundError, but a live-but-dying
    # entry raises ProcessLookupError instead: both mean the grandchild is gone and the
    # deadline was enforced.
    for _ in range(50):
        status=__import__('pathlib').Path('/proc')/str(pid)/'status'
        try:
            child_status = status.read_text()
        except (FileNotFoundError, ProcessLookupError):
            break  # The reaped child may disappear between probes.
        if 'State:\tZ' in child_status:
            break
        time.sleep(.01)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        pytest.fail('nested CLI survived parent deadline')


def test_oversized_stdin_returns_question_complete_refusal(monkeypatch,capsys):
    import io,sys,json
    from pathlib import Path
    req=request();req['body']='x'*(codex_review.MAX_INPUT_BYTES+1)
    monkeypatch.setattr(sys,'argv',['codex_review','--probe-report','unused','--cli','/usr/bin/codex','--runtime-config',str(Path.home()/'.codex/config.toml')])
    monkeypatch.setattr(sys,'stdin',type('Input',(),{'buffer':io.BytesIO(json.dumps(req).encode())})())
    codex_review.main()
    result=json.loads(capsys.readouterr().out)
    assert result['version']==1 and result['input_hash']==req['input_hash']
    assert result['results']==[{'id':'R06','verdict':'unknown','reason':'complete request exceeds checker input budget; no truncation permitted','evidence':[]}]
