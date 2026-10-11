from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mishe_tauftauf import discovery
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.seed_culture_views import _observation_reading, discover, senses

NOW = 1791368000.0


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def boundary():
    return {"schema_version": 1, "producer": discovery.SPACE_PRODUCER.copy(),
            "generated_at_utc": stamp(NOW), "nodes": [{
                "node": "note3", "source": discovery.SPACE_SOURCE, "session": "session-a",
                "cursor": 12, "backlog_page_pending": False, "transport": "reachable",
                "last_error": None, "last_poll_utc": stamp(NOW), "last_light_transition": None,
                "light": {"status": "fresh-clock-conditional", "validity_seconds": 30,
                          "units": "lux", "delayed_at_receipt": False,
                          "sequence": 12, "lux": 14.431, "age_ms_at_phone_collection": 169,
                          "phone_sample_epoch_s": NOW - 20, "consumer_receipt_epoch_s": NOW - 2,
                          "phone_sample_utc": "2000-01-01T00:00:00Z",
                          "consumer_receipt_utc": "2000-01-01T00:00:00Z"}}]}


def event_data():
    data = boundary()
    node = data["nodes"][0]
    node["light"].update(sequence=14, lux=31, phone_sample_epoch_s=NOW - 1,
                          consumer_receipt_epoch_s=NOW)
    node["cursor"] = 14
    node["last_light_transition"] = {
        "event_id": "note3:session-a:14", "kind": "measured_light_bucket_transition",
        "source": node["source"], "session": node["session"], "validity_seconds": 30,
        "bucket_from": 3, "bucket_to": 5,
        "from": {"sequence": 13, "lux": 14.431, "phone_sample_epoch_s": NOW - 2,
                 "consumer_receipt_epoch_s": NOW - 1},
        "to": {"sequence": 14, "lux": 31, "phone_sample_epoch_s": NOW - 1,
               "consumer_receipt_epoch_s": NOW}}
    return data


def test_original_expiry_survives_publication_polling_and_rendering():
    data = boundary()
    data["generated_at_utc"] = stamp(NOW + 10)
    data["nodes"][0]["last_poll_utc"] = stamp(NOW + 10)
    at_expiry = discovery._space_light(data, NOW + 10)
    assert at_expiry["current_lux"] == 14.431
    assert at_expiry["expires_epoch_s"] == NOW + 10
    expired = discovery._space_light(data, NOW + 10.001)
    assert expired["state"] == "unknown"
    assert expired["current_lux"] is None
    assert expired["reason"] == "stale-original-evidence"
    with patch("mishe_tauftauf.seed_culture_views.datetime") as clock:
        clock.now.return_value.timestamp.return_value = NOW + 10.001
        state, line = _observation_reading(at_expiry, "recent")
    assert state == "unknown"
    assert "stale-original-evidence" in line
    assert "14.431lux" not in line


def test_validated_epochs_not_independent_strings_are_visible():
    row = discovery._space_light(boundary(), NOW)
    assert row["current_lux"] == 14.431
    assert "2000-01-01" not in row["sample"]
    assert "fresh-clock-conditional" in row["sample"]
    assert stamp(NOW - 20) in row["sample"]


@pytest.mark.parametrize("validity", [1, 0, -1, None, True, float("nan"), float("inf")])
def test_short_or_invalid_producer_validity_cannot_be_extended(validity):
    data = boundary()
    data["nodes"][0]["light"]["validity_seconds"] = validity
    row = discovery._space_light(data, NOW)
    assert row["state"] == "unknown"
    assert row["current_lux"] is None


