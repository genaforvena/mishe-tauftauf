from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mishe_tauftauf import discovery
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.seed_culture_views import _observation_reading

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
