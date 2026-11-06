import subprocess
import sys
from pathlib import Path
import pytest
from mishe_tauftauf import release_promotion as p
from mishe_tauftauf.state_files import read_json


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def tree(tmp_path):
    repo=tmp_path / "repo"; repo.mkdir(); home=tmp_path / "home"; home.mkdir()
    git(repo, "init", "-q"); git(repo, "config", "user.name", "test"); git(repo, "config", "user.email", "t@example.org")
    (repo / "docs").mkdir(); (repo / "docs/page.md").write_text("old")
    git(repo,"add",".");git(repo,"commit","-qm","old"); old=git(repo,"rev-parse","HEAD")
    (repo / "docs/page.md").write_text("new");git(repo,"add",".");git(repo,"commit","-qm","new")
    return repo,home,old,git(repo,"rev-parse","HEAD")


def test_failed_probe_restores_old_consumer_and_records_events(tree):
    repo,home,old,sha=tree; pins=[old]; probes=[]
    def activate(source, target): pins[0]=target
    def probe(argv, **kw):
        probes.append(pins[0]); return dict(code=int(pins[0]==sha), output=pins[0])
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert 2+2==4"],observe=["probe"],
                previous=old,activate=activate,probe=probe,reviewer=lambda *a: None,closure=lambda *a: None)
    assert pins[0]==old
    assert probes==[sha,old]
    assert r["phase"]=="rolled-back"
    assert not r.get("delivery_verified",False)


def test_failed_restore_is_unknown(tree):
    repo,home,old,sha=tree
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a: None,probe=lambda *a,**k:dict(code=1,output="bad"),
        reviewer=lambda *a:None,closure=lambda *a:None)
    assert r["phase"]=="UNKNOWN"
    assert r["rollback_probe"]["code"]==1


def test_ordinary_reviewer_outage_does_not_block(tree):
    repo,home,old,sha=tree
    def unavailable(*a): raise ValueError("offline")
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a:None,probe=lambda *a,**k:dict(code=0,output="ok"),
        reviewer=unavailable,closure=lambda *a:None)
    assert r["phase"]=="delivered" and r["review_pending"]
    assert r["tests_passed"] and r["observed"]


def test_high_impact_cannot_use_ordinary_path(tree):
    repo,home,old,sha=tree
    target=repo/"src/mishe_tauftauf/owed.py";target.parent.mkdir(parents=True);target.write_text("pass")
    git(repo,"add",".");git(repo,"commit","-qm","scheduler")
    with pytest.raises(ValueError, match="strong"):
        p.promote(home,repo,git(repo,"rev-parse","HEAD"),check=["check"],observe=["probe"],previous=old)


def test_dirty_untracked_module_cannot_enter_release(tree):
    repo,home,old,sha=tree
    (repo/"untracked.py").write_text("secret=1")
    source=p.materialize(home,repo,sha)
    assert not (source/"untracked.py").exists()
    assert (source/"docs/page.md").read_text()=="new"


def test_release_mutation_during_check_cannot_promote(tree):
    repo,home,old,sha=tree
    with pytest.raises(ValueError,match="source changed"):
        p.promote(home,repo,sha,check=[sys.executable,"-c","from pathlib import Path; Path(\"docs/page.md\").write_text(\"mutated\")"],
          observe=["probe"], previous=old,activate=lambda *a:None,closure=lambda *a:None)


def test_running_wake_retains_pin(tree):
    repo,home,old,sha=tree
    with p.delivery_lock(home):
        with pytest.raises(ValueError,match="dispatch lock"):
            p.promote(home,repo,sha,check=["check"],observe=["probe"],previous=old)


def test_default_review_enqueue_uses_sha_and_retains_pending(tree, monkeypatch):
    repo,home,old,sha=tree
    calls=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kwargs:calls.append(argv))
    p.enqueue_review(home,dict(sha=sha,id="release",source=str(repo)))
    assert sha[:12] in calls[0][3]
    assert f"--setenv=PYTHONPATH={repo / 'src'}" in calls[0]
    assert f"--setenv=MISHE_SEED_HOME={home}" in calls[0]
    assert "--slice=mishe-plants.slice" in calls[0]
    assert read_json(home/"work/reviews"/f"{sha}.json")["state"]=="pending"


