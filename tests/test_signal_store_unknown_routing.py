"""Signal store routing for the three ways a reading becomes UNKNOWN.

Covers the paths the health probes exercise (never observed, observed then expired, and a failed
observation), so the probe subscriptions can be retired without losing this coverage.
"""

import pytest

from mishe_tauftauf import signal_store as store
from mishe_tauftauf.live_signal import OK, UNKNOWN

NOW = 1_800_000_000.0


@pytest.fixture
def home(tmp_path):
    store.register(tmp_path, "probe-sig", max_age=30, subscribers=["health"])
    return tmp_path


def test_never_observed_signal_reads_unknown_with_no_observation_reason(home):
    reading = store.read(home, "probe-sig", now=NOW)
    assert reading.state == UNKNOWN
    assert reading.value is None
    assert reading.reason == "no readable observation"


def test_green_observation_is_readable_within_max_age(home):
    store.observe(home, "probe-sig", "fresh", now=NOW, source="unit")
    reading = store.read(home, "probe-sig", now=NOW + 10)
    assert reading.state == OK
    assert reading.value == "fresh"


def test_observation_older_than_max_age_reads_unknown_as_expired(home):
    store.observe(home, "probe-sig", "stale", now=NOW, source="unit")
    reading = store.read(home, "probe-sig", now=NOW + 31)
    assert reading.state == UNKNOWN
    assert reading.value is None
    assert reading.reason.startswith("expired after 30s")


def test_expire_turns_a_stale_green_unknown_on_disk_once(home):
    store.observe(home, "probe-sig", "stale", now=NOW, source="unit")
    assert store.expire(home, now=NOW + 31) == ["probe-sig"]
    assert store.expire(home, now=NOW + 40) == []
    recorded = store.recorded(home, "probe-sig")
    assert recorded["state"] == UNKNOWN
    assert recorded["reason"] == "expired after 30s without observation"


def test_failed_observation_is_unknown_now_and_does_not_refresh_freshness(home):
    store.observe(home, "probe-sig", "was-fresh", now=NOW, source="unit")
    assert store.observe_unknown(home, "probe-sig", now=NOW + 5, reason="cannot run", source="unit")
    reading = store.read(home, "probe-sig", now=NOW + 6)
    assert reading.state == UNKNOWN
    assert reading.reason == "cannot run"
    # The failure did not extend freshness, so the old observation still expires on schedule.
    assert store.expire(home, now=NOW + 31) == []
