import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from mishe_tauftauf import release_promotion as p
from mishe_tauftauf.state_files import read_json, write_json


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


def test_rollback_probe_imports_the_new_probe_not_the_old_release(tree):
    # 7f10caa4 rolled back and its rollback probe failed with 'No module named mishe_tauftauf.pin_observation':
    # the probe ran with PYTHONPATH naming the old release, which does not carry the probe module.
    repo,home,old,sha=tree; pins=[old]; envs=[]
    def activate(source, target): pins[0]=target
    def probe(argv, **kw):
        envs.append(kw["env"]["PYTHONPATH"]); return dict(code=int(pins[0]==sha), output=pins[0])
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
                previous=old,activate=activate,probe=probe,reviewer=lambda *a: None,closure=lambda *a: None)
    assert r["phase"]=="rolled-back"
    assert envs==[str(Path(r["source"])/"src"), str(Path(r["source"])/"src")]


def test_failed_restore_is_unknown(tree):
    repo,home,old,sha=tree
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a: None,probe=lambda *a,**k:dict(code=1,output="bad"),
        reviewer=lambda *a:None,closure=lambda *a:None)
    assert r["phase"]=="UNKNOWN"
    assert r["rollback_probe"]["code"]==1


@pytest.mark.parametrize("path", ["README.md", "docs/page.md", "src/mishe_tauftauf/producers.py",
                                  "tests/test_release_promotion.py"])
def test_admitted_paths_are_ordinary(path):
    from mishe_tauftauf import work_boundary as b
    assert b.release_classification([path]) == "ordinary"


@pytest.mark.parametrize("path", ["AGENTS.md", "src/mishe_tauftauf/seed_agent_contract.md",
                                  "src/mishe_tauftauf/wake.py", "src/mishe_tauftauf/release_promotion.py",
                                  "duties.json", "unknown/new.py", "README.markdown", "docs/page.txt"])
def test_there_is_no_strong_path_every_change_is_ordinary(path):
    from mishe_tauftauf import work_boundary as b
    assert b.release_classification([path]) == "ordinary"
    assert b.release_classification(["README.md", path]) == "ordinary"
    assert b.release_classification([]) == "ordinary"


def test_every_admitted_path_is_exact_not_by_extension():
    from mishe_tauftauf import work_boundary as b
    assert "README.md" in b.ORDINARY_PATHS
    assert "README.txt" not in b.ORDINARY_PATHS
    assert "docs/README.md" not in b.ORDINARY_PATHS
    assert b.release_classification(["docs/README.md"]) == "ordinary"  # admitted by the docs/*.md rule


def test_ordinary_reviewer_outage_does_not_block(tree):
    repo,home,old,sha=tree
    def unavailable(*a): raise ValueError("offline")
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a:None,probe=lambda *a,**k:dict(code=0,output="ok"),
        reviewer=unavailable,closure=lambda *a:None)
    assert r["phase"]=="delivered" and r["review_pending"]
    assert r["tests_passed"] and r["observed"]


def test_readme_only_release_delivers_with_review_pending(tree):
    repo,home,old,sha=tree
    (repo/"README.md").write_text("reader front page")
    git(repo,"add",".");git(repo,"commit","-qm","readme")
    readme=git(repo,"rev-parse","HEAD")
    def unavailable(*a): raise ValueError("offline")
    r=p.promote(home,repo,readme,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a:None,probe=lambda *a,**k:dict(code=0,output="ok"),
        reviewer=unavailable,closure=lambda *a:None)
    assert r["phase"]=="delivered"
    assert r["classification"]=="ordinary"
    assert r["review_pending"]
    assert r["tests_passed"] and r["observed"]


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
            p.promote(home,repo,sha,check=[sys.executable,"-c","pass"],observe=["probe"],previous=old,
                      activate=lambda *a:None,closure=lambda *a:None)


def test_a_refused_delivery_admission_is_recorded_as_a_dead_run(tree):
    # 2026-11-18 06:13Z: the cadence promotion of 0148ab3e and an admitted delivery request
    # reached the dispatch lock in the same second. The refusal was a plain ValueError, so
    # `except DeliveryBusy` missed it and the record stayed "waiting-for-wakes" — GREEN in
    # checks/pin-delivery.py — for a process that had already exited.
    from mishe_tauftauf import delivery_requests
    repo,home,old,sha=tree
    write_json(delivery_requests.admission_path(home), dict(token="other-request", pid=os.getpid(), at=time.time()))
    with pytest.raises(delivery_requests.DeliveryOwned, match="admission is owned"):
        p.promote(home,repo,sha,check=[sys.executable,"-c","pass"],observe=["probe"],previous=old,
                  activate=lambda *a:None,closure=lambda *a:None)
    records=[read_json(path) for path in (home/"work/releases").glob("*.json")]
    assert [record["phase"] for record in records]==["delivery-busy"]
    assert "admission is owned" in records[0]["failure"]


def test_default_review_enqueue_uses_sha_and_retains_pending(tree, monkeypatch):
    repo,home,old,sha=tree
    calls=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kwargs:calls.append(argv))
    p.enqueue_review(home,dict(sha=sha,id="release",source=str(repo)))
    assert sha[:12] in calls[0][3]
    # The unit runs the pin's code and merges its verdict with it; the release
    # under review is only the patch data (see _review_pythonpath).
    assert f"--setenv=PYTHONPATH={home / 'runtime/current/src'}" in calls[0]
    assert f"--setenv=MISHE_SEED_HOME={home}" in calls[0]
    assert "--slice=mishe-plants.slice" in calls[0]
    assert read_json(home/"work/reviews"/f"{sha}.json")["state"]=="pending"


def test_review_retry_runs_the_pin_not_the_reviewed_tree(tree):
    # d4297d91's retry merged at 06:35 on 2026-11-18 under its own pre-fix tree,
    # after the pin had moved to the classification fix, and raised a fix/revert
    # obligation for an `unknown`-only review. A retry re-applies the live merge
    # policy, so it runs the pin's code and keeps the release's tree as data.
    repo,home,old,sha=tree
    reviewed=home/"releases"/old
    launched=[]
    p.schedule_review_retry(home,dict(id="old",sha=sha,previous=old,source=str(reviewed)),
                            due=time.time()+60,runner=lambda argv,**kw:launched.append(argv))
    assert f"--setenv=PYTHONPATH={home / 'runtime/current/src'}" in launched[0]
    assert str(reviewed) not in " ".join(launched[0])


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


def test_probe_failure_names_the_probes_own_line_in_the_signal_detail(tree):
    """A rollback's published detail must carry the cause, not just "functional probe failed".

    2026-11-17: the 0c567f9d detail read only "functional probe failed" while the record held
    "pin observation failed: cgroup enumeration missing: <unit>", so a subscriber could not tell a
    vanished consumer cgroup from a broken release without opening the record by hand.
    """
    repo,home,old,sha=tree; pins=[old]
    from mishe_tauftauf import signal_store
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])
    cause="pin observation failed: cgroup enumeration missing: /mishe-plants.slice/mishe-x.service"
    def activate(source, target): pins[0]=target
    def probe(argv, **kw):
        return dict(code=1, output=cause + "\n") if pins[0]==sha else dict(code=0, output="ok\n")
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],previous=old,
                activate=activate,probe=probe,reviewer=lambda *a:None,closure=lambda *a:None)
    assert r["phase"]=="rolled-back"
    assert r["failure"] == "functional probe failed: " + cause
    assert p.outcome_detail(r) == f"moving-pin-delivery: {sha[:8]} rolled-back: functional probe failed: {cause}"
    assert (home/"signals/moving-pin-delivery.detail").read_text().strip() == p.outcome_detail(r)


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


def test_ordinary_review_supplies_retained_recovery_evidence(tree,monkeypatch):
    # Measured cause: reviewers answered "unknown" for clean ordinary releases
    # (8ad57e3f, ac94997b) because the request asked for a pre-activation delivery
    # plan the ordinary path never carries. Supply the retained recovery evidence.
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    identity="recovery-evidence"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        source=str(source),paths=["docs/page.md"],check=dict(output="pass",code=0),
        observe_argv=["probe"],observe=dict(code=0,output="ok"),rollback_available=True,review_pending=True))
    seen={}
    def reviewer(home_arg,patch):
        seen["patch"]=patch;return dict(clear=True,response={})
    monkeypatch.setattr(p.wall_patch,"review",reviewer)
    monkeypatch.setattr(p,"config_paths",lambda *a:[])
    p.review(home,identity)
    recovery=seen["patch"]["recovery"]
    assert recovery["previous"]==old
    assert recovery["observation_argv"]==["probe"]
    assert recovery["observation_code"]==0
    assert recovery["rollback_available"] is True
    assert recovery["revert_argv"][-2:]==["--id",identity]
    assert "delivery_plan" not in seen["patch"]


def test_ordinary_review_carries_the_release_commit_intent(tree,monkeypatch):
    # Measured on ac94997b: the reviewer could not judge the intent behind a
    # comment the diff alone did not explain, because the rationale was the same
    # generic line for every ordinary release. Carry the window's own messages.
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    identity="intent"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        source=str(source),paths=["docs/page.md"],check=dict(output="pass",code=0),
        observe_argv=["probe"],observe=dict(code=0,output="ok"),review_pending=True))
    seen={}
    monkeypatch.setattr(p.wall_patch,"review",lambda h,patch:(seen.__setitem__("patch",patch) or dict(clear=True,response={})))
    monkeypatch.setattr(p,"config_paths",lambda *a:[])
    p.review(home,identity)
    assert "Author intent" in seen["patch"]["rationale"]
    assert "new" in seen["patch"]["rationale"]


