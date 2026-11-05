from mishe_tauftauf import work_metrics as m
from mishe_tauftauf.state_files import write_json


def test_deduplicated_receipts_and_unknown_denominators(tmp_path):
    root = tmp_path / "wakes"
    write_json(root / "k.work-start.json", dict(wake_key="k", started_at=10))
    write_json(root / "k.work.json", dict(wake_key="k", started_at=10, ended_at=20,
        commits_observed=["human", "own"], commits_attributed=["own"], classification="commit-attributed",
        evidence_state="KNOWN", claimed_result="changed", dirty_start={}))
    write_json(root / "k.yield.json", dict(result="changed", settled_at=19))
    write_json(root / "legacy.snapshot.json", dict(role="docs", snapshot={}))
    r = m.report(tmp_path, start=0, end=30)
    assert r["wakes"] == 1
    assert r["observed_commits"] == ["human", "own"]
    assert r["attributed_commits"] == ["own"]
    assert r["commits_per_engine_hour"] is None
    assert r["missing_usage"] == 1
    assert r["check_to_commit_seconds"] is None


def test_missing_git_does_not_become_zero_commit_productivity(tmp_path):
    root=tmp_path/"wakes"
    write_json(root/"k.work-start.json",dict(wake_key="k",started_at=10))
    write_json(root/"k.work.json",dict(wake_key="k",started_at=10,ended_at=20,evidence_state="UNKNOWN",engine_seconds=10))
    assert m.report(tmp_path,start=0,end=30)["commits_per_engine_hour"] is None


def test_unreadable_start_keeps_denominator_unknown(tmp_path):
    root=tmp_path/"wakes"
    write_json(root/"k.work-start.json",dict(wake_key="k",started_at=10))
    write_json(root/"k.work.json",dict(wake_key="k",started_at=10,ended_at=20,evidence_state="KNOWN",engine_seconds=10))
    (root/"lost.work-start.json").write_text("{")
    result=m.report(tmp_path,start=0,end=30)
    assert result["commits_per_engine_hour"] is None
    assert result["missing_usage"]==1
    assert result["unknown_window_wakes"]==1


def test_bounded_fault_and_recovery_facts_are_reported(tmp_path):
    write_json(tmp_path/"work/experiment/manifest.json",dict(version=1,start=0,end=86400,fault_events_version=1,recovery_events_version=1))
    write_json(tmp_path/"work/fault-events/one.json",dict(at=10,novel=True))
    write_json(tmp_path/"work/fault-events/two.json",dict(at=11,novel=False))
    write_json(tmp_path/"work/releases/one.json",dict(created_at=10,rollback_at=11,rollback_completed_at=12,sha="own",phase="rolled-back",events=["rollback-started"]))
    r=m.report(tmp_path,start=0,end=30)
    assert r["fault_trigger_novelty"]["fresh"]==1 and r["fault_trigger_novelty"]["repeated"]==1
    assert r["reverts_rework"]["automatic_rollbacks"]==1


def test_delayed_checkpoint_reconstructs_delivery_before_later_revert(tmp_path):
    write_json(tmp_path/"work/experiment/manifest.json",dict(version=1,start=0,end=86400,recovery_events_version=1))
    write_json(tmp_path/"wakes/k.work-start.json",dict(wake_key="k",started_at=10))
    write_json(tmp_path/"wakes/k.work.json",dict(wake_key="k",started_at=10,ended_at=20,evidence_state="KNOWN",commits_attributed=["own"]))
    write_json(tmp_path/"work/releases/one.json",dict(created_at=4*3600,delivered_at=5*3600,
        revert_started_at=7*3600,reverted_at=7*3600+10,phase="reverted",sha="own",observed=True))
    before=m.report(tmp_path,start=0,end=6*3600)["reverts_rework"]
    assert before["delivered_autonomous_shas"]==["own"] and before["explicit_reverts"]==0
    after=m.report(tmp_path,start=0,end=8*3600)["reverts_rework"]
    assert after["delivered_autonomous_shas"]==[] and after["explicit_reverts"]==1


def test_later_completed_wake_does_not_leak_outcome_into_cutoff(tmp_path):
    write_json(tmp_path/"wakes/k.work-start.json",dict(wake_key="k",started_at=10))
    write_json(tmp_path/"wakes/k.work.json",dict(wake_key="k",started_at=10,ended_at=40,
        evidence_state="KNOWN",commits_attributed=["later"],engine_seconds=30,claimed_result="changed"))
    report=m.report(tmp_path,start=0,end=30)
    assert report["attributed_commits"]==[] and report["commits_per_engine_hour"] is None
    assert report["pending_at_cutoff"]==1