def test_core_caps_longer_validity():
    data = boundary()
    data["nodes"][0]["light"]["validity_seconds"] = 300
    assert discovery._space_light(data, NOW + 10.001)["state"] == "unknown"


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(nodes=[]),
    lambda d: d["nodes"].append(copy.deepcopy(d["nodes"][0])),
    lambda d: d.update(producer={}),
    lambda d: d["nodes"][0].update(source="http://other/node/note3"),
    lambda d: d["nodes"][0].update(session=None),
    lambda d: d["nodes"][0].update(backlog_page_pending=True),
    lambda d: d["nodes"][0].update(transport="unknown/consumer-not-refreshing"),
    lambda d: d["nodes"][0].update(last_error="failed"),
    lambda d: d["nodes"][0].update(last_poll_utc=stamp(NOW - 31)),
    lambda d: d.update(generated_at_utc=stamp(NOW - 31)),
    lambda d: d["nodes"][0]["light"].update(delayed_at_receipt=True),
    lambda d: d["nodes"][0]["light"].update(units="unknown"),
    lambda d: d["nodes"][0]["light"].update(lux=float("nan")),
    lambda d: d["nodes"][0]["light"].update(sequence=True),
    lambda d: d["nodes"][0].update(cursor=11),
    lambda d: d["nodes"][0]["light"].update(phone_sample_epoch_s=NOW + 1),
    lambda d: d["nodes"][0]["light"].update(age_ms_at_phone_collection=31000),
])
def test_invalid_or_uncovered_evidence_never_reports_current_lux(mutation):
    data = boundary()
    mutation(data)
    row = discovery._space_light(data, NOW)
    assert row["state"] == "unknown"
    assert row["current_lux"] is None
    assert row["event_id"] is None


@pytest.mark.parametrize("mutation", [
    lambda e: e.update(source="other"),
    lambda e: e.update(session="other"),
    lambda e: e.update(event_id="forged"),
    lambda e: e["from"].update(sequence=14),
    lambda e: e["to"].update(sequence=15),
    lambda e: e["from"].update(phone_sample_epoch_s=NOW - 31),
    lambda e: e["to"].update(consumer_receipt_epoch_s=NOW + 1),
    lambda e: e.update(bucket_from=5),
    lambda e: e.update(validity_seconds=1),
])
def test_bad_history_does_not_invalidate_current_light_or_emit_event(mutation):
    data = event_data()
    mutation(data["nodes"][0]["last_light_transition"])
    row = discovery._space_light(data, NOW)
    assert row["current_lux"] == 31
    assert row["event_id"] is None
    assert row["event_reason"] == "invalid-or-expired-historical-transition"


def notify(home, current, previous=None, now=NOW):
    with patch("mishe_tauftauf.discovery.time.time", return_value=now):
        discovery._notify_scan(home, {"created": stamp(now), "node": "test",
                                     "observations": [current]},
                               {"observations": [previous]} if previous else None,
                               home / "sample.json")


def test_new_event_not_replayed_by_baseline_session_recovery_or_same_event(tmp_path: Path):
    baseline = discovery._space_light(boundary(), NOW)
    event = discovery._space_light(event_data(), NOW)
    notify(tmp_path, baseline)
    notify(tmp_path, event, baseline)
    assert "historical measured light change" in Feed(tmp_path).entries()[-1].body
    notify(tmp_path, event, event)
    assert len(Feed(tmp_path).entries()) == 2
    # Availability recovery must not replay retained transitions.
    offline = {**event, "state": "unknown", "reason": "transport-not-current"}
    notify(tmp_path, event, offline)
    assert "not a physical transition" in Feed(tmp_path).entries()[-1].body
    restarted = copy.deepcopy(event_data())
    restarted["nodes"][0]["session"] = "session-b"
    restarted["nodes"][0]["last_light_transition"].update(
        session="session-b", event_id="note3:session-b:14")
    notify(tmp_path, discovery._space_light(restarted, NOW), event)
    assert "not a physical transition" in Feed(tmp_path).entries()[-1].body
    # A baseline containing history is a baseline, not an event notification.
    initial = tmp_path / "initial"
    initial.mkdir()
    notify(initial, event)
    assert "Initial baseline" in Feed(initial).entries()[-1].body
    assert "historical measured light change" not in Feed(initial).entries()[-1].body