def test_ordinary_review_supplies_a_file_the_changed_document_describes(tree,monkeypatch):
    # Measured on the docs releases 19fb7646 and c87cd884: docs/network-view.md
    # describes checks/network-care.py's RED/UNKNOWN rules, but review() supplied
    # only record["paths"], so the reviewer could not trace the documented arms to
    # code and answered "unknown". A changed file that names an existing repo file
    # must carry that file's bytes too.
    import base64
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    (source/"checks").mkdir(exist_ok=True)
    (source/"checks"/"care.py").write_text("UPLINK_RED_PER_HOUR = 400\n")
    (source/"docs"/"page.md").write_text("see `checks/care.py` for the rule\n")
    identity="described"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        source=str(source),paths=["docs/page.md"],check=dict(output="pass",code=0),
        observe_argv=["probe"],observe=dict(code=0,output="ok"),review_pending=True))
    seen={}
    monkeypatch.setattr(p.wall_patch,"review",lambda h,patch:(seen.__setitem__("patch",patch) or dict(clear=True,response={})))
    monkeypatch.setattr(p,"config_paths",lambda *a:[])
    p.review(home,identity)
    described=seen["patch"]["files"]["checks/care.py"]
    assert described["before"]==described["after"]
    assert "UPLINK_RED_PER_HOUR = 400" in base64.b64decode(described["after"]["bytes"]).decode()


def test_described_paths_cannot_escape_the_release_tree(tmp_path):
    # A changed page may spell a path token with embedded `..`. Resolving it
    # against the release tree must never pull a real file from OUTSIDE that
    # tree into the review: the reviewer is an external model, so those bytes
    # would leave the host, and the adapter promises repo-relative resolution.
    root = tmp_path
    source = root / "release"; source.mkdir()
    (source / "docs").mkdir(); (source / "checks").mkdir()
    (source / "checks" / "care.py").write_text("KEEP = 1\n")
    (root / "secret.json").write_text('{"secret": true}\n')
    (source / "docs" / "page.md").write_text(
        "see `checks/care.py` and `docs/../../secret.json` for the rule\n")
    assert p._described_paths(source, ["docs/page.md"], []) == ["checks/care.py"]


def test_ordinary_review_drops_a_described_file_that_is_not_utf8(tree,monkeypatch):
    # wall_patch.review reads every entry as UTF-8, so a described file with
    # non-UTF-8 bytes would raise UnicodeDecodeError there and lose the whole
    # review as a review_error. It must be dropped, not handed over, so the
    # review still runs and only the decodable file joins it.
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    (source/"checks").mkdir(exist_ok=True)
    (source/"checks"/"care.py").write_text("UPLINK_RED_PER_HOUR = 400\n")
    (source/"checks"/"blob.py").write_bytes(b"\xff\xfeKEEP = 1\n")
    (source/"docs"/"page.md").write_text("see `checks/care.py` and `checks/blob.py`\n")
    identity="described-binary"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        source=str(source),paths=["docs/page.md"],check=dict(output="pass",code=0),
        observe_argv=["probe"],observe=dict(code=0,output="ok"),review_pending=True))
    seen={}
    monkeypatch.setattr(p.wall_patch,"review",lambda h,patch:(seen.__setitem__("patch",patch) or dict(clear=True,response={})))
    monkeypatch.setattr(p,"config_paths",lambda *a:[])
    p.review(home,identity)
    assert "checks/care.py" in seen["patch"]["files"]
    assert "checks/blob.py" not in seen["patch"]["files"]


def test_release_intent_is_empty_when_the_worktree_is_gone(tmp_path):
    assert p._release_intent(tmp_path/"missing","a","b")==""


def test_commit_intent_text_drops_attribution_trailers():
    from mishe_tauftauf.git_base_text import commit_intent_text
    raw=("abc123 push hook: name the wake-end check\n\nThe unit is the mechanism.\n\n"
         "Mishe-Wake: wake-x\nMishe-Role: genome\nCo-Authored-By: Claude <n@x>\n")
    text=commit_intent_text(raw)
    assert "push hook: name the wake-end check" in text and "The unit is the mechanism." in text
    assert "Mishe-Wake" not in text and "Mishe-Role" not in text and "Co-Authored-By" not in text


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
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
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
    # A failed review used to be stranded: a later attempt had to be started by
    # hand. The first failure now schedules one delayed re-run; the success does not.
    retries=[argv for argv in launched if any("review-retry" in str(a) for a in argv)]
    assert len(retries)==1
    record=read_json(p.record_path(home,identity))
    assert f"--on-calendar={p._calendar_time(record['review_retry_due'])}" in retries[0]
    assert not any(a.startswith("--on-active=") for a in retries[0])
    assert retries[0][-4:]==["release","review","--id",identity]
    assert record["review_retry_state"]=="scheduled in 1800s"


def test_deferred_review_error_merge_schedules_the_retry(tree,monkeypatch):
    # A wake can hold the dispatch lock when a failed review persists its result;
    # merge_review then writes merge_error and returns. The work-watch poll merges
    # later through merge_reviews, and must schedule the retry itself: before this
    # the deferred merge recorded review_error and left review_pending true with no
    # retry unit, stranding the release exactly as the in-process path used to.
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    identity="deferred-retry"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",source=str(source),paths=["docs/page.md"],check=dict(output="pass"),review_pending=True))
    write_json(home/"work/reviews"/f"{identity}.result.json",dict(id=identity,result=dict(review_error="reviewer quota spent"),merged=False))
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    p.merge_reviews(home)
    record=read_json(p.record_path(home,identity))
    assert record["review_error"]=="reviewer quota spent" and record["review_pending"] is True
    retries=[argv for argv in launched if any("review-retry" in str(a) for a in argv)]
    assert len(retries)==1
    assert f"--on-calendar={p._calendar_time(record['review_retry_due'])}" in retries[0]
    assert record["review_retry_state"]=="scheduled in 1800s"


def test_inconclusive_review_records_the_gap_without_a_fix_obligation(tree,monkeypatch):
    # Three delivered releases (4290a757, 2e2d080d, e232e4f0) merged a review whose
    # only row was `unknown` — "insufficient context", no defect named — and each
    # still raised a six-hour fix/revert obligation. Missing evidence is not a
    # finding: record it as inconclusive and ask nobody to repair what the reviewer
    # never alleged.
    from mishe_tauftauf import failure, intentions
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha)
    identity="inconclusive"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        owner="docs",source=str(source),paths=["docs/page.md"],check=dict(output="pass"),review_pending=True))
    write_json(home/"work/reviews"/f"{identity}.result.json",dict(id=identity,merged=False,result=dict(
        review=dict(clear=False,response=dict(results=[dict(id="PATCH",verdict="unknown",
            reason="insufficient context to assess deeper defects",evidence=[])])))))
    sent=[]
    monkeypatch.setattr(failure,"default_notify",lambda home,role,text:sent.append((role,text)))
    p.merge_reviews(home)
    record=read_json(p.record_path(home,identity))
    assert record["review_state"]=="inconclusive" and record["review_pending"] is False
    assert intentions.list_(home,"docs")==[] and sent==[]


def test_suspicious_review_raises_the_fix_revert_obligation(tree,monkeypatch):
    from mishe_tauftauf import failure, intentions
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha)
    identity="suspicious"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        owner="docs",source=str(source),paths=["docs/page.md"],check=dict(output="pass"),review_pending=True))
    write_json(home/"work/reviews"/f"{identity}.result.json",dict(id=identity,merged=False,result=dict(
        review=dict(clear=False,response=dict(results=[dict(id="PATCH",verdict="suspicious",
            reason="the probe reads the old pin",evidence=[])])))))
    sent=[]
    monkeypatch.setattr(failure,"default_notify",lambda home,role,text:sent.append((role,text)))
    p.merge_reviews(home)
    record=read_json(p.record_path(home,identity))
    assert record["review_state"]=="action-needed"
    entries=intentions.list_(home,"docs")
    assert [e["id"] for e in entries]==["review-"+sha[:12]]
    assert entries[0]["due"]>time.time()
    assert len(sent)==1 and identity in sent[0][1]


def test_a_failed_notice_cannot_lose_a_suspicious_finding(tree,monkeypatch):
    # Telling the owner ran before the record write, so a broken intentions
    # directory or an unavailable chat tape raised out of merge_reviews with the
    # verdict unwritten and review_pending still true (2026-11-18). The finding
    # is the durable fact; the notice is the secondary effect that may fail.
    from mishe_tauftauf import failure, intentions
    from mishe_tauftauf.feed import TapeUnknown
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha)
    identity="notice-failed"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        owner="docs",source=str(source),paths=["docs/page.md"],check=dict(output="pass"),review_pending=True))
    queued=home/"work/reviews"/f"{identity}.result.json"
    write_json(queued,dict(id=identity,merged=False,result=dict(
        review=dict(clear=False,response=dict(results=[dict(id="PATCH",verdict="suspicious",
            reason="the probe reads the old pin",evidence=[])])))))
    def unavailable(home,role,text): raise TapeUnknown("mishe-tape unavailable: no site home and no MISHE_TAPE_BIN")
    monkeypatch.setattr(failure,"default_notify",unavailable)
    p.merge_reviews(home)
    record=read_json(p.record_path(home,identity))
    assert record["review_state"]=="action-needed" and record["review_pending"] is False
    assert "mishe-tape unavailable" in record["review_notice_error"]
    assert [e["id"] for e in intentions.list_(home,"docs")]==["review-"+sha[:12]]
    assert read_json(queued)["merged"] is True