def test_oneshot_observation_uses_retained_start_stamp_not_empty_invocation_id(monkeypatch):
    from mishe_tauftauf import work_boundary as b
    stamp=["10"]
    def output(argv,**kwargs):
        values={"ActiveState":"inactive", "Environment":"PYTHONPATH=/release/src", "InvocationID":"",
                "ExecMainStartTimestampMonotonic":stamp[0], "ExecMainStatus":"0", "Result":"success"}
        return values[argv[-2]]+"\n"
    monkeypatch.setattr(b.subprocess,"check_output",output)
    monkeypatch.setattr(b.subprocess,"run",lambda *a,**k:stamp.__setitem__(0,"20"))
    assert b.invoke_arm_consumer(Path("/release/src"))["started_monotonic"]=="20"


def test_partial_default_activation_removes_new_config_files(tree,monkeypatch):
    repo,home,old,sha=tree
    oldfile=home/"old.service";oldfile.write_text("old")
    newfile=home/"new.service"
    monkeypatch.setattr(p,"config_paths",lambda h:[x for x in (oldfile,newfile) if x.exists()])
    from mishe_tauftauf import runtime_cli
    monkeypatch.setattr(runtime_cli,"default_pythonpath",lambda:str(home/"releases"/old/"src"))
    monkeypatch.setattr(p.boundary,"invoke_arm_consumer",lambda *a:dict(result="success"))
    def broken(*args):
        oldfile.write_text("new");newfile.write_text("created");raise ValueError("partial activation")
    monkeypatch.setattr(p,"activate",broken)
    monkeypatch.setattr(p,"reload_units",lambda:None)
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],previous=old,
        probe=lambda *a,**k:dict(code=0,output="old consumer"),closure=lambda *a:None)
    assert r["phase"]=="rolled-back"
    assert oldfile.read_text()=="old"
    assert not newfile.exists()


def test_async_review_cannot_overwrite_completed_revert(tree,monkeypatch):
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    identity="review-race"
    from mishe_tauftauf.state_files import write_json
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",source=str(source),
        paths=["docs/page.md"],check=dict(output="passed",code=0),config_before={},config_after={},observe_argv=["probe"],owner="docs",review_pending=True))
    monkeypatch.setattr(p,"config_paths",lambda *a:[])
    monkeypatch.setattr(p,"reload_units",lambda:None)
    monkeypatch.setattr(p.boundary,"invoke_arm_consumer",lambda *a:dict(result="success"))
    monkeypatch.setattr(p,"run",lambda *a,**k:dict(code=0,output="restored"))
    def reviewer(*args):
        assert p.revert(home,identity)["phase"]=="reverted"
        return dict(clear=True,response={})
    monkeypatch.setattr(p.wall_patch,"review",reviewer)
    p.review(home,identity)
    final=read_json(p.record_path(home,identity))
    assert final["phase"]=="reverted"
    assert final["revert_probe"]["output"]=="restored"
    assert final["review"]["clear"] is True


def test_revert_rereads_review_completed_before_lock_acquisition(tree,monkeypatch):
    from contextlib import contextmanager
    from mishe_tauftauf.state_files import write_json
    repo,home,old,sha=tree;p.materialize(home,repo,old)
    identity="inverse-race";path=p.record_path(home,identity)
    write_json(path,dict(id=identity,sha=sha,previous=old,phase="delivered",config_before={},config_after={},observe_argv=["probe"],review_pending=True))
    @contextmanager
    def lock(*args):
        current=read_json(path);current.update(review_pending=False,review=dict(clear=True));write_json(path,current)
        yield
    monkeypatch.setattr(p,"delivery_lock",lock)
    monkeypatch.setattr(p,"config_paths",lambda *a:[]);monkeypatch.setattr(p,"reload_units",lambda:None)
    monkeypatch.setattr(p.boundary,"invoke_arm_consumer",lambda *a:dict(result="success"))
    monkeypatch.setattr(p,"run",lambda *a,**k:dict(code=0,output="restored"))
    r=p.revert(home,identity)
    assert r["phase"]=="reverted" and r["review_pending"] is False and r["review"]["clear"]