def test_notification_rechecks_original_expiry(tmp_path: Path):
    row = discovery._space_light(boundary(), NOW)
    notify(tmp_path, row, row, NOW + 10.001)
    text = Feed(tmp_path).entries()[-1].body
    assert "stale-original-evidence" in text
    assert "14.431lux" not in text


def test_missing_and_corrupt_file_are_unknown(tmp_path: Path):
    assert discovery._space_light_read(tmp_path)["state"] == "unknown"
    (tmp_path / "body").mkdir()
    (tmp_path / "body" / "space.json").write_text("{broken")
    assert discovery._space_light_read(tmp_path)["reason"] == "boundary-absent-or-corrupt"


def test_stale_unknown_names_last_phone_sample_and_age():
    data = boundary()
    data["nodes"][0]["light"]["status"] = "stale"
    row = discovery._space_light(data, NOW + 7200)
    assert row["state"] == "unknown"
    assert row["reason"] == "light-not-current-or-delayed"
    assert "last-phone=" in row["sample"] and stamp(NOW - 20) in row["sample"]
    assert "age=2h0m" in row["sample"]


def test_delayed_unknown_names_age():
    data = boundary()
    data["nodes"][0]["light"]["delayed_at_receipt"] = True
    row = discovery._space_light(data, NOW + 5)
    assert row["reason"] == "light-not-current-or-delayed"
    assert "age=25s" in row["sample"]


def test_expired_fresh_claim_names_age():
    data = boundary()
    row = discovery._space_light(data, NOW + 31)
    assert row["reason"] == "stale-original-evidence"
    assert "last-phone=" in row["sample"] and stamp(NOW - 20) in row["sample"]


def test_unknown_without_valid_phone_epoch_omits_age():
    data = boundary()
    data["nodes"][0]["light"].update(status="stale", phone_sample_epoch_s=None)
    row = discovery._space_light(data, NOW + 5)
    assert row["reason"] == "light-not-current-or-delayed"
    assert "last-phone=" not in row["sample"]


def test_published_sample_without_lux_names_a_null_gap_not_a_corrupt_file():
    data = boundary()
    data["nodes"][0]["light"]["lux"] = None
    row = discovery._space_light(data, NOW)
    assert row["state"] == "unknown"
    assert row["reason"] == "light-null-gap"
    assert row["current_lux"] is None
    assert "last-phone=" in row["sample"] and stamp(NOW - 20) in row["sample"]
    assert row["sample"].startswith("Note3 light UNKNOWN: light-null-gap")


def test_null_lux_gap_outranks_the_delayed_status_flag():
    data = boundary()
    data["nodes"][0]["light"].update(lux=None, status="stale", delayed_at_receipt=True)
    row = discovery._space_light(data, NOW)
    # A value the source published as absent is a coverage gap; naming it beats
    # letting the sample-age flag describe it, so a dropout reads as itself.
    assert row["reason"] == "light-null-gap"


def null_gap_fixture(home: Path, record: dict | None) -> Path:
    """A site whose boundary publishes a null lux sample under the reader."""
    data = boundary()
    data["nodes"][0]["light"].update(lux=None, status="stale")
    (home / "body").mkdir(parents=True, exist_ok=True)
    (home / "body" / "space.json").write_text(json.dumps(data), encoding="utf-8")
    if record is not None:
        (home / "discovery").mkdir(parents=True, exist_ok=True)
        (home / "discovery" / "space-light-stream.json").write_text(
            json.dumps(record), encoding="utf-8")
    return home


def null_record(**overrides) -> dict:
    record = {"session": "session-a", "cursor": 12, "sequence": 12,
              "reason": "light-null-gap", "read_epoch_s": time.time() - 120}
    record.update(overrides)
    return record