def test_spent_retry_ladder_raises_one_owner_obligation(tree,monkeypatch):
    # The eighth failed attempt spends the ladder: "exhausted" makes
    # _review_retry_lost answer False forever, so before this the record kept
    # review_pending true with no due, no unit, no intention and no message
    # (2026-11-17). The owner must get one obligation and one notice, and no
    # ninth unit; a re-scan of the spent record must add neither.
    from mishe_tauftauf import failure, intentions
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    identity="spent"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        owner="docs",source=str(source),paths=["docs/page.md"],check=dict(output="pass"),
        review_pending=True,review_error="reviewer quota spent"))
    for n in range(p.REVIEW_RETRY_MAX_ATTEMPTS):
        (home/"work/reviews"/f"{identity}-{n}.failed-attempt.json").parent.mkdir(parents=True,exist_ok=True)
        (home/"work/reviews"/f"{identity}-{n}.failed-attempt.json").write_text("{}")
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    sent=[]
    monkeypatch.setattr(failure,"default_notify",lambda home,role,text:sent.append((role,text)))
    current=read_json(p.record_path(home,identity))
    assert p.schedule_review_retry_if_needed(home,current) is False
    assert current["review_retry_state"]=="exhausted"
    assert "review_retry_due" not in current and not launched
    entries=intentions.list_(home,"docs")
    assert [e["id"] for e in entries]==["review-exhausted-"+sha[:12]]
    assert entries[0]["due"]>time.time()
    assert len(sent)==1 and sent[0][0]=="docs" and identity in sent[0][1]
    p.schedule_review_retry_if_needed(home,current)
    assert len(intentions.list_(home,"docs"))==1 and len(sent)==1 and not launched


def test_retry_ladder_advances_on_each_failed_attempt(tree,monkeypatch):
    # Gating the retry on review_retry_state alone latched after the first
    # scheduling and left review_retry_delay's eight attempts unreachable: the
    # next failed attempt must raise the archive count and schedule the next rung.
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    identity="ladder"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",source=str(source),paths=["docs/page.md"],check=dict(output="pass"),review_pending=True))
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    reviews=home/"work/reviews"
    write_json(reviews/f"{identity}.result.json",dict(id=identity,result=dict(review_error="quota spent"),merged=False))
    p.merge_reviews(home)
    assert read_json(p.record_path(home,identity))["review_retry_state"]=="scheduled in 1800s"
    # The scheduled retry ran, archived that failed attempt and failed again.
    (reviews/f"{identity}-1.failed-attempt.json").write_text("{}")
    write_json(reviews/f"{identity}.result.json",dict(id=identity,result=dict(review_error="quota spent"),merged=False))
    p.merge_reviews(home)
    assert read_json(p.record_path(home,identity))["review_retry_state"]=="scheduled in 3600s"
    retries=[argv for argv in launched if any("review-retry" in str(a) for a in argv)]
    deadlines=[next(a for a in argv if a.startswith("--on-calendar=")) for argv in retries]
    assert len(deadlines)==2 and deadlines[0]!=deadlines[1]
    for argv in retries:
        assert not any(a.startswith("--on-active=") for a in argv)


def test_retry_deadline_is_absolute_so_a_reload_cannot_postpone_it(tmp_path,monkeypatch):
    # A user-manager daemon-reload recomputes a transient --on-active timer
    # from the reload moment (measured 2026-11-17: sixteen pending retries due
    # 16:05-16:32 all moved to reload+delay and none fired). The unit must name
    # an absolute deadline instead, and the record must name the same one.
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    current=dict(id="rel-1",sha="b"*40,source=str(tmp_path),review_pending=True)
    p.schedule_review_retry_if_needed(tmp_path,current)
    argv=launched[0]
    due=current["review_retry_due"]
    assert f"--on-calendar={p._calendar_time(due)}" in argv
    assert not any(a.startswith("--on-active=") for a in argv)
    assert due>int(time.time())+p.REVIEW_RETRY_BASE_S-2
    # A deadline already past never fires, so the armed time moves ahead; the
    # returned value is what the record must keep.
    armed=p.schedule_review_retry(tmp_path,current,due=time.time()-10)
    assert armed>=int(time.time())
    assert f"--on-calendar={p._calendar_time(armed)}" in launched[1]


def test_one_failed_attempt_schedules_one_retry(tmp_path,monkeypatch):
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    current=dict(id="rel-1",sha="a"*40,source=str(tmp_path),review_pending=True)
    p.schedule_review_retry_if_needed(tmp_path,current)
    p.schedule_review_retry_if_needed(tmp_path,current)
    assert len(launched)==1 and current["review_retry_attempts"]==0
    (tmp_path/"work/reviews"/"rel-1-1.failed-attempt.json").write_text("{}")
    p.schedule_review_retry_if_needed(tmp_path,current)
    assert len(launched)==2 and current["review_retry_attempts"]==1


def test_a_collected_retry_unit_is_armed_again(tmp_path,monkeypatch):
    # A transient calendar retry cannot be restarted after a stop/collect
    # (the root-migration flip stops every unit that names a moved tree). No
    # attempt is archived, so the attempt count alone cannot see the loss: the
    # recorded due time has to re-arm it, or the release stays review_pending.
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    current=dict(id="rel-1",sha="a"*40,source=str(tmp_path),review_pending=True)
    p.schedule_review_retry_if_needed(tmp_path,current)
    due=current["review_retry_due"]
    assert len(launched)==1 and due > 0
    # Armed and not yet due: no duplicate unit.
    p.schedule_review_retry_if_needed(tmp_path,current)
    assert len(launched)==1
    # The unit was stopped and collected: due long past, still no archive.
    current["review_retry_due"]=time.time() - p.REVIEW_RETRY_REARM_GRACE_S - 1
    p.schedule_review_retry_if_needed(tmp_path,current)
    assert len(launched)==2
    assert current["review_retry_due"] > time.time()
    assert current["review_retry_state"]=="scheduled in 1800s"


def test_legacy_retry_without_a_recorded_due_time_is_armed_again(tmp_path,monkeypatch):
    # Live 2026-11-17: record 2c6380ae was armed before review_retry_due existed
    # (state "scheduled in 1800s", no archives, no due). The flip stops and
    # collects that transient unit, and the due-time check alone returned early
    # forever, so the review stayed stranded.
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    current=dict(id="rel-1",sha="a"*40,source=str(tmp_path),review_pending=True,
                 review_retry_attempts=0,review_retry_state="scheduled in 1800s")
    p.schedule_review_retry_if_needed(tmp_path,current)
    assert len(launched)==1 and current["review_retry_due"]>time.time()
    # Now that the due time is recorded, the next call leaves the unit alone.
    p.schedule_review_retry_if_needed(tmp_path,current)
    assert len(launched)==1


def test_scan_arms_a_legacy_review_that_failed_without_a_recorded_retry(tmp_path,monkeypatch):
    # Two delivered records carried a merged: true result with a reviewer error,
    # review_retry_attempts 0 and no retry state (2026-11-17). Nothing revisits a
    # merged record, so they stayed review_pending for good. A record that
    # already reported an error is not "a first review still running"; a fresh
    # record still is.
    (tmp_path/"work/reviews").mkdir(parents=True)
    (tmp_path/"work/releases").mkdir(parents=True)
    calls=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:calls.append(argv))
    now=time.time()
    write_json(p.record_path(tmp_path,"rel-failed"),
               dict(id="rel-failed",sha="b"*40,source=str(tmp_path),phase="delivered",
                    review_pending=True,review_error="reviewer process failed"))
    write_json(tmp_path/"work/reviews"/"rel-failed-1.result.json",
               dict(id="rel-failed",result=dict(review_error="reviewer process failed"),merged=True))
    write_json(p.record_path(tmp_path,"rel-fresh"),
               dict(id="rel-fresh",sha="c"*40,source=str(tmp_path),phase="delivered",review_pending=True))
    assert p.rearm_lost_review_retries(tmp_path,clock=lambda: now,live_units={})==["rel-failed"]
    assert len([c for c in calls if c[0]=="systemd-run"])==1
    record=read_json(p.record_path(tmp_path,"rel-failed"))
    assert record["review_retry_due"]>now and record["review_retry_attempts"]==0, record
    assert "review_retry_state" not in read_json(p.record_path(tmp_path,"rel-fresh"))


def test_work_poll_scan_rearms_only_a_lost_retry(tmp_path,monkeypatch):
    # A destroyed retry unit leaves no result, so merge_review never reaches the
    # record: work poll's scan is the deterministic detector. It must arm a
    # legacy and an overdue record, leave an armed one and a still-unreviewed
    # record alone, and re-arming must be idempotent within the same poll.
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    (tmp_path/"work/releases").mkdir(parents=True)
    launched=[]
    class NoUnits: stdout=""; returncode=0
    def fake(argv,**kw):
        if argv[0]=="systemd-run":
            launched.append(argv)
        return NoUnits()
    monkeypatch.setattr(p.subprocess,"run",fake)
    now=time.time()
    def record(identity, **extra):
        payload=dict(id=identity,sha=(identity*40)[:40],source=str(tmp_path),review_pending=True)
        payload.update(extra)
        write_json(p.record_path(tmp_path,identity),payload)
    record("rel-legacy",review_retry_attempts=0,review_retry_state="scheduled in 1800s")
    record("rel-overdue",review_retry_attempts=0,review_retry_state="scheduled in 1800s",
           review_retry_due=int(now-p.REVIEW_RETRY_REARM_GRACE_S-1))
    record("rel-armed",review_retry_attempts=0,review_retry_state="scheduled in 1800s",
           review_retry_due=int(now+600))
    record("rel-fresh")
    armed=p.rearm_lost_review_retries(tmp_path,clock=lambda: now,live_units={})
    assert sorted(armed)==["rel-legacy","rel-overdue"], armed
    assert len(launched)==2, launched
    assert read_json(p.record_path(tmp_path,"rel-armed"))["review_retry_due"]==int(now+600)
    assert "review_retry_state" not in read_json(p.record_path(tmp_path,"rel-fresh"))
    assert p.rearm_lost_review_retries(tmp_path,clock=lambda: now,live_units={})==[]
    assert len(launched)==2, launched


