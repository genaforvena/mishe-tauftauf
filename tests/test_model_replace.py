"""Model changes preserve durable evidence and distinguish selection from running."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from mishe_tauftauf import cli, seed


def service():
    from mishe_tauftauf import model_change
    return model_change


@pytest.fixture
def site(tmp_path, monkeypatch):
    home = tmp_path / 'site'
    cli.initialize(home)
    for role in ('witness', 'senses'):
        (home / 'charters').mkdir(exist_ok=True)
        (home / 'charters' / (role + '.md')).write_text(role)
        p = home / 'minds' / role
        p.write_text('#!/bin/sh\nexport PI_CODING_AGENT_DIR=/tmp/store\nexec /usr/bin/omp --model=old/Model --thinking xhigh --cwd /tmp\n')
        p.chmod(0o755)
    return home


def configure(monkeypatch):
    m = service()
    monkeypatch.setattr(m.boundary, 'preflight', lambda config, target: {'thinking': ['low', 'medium', 'high'], 'probe': 'ok'})
    monkeypatch.setattr(m.boundary, 'probe', lambda *a: {'result': 'ok'})
    monkeypatch.setattr(m.boundary, 'observe', lambda *a: {'state': 'unknown', 'reason': 'no pane'})
    return m


def test_launcher_preserves_every_other_word_and_environment():
    m = service()
    text = '#!/bin/sh\n# old/Model comment\nexport PI_CODING_AGENT_DIR=/tmp/store\nexec /usr/bin/omp --model=old/Model --thinking xhigh --config /tmp/overlay --cwd /tmp\n'
    config = m.boundary.launcher(text)
    changed = m.boundary.rewrite(config, 'new/CaseSensitive', 'high')
    parsed = m.boundary.launcher(changed)
    assert parsed.model == 'new/CaseSensitive'
    assert parsed.environment == config.environment
    assert parsed.argv == [*config.argv[:1], '--model=new/CaseSensitive', '--thinking', 'high', *config.argv[4:]]
    assert 'old/Model comment' in changed


@pytest.mark.parametrize('text', [
    '#!/bin/sh\nexec omp --model old/A; touch /tmp/bad\n',
    '#!/bin/sh\nexec omp --model "$MODEL"\n',
    '#!/bin/sh\nexec omp --model old/A --model old/B\n',
    '#!/bin/sh\npython arbitrary-wrapper.py\n',
])
def test_ambiguous_launchers_are_unknown(text):
    with pytest.raises(ValueError):
        service().boundary.launcher(text)


def test_dry_run_is_read_only_and_effort_mapping_is_explicit(site, monkeypatch):
    m = configure(monkeypatch)
    before = {p: p.read_bytes() for p in site.rglob('*') if p.is_file()}
    result = m.replace(site, 'old/Model', 'new/Model', effort='compatible', dry_run=True)
    assert result['roles']['witness']['effort'] == 'high'
    assert result['state'] == 'preview'
    assert before == {p: p.read_bytes() for p in site.rglob('*') if p.is_file()}


def test_default_effort_mismatch_changes_nothing(site, monkeypatch):
    m = configure(monkeypatch)
    before = (site / 'minds/witness').read_bytes()
    with pytest.raises(ValueError, match='effort'):
        m.replace(site, 'old/Model', 'new/Model')
    assert (site / 'minds/witness').read_bytes() == before
    assert not (site / 'model-changes').exists()


def test_receipt_revert_and_conflict_preserve_peer_work(site, monkeypatch):
    m = configure(monkeypatch)
    before = (site / 'minds/witness').read_bytes()
    result = m.replace(site, 'old/Model', 'new/Model', effort='compatible')
    assert result['state'] != 'verified'
    assert m.replace(site, 'old/Model', 'new/Model', effort='compatible')['id'] == result['id']
    p = site / 'minds/witness'
    p.write_text('# peer replacement\n')
    report = m.revert(site, result['id'])
    assert report['roles']['witness']['state'] == 'conflict'
    assert p.read_text() == '# peer replacement\n'
    assert (site / 'minds/senses').read_bytes() == before


def test_exact_match_does_not_select_other_provider_or_case(site, monkeypatch):
    m = configure(monkeypatch)
    with pytest.raises(ValueError, match='No registered'):
        m.replace(site, 'OLD/Model', 'new/Model', effort='compatible')


def test_abandonment_is_not_reported_as_success_and_cannot_remain_pending(site):
    from mishe_tauftauf.feed import Feed
    from mishe_tauftauf.chat_protocol import decode_lifecycle
    wake = Feed(site).append('seed', 'seed wake witness observation=1')
    event = Feed(site).append('seed', f'seed yield witness wake={wake.sequence} continue=1\nTurn settled (abandoned); transient context discarded by explicit model replacement. Prior effects remain UNKNOWN.')
    decoded = decode_lifecycle(event)
    assert decoded.kind == 'yield'
    assert decoded.result == 'abandoned'
    assert decoded.continue_work
    assert seed._state(site, 'witness')[1] is None


def test_corrupt_receipt_is_unknown_and_original_is_retained(site, monkeypatch):
    m = configure(monkeypatch)
    result = m.replace(site, 'old/Model', 'new/Model', effort='compatible')
    p = site / 'model-changes' / result['id'] / 'receipt.json'
    p.write_text('{broken')
    report = m.status(site, result['id'])
    assert report['state'] == 'unknown'
    assert p.read_text() == '{broken'


def test_overdue_pending_change_keeps_monitor_red(site, monkeypatch):
    m = configure(monkeypatch)
    result = m.replace(site, 'old/Model', 'new/Model', effort='compatible')
    monkeypatch.setattr(m, 'now', lambda: result['created'] + 121)
    assert m.watch_once(site)['state'] == 'unknown'
    assert m.check_watch(site)['state'] == 'unknown'
    from mishe_tauftauf.feed import Feed
    assert any(entry.body.startswith('[dm] to=health\nModel change ') for entry in Feed(site).entries())


@pytest.mark.parametrize('content', [None, '{}', '{bad', '{"state":"ok","checked":0,"reports":[]}'])
def test_missing_broken_stale_monitor_is_unknown(site, content):
    p = site / 'checks/model-changes.json'
    p.parent.mkdir(exist_ok=True)
    if content is not None:
        p.write_text(content)
    assert service().check_watch(site)['state'] == 'unknown'


def test_observation_does_not_accept_preflight_or_another_process(site, monkeypatch):
    m = service()
    b = m.boundary
    monkeypatch.setattr(b, 'process', lambda *a: ('owned', {'pid':1,'start':'2','started':1,'model':'new/Model','marker':'change'}))
    directory = site / 'evidence'
    (directory / 'sessions').mkdir(parents=True)
    assert b.observe(site, 'witness', 'change', 'new/Model', directory)['state'] == 'activated'
    p = directory / 'sessions/one.jsonl'
    p.write_text(json.dumps({'type':'message','message':{'role':'assistant','provider':'wrong','model':'Model','stopReason':'stop'}})+'\n')
    assert b.observe(site, 'witness', 'change', 'new/Model', directory)['state'] == 'activated'
    p.write_text(json.dumps({'type':'message','message':{'role':'assistant','provider':'new','model':'Model','stopReason':'error','timestamp':2000}})+'\n')
    assert b.observe(site, 'witness', 'change', 'new/Model', directory)['state'] == 'failed'
    p.write_text(json.dumps({'type':'message','message':{'role':'assistant','provider':'new','model':'Model','stopReason':'stop','timestamp':2000}})+'\n')
    assert b.observe(site, 'witness', 'change', 'new/Model', directory)['state'] == 'verified'
    monkeypatch.setattr(b, 'process', lambda *a: ('owned', {'pid':2,'start':'3','started':3,'model':'new/Model','marker':'change'}))
    assert b.observe(site, 'witness', 'change', 'new/Model', directory)['state'] == 'activated'
    p.write_text('[]\n')
    assert b.observe(site, 'witness', 'change', 'new/Model', directory)['state'] == 'unknown'
    for stamp in (float('nan'), float('inf'), True, (time.time() + 60) * 1000):
        p.write_text(json.dumps({'type':'message','message':{'role':'assistant','provider':'new','model':'Model','stopReason':'stop','timestamp':stamp}})+'\n')
        assert b.observe(site, 'witness', 'change', 'new/Model', directory)['state'] == 'unknown'


def test_revert_does_not_claim_running_rollback_on_config_write_only(site, monkeypatch):
    m = configure(monkeypatch)
    result = m.replace(site, 'old/Model', 'new/Model', effort='compatible')
    report = m.revert(site, result['id'])
    assert report['state'] != 'reverted'
    assert all(r['state'] != 'reverted' for r in report['roles'].values())


def test_real_tmux_fresh_start_provider_observation_and_revert(tmp_path, monkeypatch):
    if not shutil.which('tmux'):
        pytest.skip('tmux required')
    m = service()
    home = tmp_path / 'site'
    cli.initialize(home)
    binary = tmp_path / 'omp'
    binary.write_text('''#!/usr/bin/python3
import sys, json, time, os
from pathlib import Path
a=sys.argv[1:]
def opt(k):
 for i,v in enumerate(a):
  if v==k:return a[i+1]
  if v.startswith(k+'='):return v[len(k)+1:]
if a and a[0]=='models':
 print(json.dumps({'models':[{'provider':'new','id':'Model','thinking':['high']},{'provider':'old','id':'Model','thinking':['high']}]}));sys.exit()
provider, model=opt('--model').split('/',1)
msg={'role':'assistant','provider':provider,'model':model,'stopReason':'stop','timestamp':int(time.time()*1000),'content':[{'type':'text','text':'MISHE_MODEL_OK'}]}
if '--print' in a:
 print(json.dumps({'type':'message_end','message':msg}));sys.exit()
directory=opt('--session-dir')
if directory:
 p=Path(directory);p.mkdir(parents=True,exist_ok=True)
 (p/(str(os.getpid())+'.jsonl')).write_text(json.dumps({'type':'message','message':msg})+'\\n')
while True: time.sleep(1)
''')
    binary.chmod(0o755)
    mind = home / 'minds/witness'
    mind.write_text(f'#!/bin/sh\nexec {binary} --model old/Model --thinking high --cwd {tmp_path}\n')
    mind.chmod(0o755)
    socket = str(tmp_path / 'tmux.sock')
    session = 'model-fixture'
    def tmux(*args, check=True, socket=socket, **kwargs):
        return subprocess.run(['tmux','-S',socket,*args],capture_output=True,check=check)
    tmux('new-session','-d','-s',session,'-n','witness','sleep','600')
    try:
        tmux('set-option','-t',session,'@mishe-tauftauf-home',str(home))
        tmux('split-window','-t',session,'-v',str(mind))
        (home / '.seed-raised').write_text(session+' $0\n')
        monkeypatch.setattr(seed,'_tmux',tmux)
        monkeypatch.setattr(m.boundary,'owned_socket',lambda *a:socket)
        from mishe_tauftauf import tmux as module
        monkeypatch.setattr(module,'owned_socket',lambda *a:socket)
        monkeypatch.setattr(seed,'_mind_launch_argv',lambda h,r:('-c',str(tmp_path),'env',str(h/'minds'/r)))
        from mishe_tauftauf.feed import Feed
        wake=Feed(home).append('seed','seed wake witness observation=1')
        result=m.replace(home,'old/Model','new/Model',fresh_start=True)
        for _ in range(30):
            result=m.status(home,result['id'])
            if result['state']=='verified': break
            time.sleep(.1)
        assert result['state']=='verified',result
        assert seed._state(home,'witness')[1] is None
        assert result['roles']['witness']['abandoned_wake']==wake.sequence
        assert (home/'model-changes'/result['id']/'witness/prior-pane.txt').exists()
        # Repeated advance cannot kill/restart this generation.
        oldpid=result['roles']['witness']['process']['pid']
        assert m.advance(home,result['id'])['roles']['witness']['process']['pid']==oldpid
        tmux('respawn-pane','-k','-t',session+':witness.1',str(mind))
        for _ in range(30):
            state=m.status(home,result['id'])
            if state['state']=='delivered':break
            time.sleep(.1)
        assert state['state']=='delivered',state
        rotated_pid=state['roles']['witness']['process']['pid']
        assert state['roles']['witness']['current_provider']=='unknown'
        assert m.advance(home,result['id'])['roles']['witness']['process']['pid']==rotated_pid
        reverse=m.revert(home,result['id'])
        for _ in range(30):
            reverse=m.status(home,result['id'])
            if reverse['state']=='reverted':break
            time.sleep(.1)
        assert reverse['state']=='reverted',reverse
        assert reverse['roles']['witness']['process']['model']=='old/Model'
    finally:
        tmux('kill-server',check=False)


def test_incomplete_receipt_is_retained_but_does_not_block_new_work(site, monkeypatch):
    m=configure(monkeypatch)
    orphan=site/'model-changes'/('a'*32)
    orphan.mkdir(parents=True)
    result=m.replace(site,'old/Model','new/Model',effort='compatible')
    assert result['id'] != orphan.name
    assert orphan.exists()
    assert m.watch_once(site)['state']=='unknown'


def test_nonfinite_receipt_cannot_disable_overdue_detection(site, monkeypatch):
    m=configure(monkeypatch)
    result=m.replace(site,'old/Model','new/Model',effort='compatible')
    p=site/'model-changes'/result['id']/'receipt.json'
    data=json.loads(p.read_text());data['created']=float('nan');p.write_text(json.dumps(data))
    assert m.status(site,result['id'])['state']=='unknown'