def test_null_sample_persisting_across_reads_is_named_a_frozen_stream(tmp_path):
    null_gap_fixture(tmp_path, null_record())
    row = discovery._space_light_read(tmp_path)
    # Same session, cursor and sequence across reads long enough that a
    # seconds-wide dropout would have resolved: the producer stopped publishing.
    assert row["reason"] == "light-stream-frozen"
    assert row["sample"].startswith("Note3 light UNKNOWN: light-stream-frozen")
    # The gap's age detail survives the rename; a freeze still says how stale.
    assert "last-phone=" in row["sample"] and "age=" in row["sample"]


def test_null_sample_first_seen_stays_a_single_read_gap(tmp_path):
    null_gap_fixture(tmp_path, None)
    row = discovery._space_light_read(tmp_path)
    assert row["reason"] == "light-null-gap"
    # The observation is retained so the next read can judge the duration class.
    kept = json.loads((tmp_path / "discovery" / "space-light-stream.json").read_text())
    assert kept["reason"] == "light-null-gap" and kept["cursor"] == 12


def test_advancing_cursor_between_reads_stays_a_dropout(tmp_path):
    null_gap_fixture(tmp_path, null_record(cursor=11))
    row = discovery._space_light_read(tmp_path)
    # The boundary kept receiving frames between the reads: a momentary gap.
    assert row["reason"] == "light-null-gap"


def test_new_absent_sample_between_reads_stays_a_dropout(tmp_path):
    null_gap_fixture(tmp_path, null_record(sequence=11))
    row = discovery._space_light_read(tmp_path)
    assert row["reason"] == "light-null-gap"


def test_recent_previous_null_observation_does_not_name_a_freeze(tmp_path):
    null_gap_fixture(tmp_path, null_record(read_epoch_s=time.time() - 5))
    row = discovery._space_light_read(tmp_path)
    assert row["reason"] == "light-null-gap"


def test_session_change_drops_the_duration_class(tmp_path):
    null_gap_fixture(tmp_path, null_record(session="session-other"))
    row = discovery._space_light_read(tmp_path)
    assert row["reason"] == "light-null-gap"


def test_unreadable_retention_is_not_a_freeze(tmp_path):
    null_gap_fixture(tmp_path, null_record())
    (tmp_path / "discovery" / "space-light-stream.json").write_text("{not json")
    row = discovery._space_light_read(tmp_path)
    assert row["reason"] == "light-null-gap"


def test_null_streak_origin_survives_the_render_cadence(tmp_path):
    """A pane renders every few seconds, so the origin must not be refreshed
    per render or the measured interval never reaches the freeze threshold."""
    null_gap_fixture(tmp_path, None)
    assert discovery._space_light_read(tmp_path)["reason"] == "light-null-gap"
    record_path = tmp_path / "discovery" / "space-light-stream.json"
    origin = json.loads(record_path.read_text())["read_epoch_s"]
    for _ in range(5):
        assert discovery._space_light_read(tmp_path)["reason"] == "light-null-gap"
        assert json.loads(record_path.read_text())["read_epoch_s"] == origin
    # The same streak, older than the threshold with the origin preserved, is
    # named a frozen stream; the frozen read still does not refresh the origin.
    record = json.loads(record_path.read_text())
    record["read_epoch_s"] = time.time() - discovery.SPACE_FROZEN_MIN_SECONDS - 1
    record_path.write_text(json.dumps(record), encoding="utf-8")
    aged = record["read_epoch_s"]
    row = discovery._space_light_read(tmp_path)
    assert row["reason"] == "light-stream-frozen"
    assert json.loads(record_path.read_text())["read_epoch_s"] == aged


def null_gap_now(home: Path) -> dict:
    """A site publishing a null lux sample now, plus the reader's first row.

    The notification re-read compares against the reader's own clock, so its
    boundary carries current timestamps rather than the shared fixture's.
    """
    data = boundary()
    now = time.time()
    data["generated_at_utc"] = stamp(now)
    data["nodes"][0]["last_poll_utc"] = stamp(now)
    data["nodes"][0]["light"].update(lux=None, status="stale",
                                     phone_sample_epoch_s=now - 20,
                                     consumer_receipt_epoch_s=now - 2)
    (home / "body").mkdir(parents=True, exist_ok=True)
    (home / "body" / "space.json").write_text(json.dumps(data), encoding="utf-8")
    return discovery._space_light_read(home)