def test_explicit_retry_after_reviewer_error_preserves_failed_attempt(tree,monkeypatch):
    from mishe_tauftauf.state_files import write_json
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    identity="review-retry"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",source=str(source),paths=["docs/page.md"],check=dict(output="pass"),review_pending=True))
    calls=[]
    def reviewer(*args):
        calls.append(1)
        if len(calls)==1: raise ValueError("temporary outage")
        return dict(clear=True)
    monkeypatch.setattr(p.wall_patch,"review",reviewer)
    assert p.review(home,identity)["phase"]=="review-pending"
    assert p.review(home,identity)["phase"]=="reviewed"
    assert len(calls)==2
    assert list((home/"work/reviews").glob("*.failed-attempt.json"))


def test_config_inventory_excludes_host_guards_and_other_sites(tmp_path):
    from mishe_tauftauf.release_boundary import owned_runtime_units
    from mishe_tauftauf.units import MARKER
    h=tmp_path/"owned";unitdir=tmp_path/"units";unitdir.mkdir()
    (unitdir/"mishe-host-guard.service").write_text("protected host service")
    (unitdir/"mishe-other.service").write_text(MARKER+"\nEnvironment=MISHE_SEED_HOME=/other/plant\n")
    (unitdir/"mishe-owned.service").write_text(MARKER+f"\nEnvironment=MISHE_SEED_HOME={h}\n")
    (unitdir/"mishe-owned.timer").write_text(MARKER+"\nUnit=mishe-owned.service\n")
    assert {p.name for p in owned_runtime_units(h,unitdir)}=={"mishe-owned.service","mishe-owned.timer"}


@pytest.mark.parametrize("prior_phase", ["reverted", "reverting", "UNKNOWN"])
def test_reapply_same_sha_reuses_independent_review_for_new_delivery(tree,monkeypatch,prior_phase):
    from mishe_tauftauf.state_files import write_json
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    for identity,phase in (("first",prior_phase),("second","delivered")):
        write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase=phase,source=str(source),
            paths=["docs/page.md"],check=dict(output="pass"),observed=True,review_pending=True))
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    p.enqueue_review(home,read_json(p.record_path(home,"first")))
    p.enqueue_review(home,read_json(p.record_path(home,"second")))
    calls=[]
    monkeypatch.setattr(p.wall_patch,"review",lambda *a:calls.append(1) or dict(clear=True))
    p.review(home,"first")
    p.merge_reviews(home)
    assert len(launched)==1 and len(calls)==1
    assert read_json(p.record_path(home,"second"))["review_pending"] is False
    assert read_json(p.record_path(home,"first"))["phase"]==prior_phase


def test_activation_starts_new_owned_timer_without_enabling_old_triggers(tree,monkeypatch):
    from mishe_tauftauf import runtime_cli
    repo,home,old,sha=tree
    (home/"bin").mkdir()
    existing=home/"mishe-existing.timer";existing.write_text("existing")
    new=home/"mishe-new.timer"
    monkeypatch.setattr(p,"config_paths",lambda h:[x for x in (existing,new) if x.exists()])
    monkeypatch.setattr(runtime_cli,"run_units",lambda *a:new.write_text("new"))
    calls=[];real_run=p.subprocess.run
    def record_systemctl(argv,**kw):
        if argv[0]=="git": return real_run(argv,**kw)      # the pin mirror still reads git HEAD
        calls.append(argv)
    monkeypatch.setattr(p.subprocess,"run",record_systemctl)
    p.activate(home,repo,repo,sha)
    assert ["systemctl","--user","enable","--now","mishe-new.timer"] in calls
    assert not any("mishe-existing.timer" in call for call in calls)


def test_review_binding_rejects_different_committed_release_tree(tree):
    repo,home,old,sha=tree
    new=p.materialize(home,repo,sha);before=p.materialize(home,repo,old)
    reviewed=dict(files={"docs/page.md":dict(after=p.wall_patch.snapshot(new/"docs/page.md"))})
    p.bind_reviewed_source(new,reviewed)
    with pytest.raises(ValueError,match="reviewed bytes"):
        p.bind_reviewed_source(before,reviewed)


def test_generated_resolver_configuration_is_restored(tree,monkeypatch):
    from mishe_tauftauf.state_files import write_json
    repo,home,old,sha=tree
    resolver=home/"resolvers.generated.json";write_json(resolver,{"old.service":"health"})
    before={str(path):p.wall_patch.snapshot(path) for path in p.config_paths(home)}
    write_json(resolver,{"new.service":"genome"})
    monkeypatch.setattr(p,"reload_units",lambda:None)
    p.restore_configuration(home,before)
    assert read_json(resolver)=={"old.service":"health"}