def test_scan_rearms_a_record_whose_merged_attempt_never_advanced_it(tmp_path,monkeypatch):
    # The record names the rung armed before its last failed attempt; that retry
    # then archived the attempt and re-ran the reviewer, and the merge wrote the
    # result but lost the record write. The attempt-count gate read the lag as "a
    # merge is pending" and stranded the release for good: five delivered records
    # on 2026-11-17 named attempts 0-1 against 1-2 archives, with no unit and a
    # due time hours past. A merged failed result is that lost rung; an unmerged
    # one still belongs to merge_review, which arms the next rung itself.
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    (tmp_path/"work/releases").mkdir(parents=True)
    launched=[]
    class NoUnits: stdout=""; returncode=0
    def fake(argv,**kw):
        if argv[0]=="systemd-run":
            launched.append(argv)
        return NoUnits()
    monkeypatch.setattr(p.subprocess,"run",fake)
    now=time.time()
    due=int(now-p.REVIEW_RETRY_REARM_GRACE_S-1)
    for identity, merged in (("rel-merged",True),("rel-unmerged",False)):
        write_json(p.record_path(tmp_path,identity),
                   dict(id=identity,sha=(identity*4)[:40],source=str(tmp_path),phase="delivered",
                        review_pending=True,review_retry_attempts=0,
                        review_retry_state="scheduled in 1800s",review_retry_due=due))
        (tmp_path/"work/reviews"/f"{identity}-1.failed-attempt.json").write_text("{}")
        write_json(tmp_path/"work/reviews"/f"{identity}.result.json",
                   dict(id=identity,result=dict(review_error="reviewer quota spent"),merged=merged))
    armed=p.rearm_lost_review_retries(tmp_path,clock=lambda: now,live_units={})
    assert armed==["rel-merged"], armed
    repaired=read_json(p.record_path(tmp_path,"rel-merged"))
    assert repaired["review_retry_attempts"]==1
    assert repaired["review_retry_state"]=="scheduled in 3600s"
    assert repaired["review_retry_due"]>now
    assert len(launched)==1, launched
    assert read_json(p.record_path(tmp_path,"rel-unmerged"))["review_retry_due"]==due


def test_a_malformed_record_does_not_fail_the_work_poll(tmp_path,monkeypatch):
    # Review note on 3a2379dd: a record without "id" raised KeyError outside the
    # scan's caught tuple, so one bad write failed the whole work poll. The scan
    # skips what it cannot identify and still arms the rest.
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    (tmp_path/"work/releases").mkdir(parents=True)
    launched=[]
    class NoUnits: stdout=""; returncode=0
    def fake(argv,**kw):
        if argv[0]=="systemd-run":
            launched.append(argv)
        return NoUnits()
    monkeypatch.setattr(p.subprocess,"run",fake)
    now=time.time()
    write_json(p.record_path(tmp_path,"rel-good"),
               dict(id="rel-good",sha="9"*40,source=str(tmp_path),review_pending=True,
                    review_retry_attempts=0,review_retry_state="scheduled in 1800s"))
    write_json(p.record_path(tmp_path,"rel-noid"),
               dict(sha="8"*40,source=str(tmp_path),review_pending=True,
                    review_retry_attempts=0,review_retry_state="scheduled in 1800s"))
    write_json(p.record_path(tmp_path,"rel-empty-id"),
               dict(id="",sha="7"*40,source=str(tmp_path),review_pending=True,
                    review_retry_attempts=0,review_retry_state="scheduled in 1800s"))
    assert p.rearm_lost_review_retries(tmp_path,clock=lambda: now,live_units={})==["rel-good"]
    assert len(launched)==1, launched
    assert read_json(p.record_path(tmp_path,"rel-noid"))["review_retry_state"]=="scheduled in 1800s"


def test_a_record_with_a_live_retry_unit_is_not_armed_again(tmp_path,monkeypatch):
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/releases").mkdir(parents=True)
    (tmp_path/"work/reviews").mkdir(parents=True)
    launched=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:launched.append(argv))
    now=time.time()
    legacy_sha,overdue_sha="2"*40,"3"*40
    write_json(p.record_path(tmp_path,"rel-legacy"),
               dict(id="rel-legacy",sha=legacy_sha,source=str(tmp_path),review_pending=True,
                    review_retry_attempts=0,review_retry_state="scheduled in 1800s"))
    write_json(p.record_path(tmp_path,"rel-overdue"),
               dict(id="rel-overdue",sha=overdue_sha,source=str(tmp_path),review_pending=True,
                    review_retry_attempts=0,review_retry_state="scheduled in 1800s",
                    review_retry_due=int(now-p.REVIEW_RETRY_REARM_GRACE_S-1)))
    armed=p.rearm_lost_review_retries(tmp_path,clock=lambda: now,
                                      live_units={legacy_sha[:12]:
                                                  {"timers": (f"mishe-release-review-retry-{legacy_sha[:12]}-1794930448.timer",),
                                                   "services": ()}})
    assert armed==["rel-overdue"], armed
    assert len(launched)==1, launched
    # Once the overdue rung is armed, its record names a fresh deadline and the
    # next poll leaves both records alone even though both units are live.
    assert p.rearm_lost_review_retries(
        tmp_path,clock=lambda: now,
        live_units={legacy_sha[:12]:{"timers": (f"mishe-release-review-retry-{legacy_sha[:12]}-1794930448.timer",), "services": ()},
                    overdue_sha[:12]:{"timers": (f"mishe-release-review-retry-{overdue_sha[:12]}-1794930449.timer",), "services": ()}})==[]
    assert len(launched)==1, launched


def test_a_timer_postponed_past_its_deadline_is_replaced(tmp_path,monkeypatch):
    # A user-manager daemon-reload recomputes a monotonic timer's base from the
    # reload, so a pending retry can outlive the deadline its record names
    # (2026-11-17: sixteen retries due 16:05-16:32 all moved to reload+delay and
    # none fired). A live timer this far past its recorded deadline is stopped
    # and re-armed; a review running now is a service, not a timer, and is never
    # duplicated.
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    (tmp_path/"work/releases").mkdir(parents=True)
    calls=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:calls.append(argv))
    now=time.time()
    stale,running,spent="4"*40,"5"*40,"6"*40
    for name,sha,attempts in (("rel-stale",stale,0),("rel-running",running,0),
                              ("rel-spent",spent,p.REVIEW_RETRY_MAX_ATTEMPTS)):
        write_json(p.record_path(tmp_path,name),
                   dict(id=name,sha=sha,source=str(tmp_path),review_pending=True,
                        review_retry_attempts=attempts,review_retry_state="scheduled in 7200s",
                        review_retry_due=int(now-p.REVIEW_RETRY_REARM_GRACE_S-1)))
    for attempt in range(p.REVIEW_RETRY_MAX_ATTEMPTS):
        (tmp_path/"work/reviews"/f"rel-spent-{attempt}.failed-attempt.json").write_text("{}")
    stale_timer=f"mishe-release-review-retry-{stale[:12]}-1794930448.timer"
    spent_timer=f"mishe-release-review-retry-{spent[:12]}-1794930448.timer"
    live={stale[:12]:{"timers": (stale_timer,), "services": ()},
          running[:12]:{"timers": (),
                        "services": (f"mishe-release-review-retry-{running[:12]}-1794930448.service",)},
          spent[:12]:{"timers": (spent_timer,), "services": ()}}
    armed=p.rearm_lost_review_retries(tmp_path,clock=lambda: now,live_units=live)
    assert sorted(armed)==["rel-spent","rel-stale"], armed
    stops=[c for c in calls if c[0]=="systemctl" and "stop" in c]
    # The spent ladder gets no replacement, so its pending unit is left to run
    # its one review rather than be stopped with nothing to take its place.
    assert stops==[["systemctl","--user","stop",stale_timer]], stops
    runs=[c for c in calls if c[0]=="systemd-run"]
    assert len(runs)==1 and any(a.startswith("--on-calendar=") for a in runs[0]), runs
    assert read_json(p.record_path(tmp_path,"rel-spent"))["review_retry_state"]=="exhausted"
    assert read_json(p.record_path(tmp_path,"rel-running"))["review_retry_due"]==int(now-p.REVIEW_RETRY_REARM_GRACE_S-1)