def retain_null_streak(record_path: Path, **overrides) -> str:
    """Rewrite the retained null observation and return the stored bytes."""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.update(overrides)
    stored = json.dumps(record)
    record_path.write_text(stored, encoding="utf-8")
    return stored


def unknown_reading_line(body: str) -> str:
    """The feed line that carries the non-verified readings' samples."""
    return next(line for line in body.splitlines()
                if line.startswith("Unknown readings:"))


def test_notification_recheck_names_a_sustained_freeze(tmp_path):
    """The notification re-read is the second consumer of the duration class.
    It names a freeze, and it never renews the streak's origin: scan() notifies
    every few seconds, so a write here would hold the measured interval below
    the freeze threshold for the whole life of the stream."""
    row = null_gap_now(tmp_path)
    assert row["reason"] == "light-null-gap"
    record_path = tmp_path / "discovery" / "space-light-stream.json"
    stored = retain_null_streak(
        record_path, read_epoch_s=time.time() - discovery.SPACE_FROZEN_MIN_SECONDS - 1)
    notify(tmp_path, row, row, now=time.time())
    line = unknown_reading_line(Feed(tmp_path).entries()[-1].body)
    assert "light-stream-frozen" in line
    assert "light-null-gap" not in line
    # A freeze still says how old the gap is.
    assert "last-phone=" in line and "age=" in line
    assert record_path.read_text(encoding="utf-8") == stored


def test_notification_recheck_keeps_a_seconds_wide_gap_a_gap(tmp_path):
    """A recent retained observation, or an advancing cursor, keeps the
    single-read name through the notification re-read as well."""
    row = null_gap_now(tmp_path)
    record_path = tmp_path / "discovery" / "space-light-stream.json"
    for overrides in ({"read_epoch_s": time.time() - 5}, {"cursor": 11}):
        retain_null_streak(record_path, **overrides)
        notify(tmp_path, row)
        line = unknown_reading_line(Feed(tmp_path).entries()[-1].body)
        assert "light-null-gap" in line
        assert "light-stream-frozen" not in line


def test_gap_to_freeze_reports_a_duration_class_change(tmp_path):
    """The same absent coverage judged for longer is a duration class, not a
    light change and not an availability baseline."""
    row = null_gap_now(tmp_path)
    retain_null_streak(tmp_path / "discovery" / "space-light-stream.json",
                       read_epoch_s=time.time() - discovery.SPACE_FROZEN_MIN_SECONDS - 1)
    notify(tmp_path, row, row, now=time.time())
    assert ("sense.space.light gap duration class light-null-gap -> "
            "light-stream-frozen") in Feed(tmp_path).entries()[-1].body


def test_freeze_to_gap_reports_a_duration_class_change(tmp_path):
    """A resuming stream that still publishes no value narrows the duration
    class instead of shifting the baseline."""
    row = null_gap_now(tmp_path)
    frozen = {**row, "reason": "light-stream-frozen",
              "sample": "Note3 light UNKNOWN: light-stream-frozen last-phone=x age=1h31m"}
    notify(tmp_path, row, frozen, now=time.time())
    assert ("sense.space.light gap duration class light-stream-frozen -> "
            "light-null-gap") in Feed(tmp_path).entries()[-1].body


@pytest.fixture
def current_endpoint(monkeypatch):
    """A recent clock endpoint so the pane's freshness gate passes."""
    current = {"clock": "CLOCK_BOOTTIME", "bounds_ns": [20_000_000_000, 20_000_000_100],
               "boot_id": "12345678-1234-5678-1234-567812345678",
               "time_namespace": "time:[4026531834]",
               "offsets": {"monotonic": [0, 0], "boottime": [0, 0]},
               "utc_ns": 1_700_000_000_000_000_000}
    monkeypatch.setattr("mishe_tauftauf.seed_culture_views.scan_freshness.endpoint",
                        lambda: current)
    return current