def test_revert_refuses_later_same_site_configuration_without_deleting_it(tree,monkeypatch):
    from mishe_tauftauf.state_files import write_json
    repo,home,old,sha=tree
    original=home/"original.service";original.write_text("delivered")
    later=home/"later.service";later.write_text("peer work")
    timer=home/"later.timer";timer.write_text("peer timer")
    identity="later-peer"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        config_after={str(original):p.wall_patch.snapshot(original)},config_before={},observe_argv=["probe"]))
    monkeypatch.setattr(p,"config_paths",lambda *a:[original,later,timer])
    with pytest.raises(ValueError,match="configuration inventory changed"):
        p.revert(home,identity)
    assert later.read_text()=="peer work" and timer.read_text()=="peer timer" and original.read_text()=="delivered"


def test_failed_explicit_revert_remains_unknown_in_checkpoint_metrics(tree,monkeypatch):
    from mishe_tauftauf.state_files import write_json
    from mishe_tauftauf import work_metrics
    repo,home,old,sha=tree;p.materialize(home,repo,old)
    identity="failed-revert"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,created_at=1,delivered_at=10,
        phase="delivered",observed=True,config_before={},config_after={},observe_argv=["probe"]))
    monkeypatch.setattr(p,"config_paths",lambda *a:[]);monkeypatch.setattr(p,"reload_units",lambda:None)
    monkeypatch.setattr(p.boundary,"invoke_arm_consumer",lambda *a:dict(result="success"))
    monkeypatch.setattr(p,"run",lambda *a,**kw:dict(code=1,output="restored probe failed"))
    monkeypatch.setattr(p.time,"time",lambda:100)
    assert p.revert(home,identity)["phase"]=="UNKNOWN"
    write_json(home/"work/experiment/manifest.json",dict(version=1,start=0,end=86400,recovery_events_version=1))
    facts=work_metrics.report(home,start=0,end=200)["reverts_rework"]
    assert facts["explicit_reverts"]==0 and facts["failed_recoveries"]==1 and facts["coverage"]=="UNKNOWN"


def test_review_binding_uses_git_executable_mode_with_mixed_checkout_permissions(tree):
    repo,home,old,sha=tree
    page=repo/"docs/page.md"; page.chmod(0o600)
    reviewed=dict(files={"docs/page.md":dict(after=p.wall_patch.snapshot(page))})
    source=p.materialize(home,repo,sha)
    (source/"docs/page.md").chmod(0o664)
    p.bind_reviewed_source(source,reviewed)
    (source/"docs/page.md").chmod(0o775)
    with pytest.raises(ValueError,match="reviewed bytes"):
        p.bind_reviewed_source(source,reviewed)


def _release(path, text):
    path.mkdir()
    git(path, "init", "-q"); git(path, "config", "user.name", "test"); git(path, "config", "user.email", "t@example.org")
    (path/"f.txt").write_text(text); git(path, "add", "."); git(path, "commit", "-qm", text)
    return git(path, "rev-parse", "HEAD")


def test_point_flips_the_stable_runtime_path_atomically(tmp_path):
    a=tmp_path/"rel-a";b=tmp_path/"rel-b";home=tmp_path/"home";home.mkdir()
    sha_a=_release(a,"a"); sha_b=_release(b,"b")
    p.point(home,a)
    assert p.current_link(home).resolve()==a.resolve()
    p.point(home,b)                                   # replaces the existing link
    assert p.current_link(home).resolve()==b.resolve()
    assert p.current_link(home).is_symlink()
    assert not [x for x in (home/"runtime").iterdir() if x.name!="current"]   # no temp link left behind


def test_point_mirrors_the_pin_record_to_the_pointed_release(tmp_path):
    a=tmp_path/"rel-a";b=tmp_path/"rel-b";home=tmp_path/"home";home.mkdir()
    sha_a=_release(a,"a"); sha_b=_release(b,"b")
    p.point(home,a)
    record=read_json(home/"health"/"runtime-release.json")
    assert record["sha"]==sha_a and record["source"]==str(a.resolve()) and record["version"]==1
    p.point(home,b)                                   # advance and rollback are the same move
    record=read_json(home/"health"/"runtime-release.json")
    assert record["sha"]==sha_b and record["source"]==str(b.resolve())
    p.point(home,a)
    assert read_json(home/"health"/"runtime-release.json")["sha"]==sha_a