def test_a_failed_rearm_leaves_the_postponed_timer_running(tmp_path,monkeypatch):
    # A start that fails arms nothing, so the old timer is the only pending
    # attempt. Stopping it would leave review_pending true with no unit at all:
    # the next poll can only try the same failing start again. The recorded due
    # time is kept, so that poll does retry the arming.
    from mishe_tauftauf import release_promotion as p
    (tmp_path/"work/reviews").mkdir(parents=True)
    (tmp_path/"work/releases").mkdir(parents=True)
    calls=[]
    def run(argv,**kw):
        calls.append(argv)
        if argv[0]=="systemd-run":
            raise subprocess.CalledProcessError(1,argv)
    monkeypatch.setattr(p.subprocess,"run",run)
    now=time.time(); sha="4"*40
    stale_due=int(now-p.REVIEW_RETRY_REARM_GRACE_S-1)
    write_json(p.record_path(tmp_path,"rel-stale"),
               dict(id="rel-stale",sha=sha,source=str(tmp_path),review_pending=True,
                    review_retry_attempts=0,review_retry_state="scheduled in 7200s",
                    review_retry_due=stale_due))
    timer=f"mishe-release-review-retry-{sha[:12]}-1794930448.timer"
    p.rearm_lost_review_retries(tmp_path,clock=lambda: now,
                                live_units={sha[:12]:{"timers":(timer,),"services":()}})
    assert not [c for c in calls if c[0]=="systemctl" and "stop" in c], calls
    record=read_json(p.record_path(tmp_path,"rel-stale"))
    assert record["review_retry_state"].startswith("scheduling failed:"), record
    assert record["review_retry_due"]==stale_due and record["review_pending"] is True


def _overdue_retry(tmp_path, now, sha="a"*40, identity="rel-overdue"):
    (tmp_path/"work/reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path/"work/releases").mkdir(parents=True, exist_ok=True)
    write_json(p.record_path(tmp_path,identity),
               dict(id=identity,sha=sha,source=str(tmp_path),review_pending=True,
                    review_retry_attempts=0,review_retry_state="scheduled in 7200s",
                    review_retry_due=int(now-p.REVIEW_RETRY_REARM_GRACE_S-1)))
    return f"mishe-release-review-retry-{sha[:12]}-1794930448.timer"


def test_rearm_scan_runs_while_a_wake_holds_the_quiet_lock(tmp_path,monkeypatch):
    # A wake runner holds .dispatch.lock SHARED for its whole run (core.supervisor:
    # delivery waits for every running wake to end), so an exclusive re-arm scan is
    # refused exactly while any role is awake. Measured 2026-11-17 with three wakes
    # up: all sixteen overdue records hit DeliveryBusy and no retry was re-armed.
    # The scan must exclude the exclusive writers only, so a shared hold runs it.
    import fcntl
    (tmp_path/"dispatch").mkdir()
    calls=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:calls.append(argv))
    now=time.time(); timer=_overdue_retry(tmp_path,now)
    quiet=(tmp_path/"dispatch/.dispatch.lock").open("a+")
    fcntl.flock(quiet.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    try:
        armed=p.rearm_lost_review_retries(tmp_path,clock=lambda: now,
            live_units={("a"*12):{"timers":(timer,),"services":()}},skipped=[])
    finally:
        quiet.close()
    assert armed==["rel-overdue"], armed
    runs=[c for c in calls if c[0]=="systemd-run"]
    assert len(runs)==1 and any(a.startswith("--on-calendar=") for a in runs[0]), runs
    # The superseded monotonic timer is stopped only after the replacement is armed.
    assert [c for c in calls if c[0]=="systemctl"]==[["systemctl","--user","stop",timer]]


def test_rearm_scan_counts_records_an_exclusive_holder_blocks(tmp_path,monkeypatch):
    # A delivery or activation holds the same lock exclusively. The record cannot be
    # read-modified-written then, so the scan skips it and reports that skip instead
    # of leaving a starved retry looking like nothing was overdue.
    import fcntl
    (tmp_path/"dispatch").mkdir()
    calls=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:calls.append(argv))
    now=time.time(); timer=_overdue_retry(tmp_path,now)
    quiet=(tmp_path/"dispatch/.dispatch.lock").open("a+")
    fcntl.flock(quiet.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    skipped=[]
    try:
        armed=p.rearm_lost_review_retries(tmp_path,clock=lambda: now,
            live_units={("a"*12):{"timers":(timer,),"services":()}},skipped=skipped)
    finally:
        quiet.close()
    assert armed==[] and skipped==["rel-overdue"], (armed,skipped)
    assert calls==[] and read_json(p.record_path(tmp_path,"rel-overdue"))["review_pending"] is True


def test_a_held_scan_lock_skips_the_poll_instead_of_arming_twice(tmp_path,monkeypatch):
    # Two polls must not arm two units for the same rung. The records lock that
    # serialises scans also makes the late one a no-op; the next poll then sees
    # the fresh deadline.
    import fcntl
    calls=[]
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:calls.append(argv))
    now=time.time(); timer=_overdue_retry(tmp_path,now)
    live={("a"*12):{"timers":(timer,),"services":()}}
    scan=(tmp_path/"work/.review-records.lock").open("a+")
    fcntl.flock(scan.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert p.rearm_lost_review_retries(tmp_path,clock=lambda: now,live_units=live)==[]
    finally:
        scan.close()
    assert [c for c in calls if c[0]=="systemd-run"]==[]
    assert p.rearm_lost_review_retries(tmp_path,clock=lambda: now,live_units=live)==["rel-overdue"]
    assert len([c for c in calls if c[0]=="systemd-run"])==1


def test_retry_over_an_unmerged_failure_reruns_the_review(tree,monkeypatch):
    # The scheduled retry must re-run the model even when a wake deferred the first
    # failure's merge. Merely merging it would record the error and return without
    # a second attempt, wasting the retry.
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha);p.materialize(home,repo,old)
    identity="retry-unmerged"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",source=str(source),paths=["docs/page.md"],check=dict(output="pass"),review_pending=True))
    write_json(home/"work/reviews"/f"{identity}.result.json",dict(id=identity,result=dict(review_error="reviewer quota spent"),merged=False))
    calls=[]
    monkeypatch.setattr(p.wall_patch,"review",lambda *a,**k:calls.append(1) or dict(clear=True))
    monkeypatch.setattr(p.subprocess,"run",lambda argv,**kw:None)
    assert p.review(home,identity)["phase"]=="reviewed"
    assert len(calls)==1
    assert list((home/"work/reviews").glob("*.failed-attempt.json"))


def test_review_retry_delay_backs_off_and_gives_up(tmp_path):
    from mishe_tauftauf import release_promotion as p
    reviews=tmp_path/"work/reviews";reviews.mkdir(parents=True)
    delays=[]
    for attempt in range(p.REVIEW_RETRY_MAX_ATTEMPTS):
        delays.append(p.review_retry_delay(tmp_path,"rel-1"))
        (reviews/f"rel-1-{attempt}.failed-attempt.json").write_text("{}")
    assert delays==[1800,3600,7200,14400,28800,28800,28800,28800]
    assert p.review_retry_delay(tmp_path,"rel-1") is None
    # Another release's archives do not count against this one.
    assert p.review_retry_delay(tmp_path,"rel-2")==1800


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
    launches=[argv for argv in launched if any("release-review" in str(a) for a in argv)]
    assert len(launches)==1 and len(calls)==1
    assert read_json(p.record_path(home,"second"))["review_pending"] is False
    assert read_json(p.record_path(home,"first"))["phase"]==prior_phase


def test_review_merge_retries_a_transient_wake_lock(tree, monkeypatch):
    from contextlib import contextmanager
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha); p.materialize(home,repo,old)
    identity="retry-merge"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        source=str(source),paths=["docs/page.md"],check=dict(output="pass"),owner="genome",review_pending=True))
    queued=home/"work/reviews"/f"{identity}.result.json"
    queued.parent.mkdir(parents=True,exist_ok=True)
    write_json(queued,dict(id=identity,result=dict(review=dict(clear=True)),merged=False))
    attempts=[]
    @contextmanager
    def flaky_lock(h, **kw):
        attempts.append(1)
        if len(attempts)<3: raise p.DeliveryBusy("running wake holds dispatch lock")
        yield
    monkeypatch.setattr(p,"delivery_lock",flaky_lock)
    now=[0.0]
    p.merge_review(home,queued,sleep=lambda s:now.__setitem__(0,now[0]+s),clock=lambda:now[0],retry_s=2.0)
    assert len(attempts)==3
    assert read_json(queued)["merged"] is True and "merge_error" not in read_json(queued)
    record=read_json(p.record_path(home,identity))
    assert record["review_pending"] is False and record["review_state"]=="clear"


def test_review_merge_runs_while_a_wake_holds_the_quiet_lock(tree, monkeypatch):
    # A wake runner holds .dispatch.lock SHARED for its whole run. The merge only
    # needs to exclude delivery/activation, so it joins the shared hold; an
    # exclusive merge was refused exactly while any role was awake (measured
    # 2026-11-17: seventy queued results carried merge_error for that reason).
    import fcntl
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha); p.materialize(home,repo,old)
    identity="wake-merge"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        source=str(source),paths=["docs/page.md"],check=dict(output="pass"),owner="genome",review_pending=True))
    queued=home/"work/reviews"/f"{identity}.result.json"
    queued.parent.mkdir(parents=True,exist_ok=True)
    write_json(queued,dict(id=identity,result=dict(review=dict(clear=True)),merged=False))
    (home/"dispatch").mkdir(exist_ok=True)
    quiet=(home/"dispatch/.dispatch.lock").open("a+")
    fcntl.flock(quiet.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    try:
        now=[0.0]
        p.merge_review(home,queued,sleep=lambda s:now.__setitem__(0,now[0]+s),clock=lambda:now[0],retry_s=2.0)
    finally:
        quiet.close()
    assert read_json(queued)["merged"] is True and "merge_error" not in read_json(queued)
    record=read_json(p.record_path(home,identity))
    assert record["review_pending"] is False and record["review_state"]=="clear"


def test_review_merge_defers_to_a_scan_then_merges(tree, monkeypatch):
    # The merge and the re-arm scan read-modify-write the same records, so one
    # must not overwrite the other. The shared dispatch lock cannot order them
    # (wakes hold it shared too); the records lock does, and a busy one is a
    # retry rather than a lost result.
    import fcntl
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha); p.materialize(home,repo,old)
    identity="scan-merge"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        source=str(source),paths=["docs/page.md"],check=dict(output="pass"),owner="genome",review_pending=True))
    queued=home/"work/reviews"/f"{identity}.result.json"
    queued.parent.mkdir(parents=True,exist_ok=True)
    write_json(queued,dict(id=identity,result=dict(review=dict(clear=True)),merged=False))
    held=(home/"work/.review-records.lock").open("a+")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    now=[0.0]; released=[]
    def sleep(s):
        now[0]+=s
        if not released:
            fcntl.flock(held.fileno(), fcntl.LOCK_UN); released.append(1)
    try:
        p.merge_review(home,queued,sleep=sleep,clock=lambda:now[0],retry_s=5.0)
    finally:
        held.close()
    assert released==[1] and now[0]>0
    assert read_json(queued)["merged"] is True
    assert read_json(p.record_path(home,identity))["review_pending"] is False


