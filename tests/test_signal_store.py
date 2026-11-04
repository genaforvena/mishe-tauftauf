import json
import os

import pytest

from mishe_tauftauf import signal_store as store
from mishe_tauftauf.live_signal import OK, UNKNOWN


@pytest.fixture
def home(tmp_path):
    store.register(tmp_path, "ci", max_age=60, subscribers=["genome"])
    return tmp_path


def test_missing_signal_file_reads_unknown(home):
    sig = store.read(home, "ci", now=1000.0)
    assert sig.state == UNKNOWN and sig.value is None


def test_unregistered_signal_cannot_be_observed(home):
    with pytest.raises(ValueError):
        store.observe(home, "nope", "GREEN", now=1.0, source="t")


def test_first_observation_is_an_edge_and_reads_ok(home):
    assert store.observe(home, "ci", "GREEN", now=1000.0, source="t") is True
    sig = store.read(home, "ci", now=1010.0)
    assert (sig.state, sig.value) == (OK, "GREEN")


def test_same_value_is_not_an_edge_but_refreshes_seen(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    path = home / "signals" / "ci.json"
    before = path.stat().st_mtime_ns
    assert store.observe(home, "ci", "GREEN", now=1050.0, source="t") is False
    assert path.stat().st_mtime_ns == before          # json untouched: no wake edge
    assert store.read(home, "ci", now=1100.0).state == OK  # seen refreshed to 1050


def test_changed_value_is_an_edge_and_bumps_rev(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    assert store.observe(home, "ci", "RED", now=1001.0, source="t") is True
    assert store.rev(home, "ci") == 2


def test_silent_source_expires_to_unknown(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    sig = store.read(home, "ci", now=1061.0)
    assert sig.state == UNKNOWN and "expired" in sig.reason


def test_expire_turns_file_unknown_once_and_is_an_edge(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    assert store.expire(home, now=1030.0) == []
    assert store.expire(home, now=1061.0) == ["ci"]
    assert json.loads((home / "signals" / "ci.json").read_text())["state"] == UNKNOWN
    assert store.expire(home, now=1100.0) == []       # already UNKNOWN: no second edge


def test_recovery_after_expiry_is_an_edge(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    store.expire(home, now=1061.0)
    assert store.observe(home, "ci", "GREEN", now=1070.0, source="t") is True


def test_next_expiry_is_earliest_live_deadline(home):
    store.register(home, "psi", max_age=10, subscribers=[])
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    store.observe(home, "psi", 1, now=1005.0, source="t")
    assert store.next_expiry(home) == 1015.0
    store.expire(home, now=1016.0)
    assert store.next_expiry(home) == 1060.0


def test_corrupt_signal_file_reads_unknown_and_is_preserved(home):
    (home / "signals" / "ci.json").write_text("{not json")
    (home / "signals" / "ci.seen").touch()
    assert store.read(home, "ci", now=1.0).state == UNKNOWN
    assert (home / "signals" / "ci.json").read_text() == "{not json"
