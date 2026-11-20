"""tools/delivery_view.py: the checked-to-live report keeps missing evidence explicit.

The report reads the promote path's own records, so the tests build synthetic ones: a delivered
promotion with every timestamp, a rolled-back one (which must name its failure and show only a
duration the record actually carries), and one whose timestamps are missing (UNKNOWN, never a zero).
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

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


def test_delivery_busy_refusals_are_accounted_not_silently_dropped(tmp_path):
    home = home_with(tmp_path, [
        record(phase="delivered", created_at=1000.0),
        record(phase="delivery-busy", created_at=2000.0, sha="c" * 40,
               failure="delivery admission is owned by another request"),
    ])
    text = view.render(home, Path("/nope"), 86400.0, 6000.0)
    assert "promotions in window: 2 delivered 1 rolled-back/UNKNOWN 0 delivery-busy 1" in text
    assert "no-effect delivery-busy refusal (newest): cccccccc" in text


def test_p90_is_nearest_rank_not_interpolated():
    rows = [record(waiting_since=2000.0 + i, activation_started_at=2000.0 + i + 10)
            for i in range(10)]
    out = view.summarize(rows)
    assert out["wake_wait_s"]["max"] == 10.0
    assert out["wake_wait_s"]["p90"] == 10.0
    assert out["wake_wait_s"]["mean"] == 10.0


def test_fence_modes_split_quiet_polled_and_hold_waits(monkeypatch, tmp_path):
    # held_delivery_lock retries on a 1 s sleep, so a first-try gap crosses the two record
    # saves in ~20 ms, never exactly zero. A fence at patience_s=600 means the hold was written.
    quick = [record(waiting_since=t, activation_started_at=t + d)
             for t, d in ((2010.0, 0.02), (2020.0, 0.05))]
    polled = record(waiting_since=2030.0, activation_started_at=2050.0)   # 20 s
    held = record(waiting_since=2060.0, activation_started_at=2660.0)    # 600 s
    unmeasured = record(waiting_since=None, activation_started_at=None)
    home = home_with(tmp_path, quick + [polled, held, unmeasured])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))
    text = view.render(home, Path("/nope"), 24 * 3600.0, 4000.0)
    # The unmeasured run is absent, never a zero-second fence, so it stays out of the count.
    assert "fence modes: 2/4 quiet gaps (<1s), 1 polled (gap inside quiet poll mean 0m20s)," in text
    assert "1 hold-waits (hold written mean 10m00s max 10m00s)" in text
    assert "release the shared dispatch lock" in text


def test_fence_modes_boundary_at_patience():
    modes = view.fence_modes([
        record(waiting_since=2000.0, activation_started_at=2599.0),  # just under 600 s
        record(waiting_since=3000.0, activation_started_at=3600.0),  # exactly 600 s
    ])
    assert modes == {"quiet": [], "polled": [599.0], "hold": [600.0]}


def test_fence_modes_use_each_records_patience_not_one_constant():
    modes = view.fence_modes([
        record(quiet_poll_s=120, waiting_since=2000.0, activation_started_at=2119.0),
        record(quiet_poll_s=120, waiting_since=3000.0, activation_started_at=3120.0),
        record(waiting_since=4000.0, activation_started_at=4590.0),  # legacy 600 s patience
    ])
    assert modes == {"quiet": [], "polled": [119.0, 590.0], "hold": [120.0]}


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


def test_out_of_range_activation_timestamps_keep_recovery_evidence(tmp_path):
    out_of_range = [
        record(phase="rolled-back", activation_started_at=epoch,
               rollback_at=2700.0, rollback_completed_at=2790.0)
        for epoch in (1e17, 1e100)
    ]
    text = view.render(home_with(tmp_path, out_of_range), REPO, 24 * 3600.0, 4000.0)
    assert text.count("after UNKNOWN") == 2
    assert text.count("rollback 1m30s (completed)") == 2


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


def test_promotion_gap_measures_finish_to_next_start():
    rows = [record(created_at=1000.0, delivered_at=1500.0),
            record(created_at=2000.0, delivered_at=2400.0)]
    assert view.promotion_gaps(rows) == [500.0]


def test_promotion_gap_ignores_torn_finish_and_keeps_start_order():
    rows = [record(created_at=3000.0, delivered_at=3100.0),
            record(created_at=1000.0, delivered_at=None),
            record(created_at=2000.0, delivered_at=2100.0)]
    assert view.promotion_gaps(rows) == [900.0]


def test_report_names_the_delivered_endpoints_even_with_an_intervening_failed_check(tmp_path):
    home = home_with(tmp_path, [
        record(),
        record(created_at=3500.0, phase="check-failed"),
        record(created_at=5000.0, delivered_at=5500.0),
    ])
    text = view.render(home, Path("/nope"), 86400.0, 6000.0)
    gap = next(line.split() for line in text.splitlines() if "finish ->" in line)
    assert gap == ("delivered finish -> next delivered start "
                   "mean 38m20s p90 38m20s max 38m20s (n=1)").split()


def test_promotion_gap_is_unknown_without_a_pair(tmp_path):
    home = home_with(tmp_path, [record()])
    text = view.render(home, Path("/nope"), 86400.0, 6000.0)
    assert "delivered finish -> next delivered start  UNKNOWN" in text


def test_recent_stages_shows_a_regime_shift_next_to_the_lagging_window_mean():
    old = [record(created_at=1000.0 + i * 100, check={"at": 1960.0 + i * 100}) for i in range(10)]
    new = [record(created_at=3000.0 + i * 100, check={"at": 3500.0 + i * 100}) for i in range(10)]
    line = view.recent_stages(old + new)
    assert "newest 10 delivered" in line
    assert "check 8m20s (window 12m10s)" in line


def test_recent_stages_never_turns_a_missing_stage_into_zero():
    line = view.recent_stages([record(check=None) for _ in range(3)])
    assert "check UNKNOWN" in line


def test_report_prints_recent_stages_beside_the_window_table(tmp_path):
    home = home_with(tmp_path, [record()])
    text = view.render(home, Path("/nope"), 86400.0, 6000.0)
    assert "newest 1 delivered" in text
    assert "check 16m40s (window 16m40s)" in text


def test_hold_wait_overlap_is_measured_from_the_routing_ledger(tmp_path, monkeypatch):
    held = record(waiting_since=2010.0, activation_started_at=2610.0)
    home = home_with(tmp_path, [held])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    (tmp_path / "engine").mkdir()
    stamps = []
    for event, at in (("select", 2000.0), ("outcome", 2610.0)):
        stamps.append(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)),
                                  "event": event, "wake": "w1"}))
    (tmp_path / "engine" / "routing-ledger.jsonl").write_text("\n".join(stamps) + "\n", encoding="utf-8")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))
    text = view.render(home, Path("/nope"), 86400.0, 4000.0)
    assert "hold-wait/wake overlap: 1 hold-waits, mean 100.0% of each fence" in text


def test_poll_wake_starts_counts_wake_starts_inside_the_poll_only():
    # fence [2010, 3010): poll [2010, 2610), hold [2610, 3010). 2000 predates the fence.
    held = record(waiting_since=2010.0, activation_started_at=3010.0)
    out = view.poll_wake_starts([held], [2000.0, 2100.0, 2609.0, 2610.0, 2800.0])
    assert out == {"holds": 1, "poll_mean": 2.0, "poll_max": 2,
                   "after_mean": 2.0, "with_poll_start": 1}


def test_poll_wake_starts_is_unknown_without_a_hold_wait():
    quick = record(waiting_since=2010.0, activation_started_at=2400.0)  # 390 s < patience
    assert view.poll_wake_starts([quick], [2100.0]) is None


def test_poll_wake_starts_uses_each_records_patience_window():
    # New-era fence [2010, 2210): quiet poll [2010, 2130), hold [2130, 2210).
    held = record(quiet_poll_s=120, waiting_since=2010.0, activation_started_at=2210.0)
    out = view.poll_wake_starts([held], [2000.0, 2100.0, 2129.0, 2130.0, 2200.0])
    assert out == {"holds": 1, "poll_mean": 2.0, "poll_max": 2,
                   "after_mean": 2.0, "with_poll_start": 1}


def test_report_prints_poll_starts_beside_the_hold_overlap(tmp_path, monkeypatch):
    held = record(waiting_since=2010.0, activation_started_at=3010.0)
    home = home_with(tmp_path, [held])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    (tmp_path / "engine").mkdir()
    stamps = []
    for wake, at in (("w1", 2100.0), ("w2", 2800.0)):
        for event in ("select", "outcome"):
            stamps.append(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)),
                                      "event": event, "wake": wake}))
    (tmp_path / "engine" / "routing-ledger.jsonl").write_text("\n".join(stamps) + "\n", encoding="utf-8")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))
    text = view.render(home, Path("/nope"), 86400.0, 4000.0)
    assert ("wake starts in the quiet poll (600s): mean 1.00/hold-wait (max 1),"
            " 1/1 fences had >=1; after hold 1.00") in text


def test_poll_wake_starts_restricts_to_evidenced_windows():
    held = record(waiting_since=2010.0, activation_started_at=3010.0)
    # A fence the ledger does not cover must not fold its zero starts into the mean.
    out = view.poll_wake_starts([held], [2100.0], [(2010.0, 3010.0, 600.0)])
    assert out == {"holds": 1, "poll_mean": 1.0, "poll_max": 1,
                   "after_mean": 0.0, "with_poll_start": 1}
    assert view.poll_wake_starts([held], [2100.0], []) is None


def test_report_keeps_positive_evidence_when_another_fence_is_uncovered(tmp_path, monkeypatch):
    covered = record(waiting_since=2010.0, activation_started_at=2610.0)
    gap = record(waiting_since=4010.0, activation_started_at=4610.0, sha="d" * 40)
    home = home_with(tmp_path, [covered, gap])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    (tmp_path / "engine").mkdir()
    stamps = []
    for wake, start, end in (("w1", 2000.0, 2610.0), ("w2", 2100.0, 2610.0)):
        for event, at in (("select", start), ("outcome", end)):
            stamps.append(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)),
                                      "event": event, "wake": wake}))
    (tmp_path / "engine" / "routing-ledger.jsonl").write_text("\n".join(stamps) + "\n", encoding="utf-8")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))
    text = view.render(home, Path("/nope"), 86400.0, 6000.0)
    assert "hold-wait/wake overlap: 1/2 hold-waits, mean 100.0% of each fence" in text
    assert "1 hold-wait fence(s) UNKNOWN: no wake interval inside (0 torn), excluded" in text
    assert ("1/1 fences had >=1; after hold 0.00;"
            " 1 fence(s) UNKNOWN (no ledger interval or start inside), excluded") in text
    assert "mean 50.0%" not in text


def test_report_keeps_already_running_wake_zero_starts_in_the_poll_mean(tmp_path, monkeypatch):
    held = record(waiting_since=2010.0, activation_started_at=2610.0)
    home = home_with(tmp_path, [held])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    (tmp_path / "engine").mkdir()
    stamps = []
    for event, at in (("select", 2000.0), ("outcome", 2610.0)):
        stamps.append(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)),
                                  "event": event, "wake": "w1"}))
    (tmp_path / "engine" / "routing-ledger.jsonl").write_text("\n".join(stamps) + "\n", encoding="utf-8")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))
    text = view.render(home, Path("/nope"), 86400.0, 4000.0)
    assert "hold-wait/wake overlap: 1 hold-waits, mean 100.0% of each fence" in text
    # The lock was held by a wake that started before the fence: zero starts is a real
    # observation, not a ledger gap, so it stays in the mean and needs no UNKNOWN suffix.
    assert ("wake starts in the quiet poll (600s): mean 0.00/hold-wait (max 0),"
            " 0/1 fences had >=1; after hold 0.00") in text


@pytest.mark.parametrize("paired_wake", [False, True])
def test_report_counts_recorded_starts_without_outcomes(tmp_path, monkeypatch, paired_wake):
    held = record(waiting_since=2010.0, activation_started_at=3010.0)
    home = home_with(tmp_path, [held])
    ledger = home / "engine" / "routing-ledger.jsonl"
    ledger.parent.mkdir()
    rows = [("select", "interrupted", 2010), ("select", "interrupted", 2700)]
    if paired_wake:
        rows = [("select", "running", 2000), ("outcome", "running", 2210)] + rows
    ledger.write_text("\n".join(
        json.dumps({"event": event, "wake": wake,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))})
        for event, wake, at in sorted(rows, key=lambda row: row[2])
    ) + "\n", encoding="utf-8")
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))

    text = view.render(home, Path("/nope"), 86400.0, 4000.0)

    assert ("wake starts in the quiet poll (600s): mean 1.00/hold-wait (max 1),"
            " 1/1 fences had >=1; after hold 0.00") in text
    if paired_wake:
        assert "hold-wait/wake overlap: 1 hold-waits, mean 20.0%" in text
    else:
        assert "hold-wait/wake overlap UNKNOWN (no wake interval covers a measured fence)" in text


def test_quiet_poll_s_rejects_boolean_patience():
    assert view.quiet_poll_s({}) == view.FENCE_HOLD_S
    assert view.quiet_poll_s({"quiet_poll_s": 120}) == 120.0
    assert view.quiet_poll_s({"quiet_poll_s": True}) == view.FENCE_HOLD_S


def test_report_does_not_claim_zero_overlap_without_ledger_evidence(tmp_path, monkeypatch):
    held = record(waiting_since=2010.0, activation_started_at=3010.0)
    home = home_with(tmp_path, [held])
    (tmp_path / "runtime").mkdir()
    os.symlink(f"{tmp_path}/releases/{'c' * 40}", tmp_path / "runtime" / "current")
    (tmp_path / "engine").mkdir()
    ledger = tmp_path / "engine" / "routing-ledger.jsonl"
    monkeypatch.setattr(view, "git", lambda repo, *a, **k: (1, ""))
    stale = json.dumps({"at": "1970-01-01T00:16:40Z", "event": "select", "wake": "old"})
    for content in ("", stale + "\n"):
        ledger.write_text(content, encoding="utf-8")
        text = view.render(home, Path("/nope"), 86400.0, 4000.0)
        assert "hold-wait/wake overlap UNKNOWN (no wake interval covers a measured fence)" in text
        assert "wake starts in the quiet poll UNKNOWN (same missing ledger evidence)" in text
        assert "after hold 0.00" not in text