def test_review_merge_keeps_pending_evidence_when_lock_never_frees(tree, monkeypatch):
    from contextlib import contextmanager
    repo,home,old,sha=tree
    source=p.materialize(home,repo,sha); p.materialize(home,repo,old)
    identity="busy-merge"
    write_json(p.record_path(home,identity),dict(id=identity,sha=sha,previous=old,phase="delivered",
        source=str(source),paths=["docs/page.md"],check=dict(output="pass"),owner="genome",review_pending=True))
    queued=home/"work/reviews"/f"{identity}.result.json"
    queued.parent.mkdir(parents=True,exist_ok=True)
    write_json(queued,dict(id=identity,result=dict(review=dict(clear=True)),merged=False))
    attempts=[]
    @contextmanager
    def always_busy(h, **kw):
        attempts.append(1)
        raise p.DeliveryBusy("running wake holds dispatch lock")
        yield
    monkeypatch.setattr(p,"delivery_lock",always_busy)
    now=[0.0]
    p.merge_review(home,queued,sleep=lambda s:now.__setitem__(0,now[0]+s),clock=lambda:now[0],retry_s=1.0)
    assert len(attempts)>=2
    result=read_json(queued)
    assert result["merged"] is False and "dispatch lock" in result["merge_error"]
    assert read_json(p.record_path(home,identity))["review_pending"] is True


def test_review_merge_batch_budget_bounds_work_poll_time(tree, monkeypatch):
    from contextlib import contextmanager
    repo, home, old, sha = tree
    source = p.materialize(home, repo, sha); p.materialize(home, repo, old)
    for i in range(4):
        identity = f"batch-{i}"
        write_json(p.record_path(home, identity), dict(id=identity, sha=sha, previous=old, phase="delivered",
            source=str(source), paths=["docs/page.md"], check=dict(output="pass"), owner="genome", review_pending=True))
        queued = home/"work/reviews"/f"{identity}.result.json"
        queued.parent.mkdir(parents=True, exist_ok=True)
        write_json(queued, dict(id=identity, result=dict(review=dict(clear=True)), merged=False))
    attempts = []
    @contextmanager
    def always_busy(h, **kw):
        attempts.append(1)
        raise p.DeliveryBusy("running wake holds dispatch lock")
        yield
    monkeypatch.setattr(p, "delivery_lock", always_busy)
    now = [0.0]
    p.merge_reviews(home, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0], budget_s=20.0)
    assert now[0] <= 20.5          # one bounded batch, not 4 x 30s
    assert len(attempts) >= 4      # every pending result still attempted
    for i in range(4):
        result = read_json(home/"work/reviews"/f"batch-{i}.result.json")
        assert result["merged"] is False and "dispatch lock" in result["merge_error"]


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


_REAL_ENSURE_DUAL_PROBE = p.ensure_dual_probe


@pytest.fixture(autouse=True)
def no_real_probe(monkeypatch):
    # The root-fs probe is host state: activation/restore tests never start, stop or shadow the real unit.
    monkeypatch.setattr(p, "ensure_dual_probe", lambda home, unit_dir=None: "stubbed")


def _systemctl_recorder(monkeypatch, states):
    calls = []
    monkeypatch.setattr(p, "_probe_fields", lambda: states.pop(0))
    monkeypatch.setattr(p.subprocess, "run", lambda argv, **k: calls.append(" ".join(argv)))
    return calls


def test_installed_probe_replaces_a_shadowing_transient(tmp_path, monkeypatch):
    unit_dir = tmp_path / "units"; unit_dir.mkdir(); durable = unit_dir / p.DUAL_PROBE; durable.write_text("unit")
    transient = "/run/user/1000/systemd/transient/mishe-dual-probe.service"
    calls = _systemctl_recorder(monkeypatch, [dict(FragmentPath=transient, ActiveState="active"),
                                              dict(FragmentPath=str(durable), ActiveState="active")])
    assert _REAL_ENSURE_DUAL_PROBE(tmp_path, unit_dir) == "installed"
    assert calls == ["systemctl --user stop mishe-dual-probe.service", "systemctl --user daemon-reload",
                     "systemctl --user enable mishe-dual-probe.service", "systemctl --user start mishe-dual-probe.service"]


def test_installed_probe_already_loaded_is_only_started(tmp_path, monkeypatch):
    unit_dir = tmp_path / "units"; unit_dir.mkdir(); durable = unit_dir / p.DUAL_PROBE; durable.write_text("unit")
    calls = _systemctl_recorder(monkeypatch, [dict(FragmentPath=str(durable), ActiveState="inactive"),
                                              dict(FragmentPath=str(durable), ActiveState="active")])
    assert _REAL_ENSURE_DUAL_PROBE(tmp_path, unit_dir) == "installed"
    assert not any("daemon-reload" in c or "stop " in c for c in calls)


def test_unknown_probe_fragment_is_refused_not_guessed(tmp_path, monkeypatch):
    unit_dir = tmp_path / "units"; unit_dir.mkdir(); (unit_dir / p.DUAL_PROBE).write_text("unit")
    calls = _systemctl_recorder(monkeypatch, [dict(FragmentPath="/etc/systemd/user/other.service", ActiveState="active")])
    with pytest.raises(RuntimeError, match="fragment UNKNOWN"):
        _REAL_ENSURE_DUAL_PROBE(tmp_path, unit_dir)
    assert calls == []


def test_revert_without_installed_probe_restarts_the_transient(tmp_path, monkeypatch):
    unit_dir = tmp_path / "units"; unit_dir.mkdir()
    calls = _systemctl_recorder(monkeypatch, [dict(ActiveState="inactive")])
    assert _REAL_ENSURE_DUAL_PROBE(tmp_path, unit_dir) == "transient"
    assert len(calls) == 1 and calls[0].startswith("systemd-run --user --collect --unit=mishe-dual-probe.service")
    assert calls[0].endswith(f"/usr/bin/python3 {tmp_path / 'operator' / 'dual_probe.py'}")


def test_running_probe_without_installed_unit_is_left_alone(tmp_path, monkeypatch):
    unit_dir = tmp_path / "units"; unit_dir.mkdir()
    calls = _systemctl_recorder(monkeypatch, [dict(ActiveState="active")])
    assert _REAL_ENSURE_DUAL_PROBE(tmp_path, unit_dir) == "transient"
    assert calls == []


def _advance_tree(tmp_path):
    repo = tmp_path / "arepo"; repo.mkdir()
    git(repo, "init", "-q"); git(repo, "config", "user.name", "t"); git(repo, "config", "user.email", "t@e.org")
    (repo / "docs").mkdir(); (repo / "docs/a.md").write_text("a")
    git(repo, "add", "."); git(repo, "commit", "-qm", "base"); pin = git(repo, "rev-parse", "HEAD")
    (repo / "docs/b.md").write_text("b"); git(repo, "add", "."); git(repo, "commit", "-qm", "docs2"); c1 = git(repo, "rev-parse", "HEAD")
    (repo / "tests").mkdir(); (repo / "tests/t_x.py").write_text("x"); git(repo, "add", "."); git(repo, "commit", "-qm", "tests"); c2 = git(repo, "rev-parse", "HEAD")
    return repo, pin, c1, c2


def test_advance_target_takes_the_newest_commit_whatever_ci_or_paths_say(tmp_path):
    repo, pin, c1, c2 = _advance_tree(tmp_path)
    assert p.advance_candidates(repo, pin) == [c1, c2]
    assert p.advance_target(repo, pin) == (c2, None)
    (repo / "duties.json").write_text("{}"); git(repo, "add", "."); git(repo, "commit", "-qm", "mechanism")
    assert p.advance_target(repo, pin) == (git(repo, "rev-parse", "HEAD"), None)


def test_advance_target_is_pin_when_pin_is_head(tmp_path):
    repo, _pin, _c1, c2 = _advance_tree(tmp_path)
    assert p.advance_target(repo, c2) == (None, "pin-is-head")