def test_default_activation_generates_units_and_wrapper_on_the_stable_path_not_a_sha(tree,monkeypatch):
    # 2026-11-05: five consumers carried four different pinned shas, doctor logged drift 114 times, and moving the pin
    # was a multi-hour reviewed ritual. Units and the wrapper now name runtime/current only.
    from mishe_tauftauf import runtime_cli
    repo,home,old,sha=tree
    (home/"bin").mkdir()
    seen=[]
    monkeypatch.setattr(p,"config_paths",lambda h:[])
    monkeypatch.setattr(runtime_cli,"run_units",lambda args,*a:seen.append(args.pythonpath))
    p.activate(home,repo,repo,sha)
    stable=str(p.current_link(home)/"src")
    assert seen==[stable]
    for name in ("mishe","mishe-tauftauf"):                                   # the doctor checks bin/mishe-tauftauf too
        wrapper=(home/"bin"/name).read_text()
        assert str(p.current_link(home)) in wrapper and sha not in wrapper and sha not in seen[0]
        assert (home/"bin"/name).stat().st_mode & 0o111
    assert p.current_link(home).resolve()==repo.resolve()


def test_failed_default_activation_moves_the_pointer_back(tree,monkeypatch):
    repo,home,old,sha=tree
    oldsrc=home/"releases"/old;oldsrc.parent.mkdir(parents=True)
    _release(oldsrc,"old")                            # a clean release the pointer can name
    p.point(home,oldsrc)
    monkeypatch.setattr(p,"config_paths",lambda h:[])
    from mishe_tauftauf import runtime_cli
    monkeypatch.setattr(runtime_cli,"default_pythonpath",lambda:str(home/"releases"/old/"src"))
    monkeypatch.setattr(p.boundary,"invoke_arm_consumer",lambda *a:dict(result="success"))
    monkeypatch.setattr(p,"materialize",lambda h,r,s:(home/"releases"/s) if s==old else repo)
    monkeypatch.setattr(p,"reload_units",lambda:None)
    def flip_then_fail(h,r,source,s):
        p.point(h,source); raise ValueError("activation failed after the pointer moved")
    monkeypatch.setattr(p,"activate",flip_then_fail)
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],previous=old,
        probe=lambda *a,**k:dict(code=0,output="ok"),closure=lambda *a:None)
    assert r["phase"]=="rolled-back"
    assert p.current_link(home).resolve()==oldsrc.resolve()


def test_arm_consumer_probe_accepts_a_unit_that_names_the_stable_pointer_to_the_release(tmp_path,monkeypatch):
    from mishe_tauftauf import work_boundary as b
    release=tmp_path/"releases"/"abc"/"src";release.mkdir(parents=True)
    (tmp_path/"runtime").mkdir();(tmp_path/"runtime"/"current").symlink_to(tmp_path/"releases"/"abc")
    stamp=["10"]
    def output(argv,**kwargs):
        values={"ActiveState":"inactive","Environment":f"PYTHONPATH={tmp_path}/runtime/current/src MISHE_X=1","InvocationID":"",
                "ExecMainStartTimestampMonotonic":stamp[0],"ExecMainStatus":"0","Result":"success"}
        return values[argv[-2]]+"\n"
    monkeypatch.setattr(b.subprocess,"check_output",output)
    monkeypatch.setattr(b.subprocess,"run",lambda *a,**k:stamp.__setitem__(0,"20"))
    assert b.invoke_arm_consumer(release)["result"]=="success"           # unit names the pointer, caller names the release
    stamp[0]="10"
    with pytest.raises(ValueError):
        b.invoke_arm_consumer(tmp_path/"releases"/"other"/"src")          # a different release is still refused


def test_revert_keeps_operator_wrapper_missing_from_older_snapshot(tree,monkeypatch):
    # 0d210604 revert (23:45Z): the pre-activation snapshot predates bin/mishe-tauftauf being tracked,
    # so restore_configuration treated the wrapper as new and deleted it, breaking the operator CLI.
    repo,home,old,sha=tree
    wrapper=home/"bin"/"mishe-tauftauf";wrapper.parent.mkdir(parents=True,exist_ok=True)
    wrapper.write_text("#!/bin/sh\nexec stable\n")
    monkeypatch.setattr(p,"reload_units",lambda:None)
    p.restore_configuration(home,{})
    assert wrapper.read_text()=="#!/bin/sh\nexec stable\n"
