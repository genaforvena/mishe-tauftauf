"""Scoped fixture: discriminate fresh producer receipt from stale-original-evidence
and unknown clock in the space-light sense pipeline.

Genome runs this to verify the fix without a live Note3 sensor.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mishe_tauftauf import discovery

NOW = 1791368000.0
BOUND = ("phone/host clock skew <=23929ms behind host "
         "(acquisition span 23929-26622ms, n=32 retained); skew/latency split unknown")


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def boundary(receipt_age, phone_age, status_reason=BOUND):
    return {"schema_version": 1, "producer": discovery.SPACE_PRODUCER.copy(),
            "generated_at_utc": stamp(NOW), "nodes": [{
                "node": "note3", "source": discovery.SPACE_SOURCE, "session": "session-a",
                "cursor": 12, "backlog_page_pending": False, "transport": "reachable",
                "last_error": None, "last_poll_utc": stamp(NOW), "last_light_transition": None,
                "light": {"status": "fresh-clock-conditional", "validity_seconds": 30,
                          "units": "lux", "delayed_at_receipt": False,
                          "sequence": 12, "lux": 14.431, "age_ms_at_phone_collection": 169,
                          "phone_sample_epoch_s": NOW - phone_age,
                          "consumer_receipt_epoch_s": NOW - receipt_age,
                          "phone_sample_utc": "2000-01-01T00:00:00Z",
                          "consumer_receipt_utc": "2000-01-01T00:00:00Z",
                          "status_reason": status_reason}}]}


def test_fresh_producer_receipt_verifies():
    """consumer_receipt_epoch_s within validity → verified, clock bound carried."""
    row = discovery._space_light(boundary(receipt_age=2, phone_age=20), NOW)
    assert row["state"] == "verified"
    assert row["current_lux"] == 14.431
    assert row["clock_bound"] == BOUND
    assert row["clock_uncertainty"] == "phone/host clock agreement unverified"


def test_stale_original_evidence_reports_stale():
    """consumer_receipt_epoch_s beyond validity → stale-original-evidence."""
    row = discovery._space_light(boundary(receipt_age=31, phone_age=51), NOW)
    assert row["state"] == "unknown"
    assert row["reason"] == "stale-original-evidence"
    assert row["current_lux"] is None
    assert "age=31s" in row["sample"]


def test_unknown_clock_verifies_with_unverified_clock():
    """No status_reason → verified with clock=unverified, no bound text."""
    row = discovery._space_light(boundary(receipt_age=2, phone_age=20, status_reason=None), NOW)
    assert row["state"] == "verified"
    assert row["clock_bound"] is None
    assert row["clock_uncertainty"] == "phone/host clock agreement unverified"


def test_boundary_exactly_at_validity_verifies():
    """consumer_receipt_epoch_s exactly at validity → verified (not stale)."""
    row = discovery._space_light(boundary(receipt_age=30, phone_age=50), NOW)
    assert row["state"] == "verified"


def test_phone_clock_behind_host_does_not_trigger_false_stale():
    """Phone stamp 27s behind receipt (within validity) → verified."""
    row = discovery._space_light(boundary(receipt_age=2, phone_age=29), NOW)
    assert row["state"] == "verified"
