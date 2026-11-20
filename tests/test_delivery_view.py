"""tools/delivery_view.py: the checked-to-live report keeps missing evidence explicit.

The report reads the promote path's own records, so the tests build synthetic ones: a delivered
promotion with every timestamp, a rolled-back one (which must name its failure and show only a
duration the record actually carries), and one whose timestamps are missing (UNKNOWN, never a zero).
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("delivery_view", REPO / "tools" / "delivery_view.py")
view = importlib.util.module_from_spec(spec)
spec.loader.exec_module(view)


def record(**over):
    base = {
        "created_at": 1000.0, "check": {"at": 2000.0, "code": 0},
        "waiting_since": 2010.0, "activation_started_at": 2600.0, "delivered_at": 2700.0,
        "reviewed_at": 2730.0, "phase": "delivered", "sha": "b" * 40, "previous": "a" * 40,
    }
    base.update(over)
    return base


def home_with(tmp_path, records):
    releases = tmp_path / "work" / "releases"
    releases.mkdir(parents=True)
    for i, rec in enumerate(records):
        (releases / f"{i}.json").write_text(json.dumps(rec), encoding="utf-8")
    return tmp_path


def test_stages_measures_each_stage_separately():
    s = view.stages(record())
    assert s == {"check_s": 1000.0, "wake_wait_s": 590.0, "drain_observe_s": 100.0,
                 "review_s": 30.0, "checked_to_live_s": 1700.0}


def test_stages_is_none_where_the_record_does_not_permit_the_measure():
    s = view.stages(record(waiting_since=None, activation_started_at=None, reviewed_at=None))
    assert s["check_s"] == 1000.0
    assert s["wake_wait_s"] is None and s["drain_observe_s"] is None
    assert s["review_s"] is None
    assert s["checked_to_live_s"] == 1700.0


def test_missing_timestamps_never_become_zeros_in_the_summary():
    rows = [record(), record(waiting_since=None)]
    out = view.summarize(rows)
    assert out["wake_wait_s"]["n"] == 1
    assert out["review_s"]["n"] == 2


def test_p90_is_nearest_rank_not_interpolated():
    rows = [record(waiting_since=2000.0 + i, activation_started_at=2000.0 + i + 10)
            for i in range(10)]
    out = view.summarize(rows)
    assert out["wake_wait_s"]["max"] == 10.0
    assert out["wake_wait_s"]["p90"] == 10.0
    assert out["wake_wait_s"]["mean"] == 10.0


def test_legacy_unknown_recovery_keeps_its_reason_and_stage_unknown(monkeypatch, tmp_path):
    rolled = record(phase="UNKNOWN", delivered_at=None, reviewed_at=None,
                    failure="functional probe failed: old consumer invocations remain",
                    activation_started_at=2600.0)
    home = home_with(tmp_path, [record(), rolled])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))  # no waiting commits: unknown pin range
    text = view.render(home, Path("/nope"), 24 * 3600.0, 4000.0)
    assert "old consumer invocations remain" in text
    assert "UNKNOWN (recovery stage unrecorded)" in text
    assert "rollback duration UNKNOWN" in text
    assert "drain + functional observation" in text          # the delivered run still reports its stages
    assert "UNKNOWN: no pin" not in text                      # the pin itself resolved


def test_a_rolled_back_run_shows_only_the_recorded_duration(monkeypatch, tmp_path):
    rolled = record(phase="rolled-back", delivered_at=None, reviewed_at=None,
                    failure="functional probe failed: old consumer invocations remain",
                    activation_started_at=2600.0, rollback_at=2700.0, rollback_completed_at=2790.0)
    home = home_with(tmp_path, [record(), rolled])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))
    text = view.render(home, Path("/nope"), 24 * 3600.0, 4000.0)
    assert f"rolled back {'b' * 8}" in text
    assert "rollback 1m30s (completed)" in text
    assert "completed rollback time mean 1m30s max 1m30s  (n=1)" in text
    assert "not recorded" not in text


def test_a_failed_rollback_probe_never_claims_the_pin_was_restored(monkeypatch, tmp_path):
    rolled = record(phase="UNKNOWN", delivered_at=None, reviewed_at=None,
                    failure="pin observation failed", activation_started_at=2600.0,
                    rollback_at=2700.0, rollback_failed_at=2760.0, rollback_probe={"code": 1})
    home = home_with(tmp_path, [record(), rolled])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))
    text = view.render(home, Path("/nope"), 24 * 3600.0, 4000.0)
    assert "UNKNOWN (rollback probe failed)" in text
    assert "rollback 1m00s (probe failed)" in text
    assert "failed-probe rollback time mean 1m00s max 1m00s  (n=1)" in text
    assert "completed rollback time" not in text


def test_restore_error_reports_the_stage_before_the_rollback_probe(tmp_path):
    rolled = record(phase="UNKNOWN", delivered_at=None, reviewed_at=None,
                    failure="pin observation failed", rollback_at=2700.0,
                    rollback_failed_at=2760.0, rollback_error="unit configuration could not be restored")
    home = home_with(tmp_path, [rolled])
    text = view.render(home, REPO, 24 * 3600.0, 4000.0)
    assert "UNKNOWN (rollback restore failed)" in text
    assert "unit configuration could not be restored" in text
    assert "rollback 1m00s (restore failed)" in text
    assert "failed-probe rollback time" not in text


def test_failed_explicit_revert_is_distinct_from_automatic_rollback(tmp_path):
    reverted = record(phase="UNKNOWN", revert_started_at=3300.0,
                      revert_failed_at=3400.0, revert_probe={"code": 1})
    home = home_with(tmp_path, [reverted])
    text = view.render(home, REPO, 24 * 3600.0, 4000.0)
    assert f"UNKNOWN (explicit revert failed) {'b' * 8} after {view.fmt_ts(3300.0)}" in text
    assert "rollback probe failed" not in text
    assert "rollback duration" not in text


def test_invalid_rollback_timestamp_keeps_duration_unknown(tmp_path):
    rolled = record(phase="UNKNOWN", rollback_at=2700.0, rollback_failed_at="unreadable",
                    rollback_probe={"code": 1})
    home = home_with(tmp_path, [rolled])
    text = view.render(home, REPO, 24 * 3600.0, 4000.0)
    assert "UNKNOWN (rollback probe failed)" in text
    assert "rollback duration UNKNOWN" in text
    assert "failed-probe rollback time" not in text


def test_torn_activation_timestamps_report_unknown_not_a_crash(tmp_path):
    torn = record(phase="UNKNOWN", activation_started_at="unreadable", rollback_probe={"code": 1})
    infinite = record(phase="rolled-back", activation_started_at=float("inf"),
                      rollback_at=2700.0, rollback_completed_at=2790.0)
    home = home_with(tmp_path, [torn, infinite])
    text = view.render(home, REPO, 24 * 3600.0, 4000.0)
    assert text.count("after UNKNOWN") == 2
    assert "rollback 1m30s (completed)" in text      # the rolled-back run's duration still reports


def test_unreadable_records_report_unknown_not_a_series(tmp_path):
    releases = tmp_path / "work" / "releases"
    releases.mkdir(parents=True)
    (releases / "torn.json").write_text("{not json", encoding="utf-8")
    text = view.render(tmp_path, Path("/nope"), 24 * 3600.0, 4000.0)
    assert "UNKNOWN: no readable promotion records" in text


def test_oldest_waiting_commit_and_the_in_flight_run(monkeypatch, tmp_path):
    home = home_with(tmp_path, [record(phase="waiting-for-wakes", sha="d" * 40,
                                       created_at=3000.0, delivered_at=None, reviewed_at=None,
                                       activation_started_at=None, waiting_since=None)])
    (home / "runtime").mkdir()
    os.symlink(f"{home}/releases/{'c' * 40}", home / "runtime" / "current")

    def fake_git(repo, *args):
        assert args[0] == "log" and args[-1] == "cccccccccccccccccccccccccccccccccccccccc..HEAD", args
        return 0, "eeee0000000000000000000000000000000000001;3500;oldest waiting\n" \
                  "eeee0000000000000000000000000000000000002;3600;newest waiting\n"

    monkeypatch.setattr(view, "git", fake_git)
    text = view.render(home, Path("/nope"), 24 * 3600.0, 4000.0)
    assert "oldest checked fix still waiting: eeee0000" in text
    assert '"oldest waiting"' in text
    assert "aged 8m20s; 2 commit(s) on HEAD not on the pin" in text
    assert "in flight: dddddddd phase waiting-for-wakes" in text



def test_a_dead_unfinished_run_is_not_reported_as_in_flight(tmp_path):
    stale_s = view.LIVE_MAX_AGE_S + 1.0
    home = home_with(tmp_path, [record(phase="waiting-for-wakes", sha="d" * 40,
                                       created_at=4000.0 - stale_s, delivered_at=None,
                                       reviewed_at=None, activation_started_at=None,
                                       waiting_since=None)])
    text = view.render(home, Path("/nope"), 24 * 3600.0, 4000.0)
    assert "in flight: none" in text
    assert "abandoned: dddddddd phase waiting-for-wakes" in text
    assert "is not trusted as live" in text
    assert "activation is held until wakes end" not in text


def test_an_unfinished_run_at_the_live_bound_is_still_in_flight(tmp_path):
    at_bound = view.LIVE_MAX_AGE_S
    home = home_with(tmp_path, [record(phase="waiting-for-wakes", sha="d" * 40,
                                       created_at=4000.0 - at_bound, delivered_at=None,
                                       reviewed_at=None, activation_started_at=None,
                                       waiting_since=None)])
    text = view.render(home, Path("/nope"), 24 * 3600.0, 4000.0)
    assert "in flight: dddddddd phase waiting-for-wakes" in text
    assert "abandoned" not in text


def test_a_live_run_hides_stale_dead_ones_from_the_in_flight_line(tmp_path):
    home = home_with(tmp_path, [
        record(phase="waiting-for-wakes", sha="d" * 40, created_at=4000.0 - 4000.0,
               delivered_at=None, reviewed_at=None, activation_started_at=None, waiting_since=None),
        record(phase="activating", sha="e" * 40, created_at=3900.0,
               delivered_at=None, reviewed_at=None, activation_started_at=None),
    ])
    text = view.render(home, Path("/nope"), 24 * 3600.0, 4000.0)
    assert "in flight: eeeeeeee phase activating" in text
    assert "abandoned" not in text


def test_commit_waits_use_each_commits_own_time(monkeypatch, tmp_path):
    delivered = [record(created_at=5000.0, sha="d" * 40, previous="c" * 40)]
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (0, "e" * 40 + ";4000;first\n" + "f" * 40 + ";4600;second\n"))
    assert sorted(view.commit_waits(delivered, Path("/nope"))) == [400.0, 1000.0]


def test_commit_waits_skip_records_without_a_previous_release(monkeypatch):
    monkeypatch.setattr(view, "git", lambda *a, **k: (0, ""))
    assert view.commit_waits([record(previous=None)], Path("/nope")) == []
