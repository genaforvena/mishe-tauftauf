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
    store.register(tmp_path, "probe-sig", max_age=30, subscribers=["health"], now=NOW)
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


def test_never_observed_signal_routes_one_unknown_edge_after_its_grace(home):
    # A missing monitor must wake its subscriber once, not stay silent forever.
    assert store.expire(home, now=NOW + 29) == []
    assert store.expire(home, now=NOW + 31) == ["probe-sig"]
    recorded = store.recorded(home, "probe-sig")
    assert recorded["state"] == UNKNOWN
    assert recorded["reason"] == "never observed within 30s of registration"
    assert store.expire(home, now=NOW + 60) == []


def test_next_expiry_includes_the_grace_deadline_of_a_never_observed_signal(home):
    assert store.next_expiry(home) == NOW + 30
    store.expire(home, now=NOW + 31)
    assert store.next_expiry(home) is None


def test_observation_before_the_grace_deadline_keeps_the_signal_out_of_the_missing_path(home):
    store.observe(home, "probe-sig", "alive", now=NOW + 20, source="unit")
    assert store.expire(home, now=NOW + 31) == []                 # still fresh: no grace edge
    assert store.read(home, "probe-sig", now=NOW + 31).state == OK
    assert store.expire(home, now=NOW + 51) == ["probe-sig"]      # ordinary expiry, not the grace path
    assert store.recorded(home, "probe-sig")["reason"] == "expired after 30s without observation"


def test_legacy_registry_entry_without_registered_at_is_stamped_then_graced(tmp_path):
    store.register(tmp_path, "legacy-sig", max_age=30, subscribers=["health"], now=NOW)
    entries = store.registry(tmp_path)
    del entries["legacy-sig"]["registered_at"]
    from mishe_tauftauf.state_files import write_json
    write_json(tmp_path / "signals" / "registry.json", entries)
    assert store.expire(tmp_path, now=NOW + 100) == []          # stamps the grace start
    assert store.registry(tmp_path)["legacy-sig"]["registered_at"] == NOW + 100
    assert store.expire(tmp_path, now=NOW + 131) == ["legacy-sig"]
