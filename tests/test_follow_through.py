from datetime import datetime, timedelta, timezone
import json

from mishe_tauftauf import follow_through, initiative, substrate_watch
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.wall import addressed


def snapshots(home, now):
    (home / "checks").mkdir(parents=True, exist_ok=True)
    substrate_watch.check(home, now=now)
    (home / "checks/initiative.json").write_text(json.dumps(
        initiative.collect(home, now=now, entries=[])))


def test_freshness_guard_routes_missing_then_detects_new_failure_after_recovery(tmp_path):
    now = datetime.now(timezone.utc)
    first = follow_through.health(tmp_path, now=now)
    assert first["state"] == "UNKNOWN"
    assert all(not row["fresh"] for row in first["producers"].values())
    assert addressed(Feed(tmp_path).entries()[-1], "health")
    sequence = first["alert_sequence"]
    assert follow_through.health(tmp_path, now=now)["alert_sequence"] == sequence
    snapshots(tmp_path, now)
    recovered = follow_through.health(tmp_path, now=now)
    assert recovered["state"] == "GREEN"
    # Fresh PARTIAL evidence is an observed gap, not a dead producer.
    assert recovered["producers"]["initiative"]["reported_state"] == "PARTIAL"
    stale = follow_through.health(tmp_path, now=now + timedelta(seconds=181))
    assert stale["state"] == "UNKNOWN"
    assert stale["alert_sequence"] > sequence
    assert addressed(Feed(tmp_path).entries()[-1], "health")


def test_broken_future_and_tampered_guard_cannot_look_healthy(tmp_path):
    now = datetime.now(timezone.utc)
    snapshots(tmp_path, now)
    future = follow_through.health(tmp_path, now=now - timedelta(seconds=1))
    assert future["state"] == "UNKNOWN"
    (tmp_path / "checks/initiative.json").write_text("{broken")
    assert follow_through.health(tmp_path, now=now)["state"] == "UNKNOWN"
    snapshots(tmp_path, now)
    guard = tmp_path / "checks/follow-through-health.json"
    guard.write_text("corrupt original")
    result = follow_through.health(tmp_path, now=now)
    assert result["state"] == "UNKNOWN"
    retained = list((tmp_path / "artifacts").glob("follow-through-guard-corrupt-*.json"))
    assert [path.read_text() for path in retained] == ["corrupt original"]
    assert follow_through.health(tmp_path, now=now)["state"] == "GREEN"


def test_fresh_three_field_snapshots_are_broken_not_healthy(tmp_path):
    now = datetime.now(timezone.utc)
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks/substrate-watch.json").write_text(json.dumps({
        "version": 1, "checked_at": now.isoformat(), "status": "KNOWN"}))
    (tmp_path / "checks/initiative.json").write_text(json.dumps({
        "version": 1, "generated_at": now.isoformat(), "state": "PARTIAL"}))
    row = follow_through.health(tmp_path, now=now)
    assert row["state"] == "UNKNOWN"
    assert all(not item["fresh"] for item in row["producers"].values())


def test_retention_failure_never_overwrites_corrupt_guard(tmp_path, monkeypatch):
    from pathlib import Path

    now = datetime.now(timezone.utc)
    snapshots(tmp_path, now)
    guard = tmp_path / "checks/follow-through-health.json"
    guard.write_text("original corrupt evidence")
    real_write = Path.write_bytes

    def no_archive(path, value):
        if path.parent == tmp_path / "artifacts":
            raise PermissionError("controlled archive failure")
        return real_write(path, value)

    monkeypatch.setattr(Path, "write_bytes", no_archive)
    first = follow_through.health(tmp_path, now=now)
    assert first["state"] == "UNKNOWN"
    assert guard.read_text() == "original corrupt evidence"
    assert "guard state retention failed" in first["problems"]
    again = follow_through.health(tmp_path, now=now)
    assert again["alert_sequence"] == first["alert_sequence"]
    assert guard.read_text() == "original corrupt evidence"


def test_relative_home_observes_the_same_valid_cache(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    home = tmp_path / "site"
    snapshots(home, now)
    monkeypatch.chdir(tmp_path)
    assert follow_through.health("site", now=now)["state"] == "GREEN"
    assert substrate_watch.snapshot_lines("site", now=now) == substrate_watch.snapshot_lines(home, now=now)