def _hold_dispatch_lock(home):
    import fcntl
    from mishe_tauftauf.core.state import DispatchStore
    path = DispatchStore(home / "dispatch").lock_path
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def test_held_lock_with_no_wait_refuses_at_once_while_a_wake_holds_the_lock(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    try:
        with pytest.raises(p.DeliveryBusy):
            with p.held_delivery_lock(tmp_path, 0):
                pass
    finally:
        handle.close()
    assert not (tmp_path / "hold").exists()


def test_held_lock_waits_under_a_hold_for_the_running_wake_and_releases_the_hold(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    seen = {"hold_while_waiting": None, "sleeps": 0}

    def sleep(_):
        seen["sleeps"] += 1
        seen["hold_while_waiting"] = (tmp_path / "hold").exists()
        handle.close()   # the wake settles

    from mishe_tauftauf import wake
    resumed = []
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(wake, "resume_owed", lambda *a, **k: resumed.append(True))
    mp.setattr("mishe_tauftauf.roles.declared", lambda home: [])
    try:
        with p.held_delivery_lock(tmp_path, 60, sleep=sleep, patience_s=0):
            assert (tmp_path / "hold").exists()
    finally:
        mp.undo()
    assert seen["sleeps"] == 1 and seen["hold_while_waiting"] is True
    assert not (tmp_path / "hold").exists() and resumed == [True]


def test_held_lock_gives_up_when_the_wake_never_settles_and_still_releases_the_hold(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    from mishe_tauftauf import wake
    mp = pytest.MonkeyPatch()
    mp.setattr(wake, "resume_owed", lambda *a, **k: None)
    mp.setattr("mishe_tauftauf.roles.declared", lambda home: [])
    try:
        with pytest.raises(p.DeliveryBusy):
            with p.held_delivery_lock(tmp_path, 0.01, sleep=lambda s: None, patience_s=0):
                pass
    finally:
        mp.undo()
        handle.close()
    assert not (tmp_path / "hold").exists()


def test_a_manual_hold_written_in_the_claim_window_is_neither_overwritten_nor_released(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    from mishe_tauftauf import wake
    mp = pytest.MonkeyPatch()
    mp.setattr(wake, "resume_owed", lambda *a, **k: None)
    mp.setattr("mishe_tauftauf.roles.declared", lambda home: [])
    real_open = os.open

    def racing_open(path, flags, mode=0o777):
        # The human's `mishe hold on` lands between the exists() check and our create.
        (tmp_path / "hold").write_text("operator: covered 16:00-16:10\n")
        return real_open(path, flags, mode)

    mp.setattr(p.os, "open", racing_open)
    try:
        with pytest.raises(p.DeliveryBusy):
            with p.held_delivery_lock(tmp_path, 0.01, sleep=lambda s: None, patience_s=0):
                pass
    finally:
        mp.undo()
        handle.close()
    assert (tmp_path / "hold").read_text() == "operator: covered 16:00-16:10\n"


def test_a_delivered_promotion_publishes_the_outcome_signal_as_an_edge_per_sha(tree):
    from mishe_tauftauf import signal_store
    repo,home,old,sha=tree
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a:None,probe=lambda *a,**k:dict(code=0,output="ok"),
        reviewer=lambda *a:None,closure=lambda *a:None)
    reading=signal_store.recorded(home, "moving-pin-delivery")
    assert r["phase"]=="delivered" and reading["value"]=="VERIFIED" and reading["event"]==sha+":delivered"
    (repo/"docs/page2.md").write_text("two"); git(repo,"add","."); git(repo,"commit","-qm","two")
    second=git(repo,"rev-parse","HEAD")
    p.promote(home,repo,second,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=sha,activate=lambda *a:None,probe=lambda *a,**k:dict(code=0,output="ok"),
        reviewer=lambda *a:None,closure=lambda *a:None)
    assert signal_store.recorded(home, "moving-pin-delivery")["event"]==second+":delivered"


def test_a_failed_check_publishes_failed_and_a_missing_signal_never_raises(tree):
    from mishe_tauftauf import signal_store
    repo,home,old,sha=tree
    r=p.promote(home,repo,sha,check=[sys.executable,"-c","raise SystemExit(3)"],observe=["probe"],
        previous=old,activate=lambda *a:None,closure=lambda *a:None)
    assert r["phase"]=="check-failed" and "signal_error" in r        # not registered: recorded, not raised
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])
    p.promote(home,repo,sha,check=[sys.executable,"-c","raise SystemExit(3)"],observe=["probe"],
        previous=old,activate=lambda *a:None,closure=lambda *a:None)
    assert signal_store.recorded(home, "moving-pin-delivery")["value"]=="FAILED"


def test_a_failed_promotion_writes_the_outcome_detail_and_a_delivered_one_clears_it(tree):
    from mishe_tauftauf import signal_store
    repo,home,old,sha=tree
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])
    detail=home/"signals"/"moving-pin-delivery.detail"
    failing=("import sys; print('collected 2 items'); print('FAILED tests/test_x.py::test_y'); sys.exit(1)")
    p.promote(home,repo,sha,check=[sys.executable,"-c",failing],observe=["probe"],
        previous=old,activate=lambda *a:None,closure=lambda *a:None)
    text=detail.read_text()                                          # the woken reviewer reads sha, phase and reason
    assert sha[:8] in text and "check-failed" in text and "FAILED tests/test_x.py::test_y" in text
    p.promote(home,repo,sha,check=[sys.executable,"-c","assert True"],observe=["probe"],
        previous=old,activate=lambda *a:None,probe=lambda *a,**k:dict(code=0,output="ok"),
        reviewer=lambda *a:None,closure=lambda *a:None)
    assert not detail.exists()                                       # a fresh VERIFIED is not shown a stale failure


def test_a_quiet_gap_inside_the_patience_window_is_taken_without_ever_holding_wakes(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    seen = {"sleeps": 0, "hold_ever": False}

    def sleep(_):
        seen["sleeps"] += 1
        seen["hold_ever"] = seen["hold_ever"] or (tmp_path / "hold").exists()
        if seen["sleeps"] == 3:
            handle.close()   # the running wake ends: a gap

    with p.held_delivery_lock(tmp_path, 60, sleep=sleep, patience_s=600):
        assert not (tmp_path / "hold").exists()
    assert seen["sleeps"] == 3 and seen["hold_ever"] is False
    assert not (tmp_path / "hold").exists()


def test_with_no_gap_the_hold_starts_only_after_the_patience_window(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    now = {"t": 0.0}
    seen = {"hold_at": None}

    def clock():
        return now["t"]

    def sleep(_):
        now["t"] += 100.0
        if (tmp_path / "hold").exists() and seen["hold_at"] is None:
            seen["hold_at"] = now["t"]
            handle.close()

    from mishe_tauftauf import wake
    mp = pytest.MonkeyPatch()
    mp.setattr(wake, "resume_owed", lambda *a, **k: None)
    mp.setattr("mishe_tauftauf.roles.declared", lambda home: [])
    try:
        with p.held_delivery_lock(tmp_path, 3000, sleep=sleep, patience_s=300, clock=clock):
            pass
    finally:
        mp.undo()
    assert seen["hold_at"] is not None and seen["hold_at"] >= 300.0
    assert not (tmp_path / "hold").exists()



def test_a_foreign_hold_is_left_alone_and_never_resumed(tmp_path):
    """Regression (2026-11-17 12:27-12:32Z): the delivery wrote its promotion hold
    after patience, unconditionally, and unlinked it in `finally`. A manual
    `mishe hold on` — or plant_boot's boot-guard hold — is already stopping wakes;
    overwriting it and then unlinking resumes wakes its owner still wants held."""
    handle = _hold_dispatch_lock(tmp_path)
    manual = "authorized reboot: waiting for current-boot guard admission\n"
    (tmp_path / "hold").write_text(manual)
    resumed = []

    from mishe_tauftauf import wake
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(wake, "resume_owed", lambda *a, **k: resumed.append(True))
    mp.setattr("mishe_tauftauf.roles.declared", lambda home: [])
    try:
        with pytest.raises(p.DeliveryBusy):
            with p.held_delivery_lock(tmp_path, 0.01, sleep=lambda s: None, patience_s=0):
                pass
    finally:
        mp.undo()
        handle.close()
    assert (tmp_path / "hold").read_text() == manual
    assert resumed == []


def test_a_hold_rewritten_while_the_delivery_waited_is_left_to_its_new_owner(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    now = {"t": 0.0}
    resumed = []

    def clock():
        return now["t"]

    def sleep(_):
        now["t"] += 100.0
        hold = tmp_path / "hold"
        if hold.exists() and "manual" not in hold.read_text():
            hold.write_text("manual hold\n")
            handle.close()   # the wake settles after the manual hold appears

    from mishe_tauftauf import wake
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(wake, "resume_owed", lambda *a, **k: resumed.append(True))
    mp.setattr("mishe_tauftauf.roles.declared", lambda home: [])
    try:
        with p.held_delivery_lock(tmp_path, 1000, sleep=sleep, patience_s=100, clock=clock):
            pass
    finally:
        mp.undo()
        handle.close()
    assert (tmp_path / "hold").read_text() == "manual hold\n"
    assert resumed == []


def test_lock_holder_info_reads_pid_and_argv_from_lsof_and_ps(tmp_path):
    calls = []

    class Result:
        def __init__(self, code, out):
            self.returncode = code; self.stdout = out; self.stderr = ""

    def runner(argv, **kw):
        calls.append(argv)
        if argv[0] == "lsof": return Result(0, "2222\n1111\n1111\n")
        if argv[0] == "ps": return Result(0, " 1111 mishe-wake --role health\n 2222 mishe-deliver promote\n")
        raise AssertionError(argv)

    info = p._lock_holder_info(tmp_path, runner=runner)
    assert info["available"] is True
    assert info["holders"] == [
        dict(pid="1111", argv="mishe-wake --role health"),
        dict(pid="2222", argv="mishe-deliver promote"),
    ]
    assert calls[0][:2] == ["lsof", "-t"]
    assert calls[1][0] == "ps"


def test_lock_holder_info_records_unavailable_without_raising(tmp_path):
    def missing(*a, **k): raise FileNotFoundError("lsof")
    info = p._lock_holder_info(tmp_path, runner=missing)
    assert info["available"] is False and "lsof" in info["reason"]

    class Bad:
        returncode = 1; stdout = ""; stderr = "no such file"
    info = p._lock_holder_info(tmp_path, runner=lambda *a, **k: Bad())
    assert info["available"] is False and "exit 1" in info["reason"]


def test_held_lock_give_up_attaches_holder_info_to_delivery_busy(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    seen = {}

    def probe(home):
        seen["home"] = home
        return dict(available=True, holders=[dict(pid="1", argv="wake")])

    from mishe_tauftauf import wake
    mp = pytest.MonkeyPatch()
    mp.setattr(wake, "resume_owed", lambda *a, **k: None)
    mp.setattr("mishe_tauftauf.roles.declared", lambda home: [])
    try:
        with pytest.raises(p.DeliveryBusy) as caught:
            with p.held_delivery_lock(tmp_path, 0.01, sleep=lambda s: None, patience_s=0, holder_probe=probe):
                pass
    finally:
        mp.undo()
        handle.close()
    assert seen["home"] == tmp_path
    assert caught.value.holders == dict(available=True, holders=[dict(pid="1", argv="wake")])
    assert not (tmp_path / "hold").exists()


def test_promotion_records_lock_holder_and_phase_on_delivery_busy(tree, monkeypatch):
    from mishe_tauftauf import signal_store
    repo, home, old, sha = tree
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])

    def lock(home_arg, wait_s, **kw):
        exc = p.DeliveryBusy("busy")
        exc.holders = dict(available=True, holders=[dict(pid="4242", argv="mishe-wake --role senses")])
        raise exc

    monkeypatch.setattr(p, "held_delivery_lock", lock)
    with pytest.raises(p.DeliveryBusy):
        p.promote(home, repo, sha, check=[sys.executable, "-c", "assert True"], observe=["probe"],
                  previous=old, activate=lambda *a: None, probe=lambda *a, **k: dict(code=0, output="ok"),
                  reviewer=lambda *a: None, closure=lambda *a: None, wait_s=2400)
    records = list((home / "work/releases").glob("*.json"))
    assert len(records) == 1
    record = read_json(records[0])
    assert record["phase"] == "delivery-busy"
    assert record["held_delivery_lock_holder"]["holders"][0]["pid"] == "4242"
    # The raise must still publish: otherwise the signal keeps the previous reading (often VERIFIED).
    reading = signal_store.recorded(home, "moving-pin-delivery")
    assert reading["value"] == "FAILED" and reading["event"] == sha + ":delivery-busy"
    detail = (home / "signals" / "moving-pin-delivery.detail").read_text()
    assert sha[:8] in detail and "delivery-busy" in detail and "2400s activation hold" in detail


def test_a_failed_import_closure_publishes_failed(tree):
    from mishe_tauftauf import signal_store
    repo, home, old, sha = tree
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])

    def broken_closure(source, env):
        raise ValueError("committed import closure failed: No module named missing")

    with pytest.raises(ValueError, match="import closure failed"):
        p.promote(home, repo, sha, check=[sys.executable, "-c", "assert True"], observe=["probe"],
                  previous=old, activate=lambda *a: None, probe=lambda *a, **k: dict(code=0, output="ok"),
                  reviewer=lambda *a: None, closure=broken_closure)
    reading = signal_store.recorded(home, "moving-pin-delivery")
    assert reading["value"] == "FAILED" and reading["event"] == sha + ":check-failed"
    assert "import closure failed" in (home / "signals" / "moving-pin-delivery.detail").read_text()