def _write_scan(home: Path, observations: list[dict]) -> None:
    """Publish one recent snapshot and its succeeded attempt record."""
    snapshot = {"created": "2099-01-01T00:00:00Z", "scan_id": "sample-1", "node": "node",
                "observations": observations,
                "acquisition": {"start": _endpoint(10_000_000_000, 10_000_000_100),
                                "end": _endpoint(11_000_000_000, 11_000_000_100,
                                                 1_700_000_001_000_000_000)}}
    directory = home / "discovery"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "latest.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (directory / "attempt.json").write_text(json.dumps(
        {"status": "succeeded", "stage": "notification", "scan_id": "sample-1",
         "created": "2023-11-14T22:13:20Z", "artifact": "sample-1.json"}), encoding="utf-8")


def _endpoint(first: int, last: int, utc_ns: int = 1_700_000_000_000_000_000) -> dict:
    return {"clock": "CLOCK_BOOTTIME", "bounds_ns": [first, last],
            "boot_id": "12345678-1234-5678-1234-567812345678",
            "time_namespace": "time:[4026531834]",
            "offsets": {"monotonic": [0, 0], "boottime": [0, 0]}, "utc_ns": utc_ns}


@pytest.mark.parametrize("pane", [discover, senses])
def test_pane_names_a_sustained_freeze_it_re_derives_at_render_time(
        tmp_path: Path, current_endpoint, pane) -> None:
    """The pane re-derives the light row from the boundary frame at render
    time; the reader-side duration class must survive that re-derivation or
    the pane cannot name a freeze it is staring at."""
    home = tmp_path / "site"
    null_gap_fixture(home, None)
    _write_scan(home, [])
    # No retained observation yet: the first render of a null read is a gap.
    assert "light-null-gap" in pane(home)
    assert "light-stream-frozen" not in pane(home)
    (home / "discovery" / "space-light-stream.json").write_text(json.dumps(
        null_record(read_epoch_s=time.time() - discovery.SPACE_FROZEN_MIN_SECONDS - 1)),
        encoding="utf-8")
    rendered = pane(home)
    assert "light-stream-frozen" in rendered
    assert "light-null-gap" not in rendered


@pytest.mark.parametrize("pane", [discover, senses])
def test_pane_keeps_a_seconds_wide_gap_a_gap(tmp_path: Path, current_endpoint, pane) -> None:
    """A retention record that is still recent, or whose cursor advanced,
    keeps the single-read name on the pane."""
    home = tmp_path / "site"
    null_gap_fixture(home, None)
    _write_scan(home, [])
    for record in (null_record(read_epoch_s=time.time() - 5),
                   null_record(cursor=11)):
        (home / "discovery" / "space-light-stream.json").write_text(
            json.dumps(record), encoding="utf-8")
        rendered = pane(home)
        assert "light-null-gap" in rendered
        assert "light-stream-frozen" not in rendered


def test_fresh_sample_is_not_retained_as_a_null_observation(tmp_path):
    data = boundary()
    now = time.time()
    data["generated_at_utc"] = stamp(now)
    data["nodes"][0]["last_poll_utc"] = stamp(now)
    data["nodes"][0]["light"].update(phone_sample_epoch_s=now - 20,
                                     consumer_receipt_epoch_s=now - 2)
    (tmp_path / "body").mkdir(parents=True, exist_ok=True)
    (tmp_path / "body" / "space.json").write_text(json.dumps(data), encoding="utf-8")
    row = discovery._space_light_read(tmp_path)
    assert row["state"] == "verified"
    assert not (tmp_path / "discovery" / "space-light-stream.json").exists()
