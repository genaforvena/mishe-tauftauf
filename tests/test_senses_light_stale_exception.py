"""Light re-derivation is the one documented exception to the stale-label rule.

seed_culture_views._observation_reading does not gate sense.space.light on the
item's own source_state: it re-derives the reading from the retained boundary
against the current clock (discovery._space_light, 30 s validity on receipt).
These tests pin both sides of that boundary with a real clock, so an expired
boundary can never be shown as a current light reading under a recent scan.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from mishe_tauftauf import discovery
from mishe_tauftauf.seed_culture_views import _observation_reading


def _stamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _boundary(receipt_age: float, phone_age: float, now: float) -> dict:
    return {"schema_version": 1, "producer": discovery.SPACE_PRODUCER.copy(),
            "generated_at_utc": _stamp(now), "nodes": [{
                "node": "note3", "source": discovery.SPACE_SOURCE, "session": "session-a",
                "cursor": 12, "backlog_page_pending": False, "transport": "reachable",
                "last_error": None, "last_poll_utc": _stamp(now), "last_light_transition": None,
                "light": {"status": "fresh-clock-conditional", "validity_seconds": 30,
                          "units": "lux", "delayed_at_receipt": False,
                          "sequence": 12, "lux": 14.431, "age_ms_at_phone_collection": 169,
                          "phone_sample_epoch_s": now - phone_age,
                          "consumer_receipt_epoch_s": now - receipt_age,
                          "phone_sample_utc": _stamp(now - phone_age),
                          "consumer_receipt_utc": _stamp(now - receipt_age),
                          "status_reason": None}}]}


def _item(boundary: dict, source_state: str) -> dict:
    return {"id": "sense.space.light", "kind": "read", "state": source_state,
            "sample": "stale-frame", "boundary": boundary}


def test_expired_boundary_under_recent_scan_is_unknown_even_when_label_says_stale() -> None:
    now = time.time()
    state, line = _observation_reading(_item(_boundary(receipt_age=3600, phone_age=3620, now=now),
                                             "stale"), "recent")
    assert state == "unknown"
    assert line.startswith("UNKNOWN sense.space.light:")
    assert "stale-original-evidence" in line


def test_expired_boundary_under_recent_scan_is_unknown_when_label_says_verified() -> None:
    # The producer's own label cannot refresh an expired boundary either.
    now = time.time()
    state, _ = _observation_reading(_item(_boundary(receipt_age=3600, phone_age=3620, now=now),
                                          "verified"), "recent")
    assert state == "unknown"


def test_missing_boundary_stays_unknown_not_verified() -> None:
    item = {"id": "sense.space.light", "kind": "read", "state": "stale",
            "sample": "Note3 light UNKNOWN: missing"}
    state, line = _observation_reading(item, "recent")
    assert state == "unknown"
    assert line.startswith("UNKNOWN sense.space.light:")


def test_stale_label_with_fresh_receipt_is_the_documented_exception() -> None:
    # Documented exception: the re-derived receipt is inside the 30 s validity
    # against the current clock, so the reading is current evidence, not a
    # refreshed stale sample. The producer's stale label is kept in the detail.
    now = time.time()
    state, line = _observation_reading(_item(_boundary(receipt_age=2, phone_age=20, now=now),
                                             "stale"), "recent")
    assert state == "verified"
    assert "source_state=stale" in line
