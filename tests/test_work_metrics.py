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