def test_a_source_mutated_during_checks_publishes_failed(tree, monkeypatch):
    from mishe_tauftauf import signal_store
    repo, home, old, sha = tree
    signal_store.register(home, "moving-pin-delivery", max_age=7200, subscribers=["field-review"])
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    mutating = "open('mutated-during-check','w').write('x')"
    with pytest.raises(ValueError, match="committed source changed during checks"):
        p.promote(home, repo, sha, check=[sys.executable, "-c", mutating], observe=["probe"],
                  previous=old, activate=lambda *a: None, probe=lambda *a, **k: dict(code=0, output="ok"),
                  reviewer=lambda *a: None, closure=lambda *a: None)
    reading = signal_store.recorded(home, "moving-pin-delivery")
    assert reading["value"] == "FAILED" and reading["event"] == sha + ":check-mutated-source"
    assert "check-mutated-source" in (home / "signals" / "moving-pin-delivery.detail").read_text()

def test_stale_hold_with_a_dead_writer_pid_is_cleared_when_the_dispatch_lock_is_free(tmp_path):
    hold = tmp_path / "hold"
    hold.write_text(f"{p.HOLD_TEXT}\npid=999999 started=1\n")
    assert p.clear_stale_hold(tmp_path, pid_alive=lambda pid: False) is True
    assert not hold.exists()


def test_hold_with_a_live_writer_pid_is_kept(tmp_path):
    hold = tmp_path / "hold"
    hold.write_text(f"{p.HOLD_TEXT}\npid=4242 started=1\n")
    assert p.clear_stale_hold(tmp_path, pid_alive=lambda pid: True) is False
    assert hold.exists()


def test_dead_writer_hold_is_kept_while_a_wake_holds_the_dispatch_lock(tmp_path):
    hold = tmp_path / "hold"
    hold.write_text(f"{p.HOLD_TEXT}\npid=999999 started=1\n")
    handle = _hold_dispatch_lock(tmp_path)
    try:
        assert p.clear_stale_hold(tmp_path, pid_alive=lambda pid: False) is False
    finally:
        handle.close()
    assert hold.exists()


def test_legacy_hold_without_pid_is_kept_when_fresh_and_cleared_when_old(tmp_path):
    hold = tmp_path / "hold"
    hold.write_text(f"{p.HOLD_TEXT}\n")
    assert p.clear_stale_hold(tmp_path, now=lambda: hold.stat().st_mtime + 10) is False
    assert hold.exists()
    assert p.clear_stale_hold(tmp_path, now=lambda: hold.stat().st_mtime + 7200) is True
    assert not hold.exists()


def test_manual_hold_is_never_auto_cleared_even_when_old(tmp_path):
    hold = tmp_path / "hold"
    hold.write_text("freeze for deploy\n")
    assert p.clear_stale_hold(tmp_path, now=lambda: hold.stat().st_mtime + 7200) is False
    assert hold.exists()


def test_manual_hold_with_pid_like_text_is_never_auto_cleared(tmp_path):
    hold = tmp_path / "hold"
    hold.write_text("freeze for deploy pid=999999\n")
    assert p.clear_stale_hold(tmp_path, pid_alive=lambda pid: False,
                              now=lambda: hold.stat().st_mtime + 7200) is False
    assert hold.exists()


def test_held_lock_writes_the_writer_pid_into_the_hold(tmp_path):
    handle = _hold_dispatch_lock(tmp_path)
    seen = {}

    def sleep(_):
        seen["text"] = (tmp_path / "hold").read_text()
        handle.close()

    from mishe_tauftauf import wake
    mp = pytest.MonkeyPatch()
    mp.setattr(wake, "resume_owed", lambda *a, **k: None)
    mp.setattr("mishe_tauftauf.roles.declared", lambda home: [])
    try:
        with p.held_delivery_lock(tmp_path, 60, sleep=sleep, patience_s=0):
            pass
    finally:
        mp.undo()
    assert f"pid={os.getpid()} " in seen["text"]
    assert seen["text"].startswith(p.HOLD_TEXT)


def test_promote_does_not_overwrite_a_review_merged_after_its_last_save(tmp_path, tree, monkeypatch):
    # promote() used to rewrite the whole record after _promote returned and its delivery
    # lock closed, so a review poll's verdict merged in that gap was replaced by the stale
    # copy with review_pending still true.
    repo, home, old, sha = tree

    def merge_between(home_, record):
        path = next((home_ / "work/releases").glob("*.json"))
        current = read_json(path)
        current.update(review_pending=False, review=dict(verdict="OK"))
        write_json(path, current)
        return dict()

    monkeypatch.setattr(p, "publish_outcome", merge_between)
    r = p.promote(home, repo, sha, check=[sys.executable, "-c", "assert True"], observe=["probe"],
                  previous=old, activate=lambda *a: None, probe=lambda *a, **k: dict(code=0, output=""),
                  closure=lambda *a: None)
    assert r["phase"] == "delivered"
    on_disk = read_json(home / "work/releases" / f"{r['id']}.json")
    assert on_disk["review_pending"] is False
    assert on_disk["review"] == {"verdict": "OK"}


def test_promote_persists_signal_error_without_losing_a_merged_review(tmp_path, tree, monkeypatch):
    repo, home, old, sha = tree

    def fail_signal(home_, record):
        path = next((home_ / "work/releases").glob("*.json"))
        current = read_json(path)
        current.update(review_pending=False, review=dict(verdict="OK"))
        write_json(path, current)
        record["signal_error"] = "OSError: signal store down"
        return record

    monkeypatch.setattr(p, "publish_outcome", fail_signal)
    r = p.promote(home, repo, sha, check=[sys.executable, "-c", "assert True"], observe=["probe"],
                  previous=old, activate=lambda *a: None, probe=lambda *a, **k: dict(code=0, output=""),
                  closure=lambda *a: None)
    on_disk = read_json(home / "work/releases" / f"{r['id']}.json")
    assert on_disk["signal_error"] == "OSError: signal store down"
    assert on_disk["review_pending"] is False
    assert on_disk["review"] == {"verdict": "OK"}
