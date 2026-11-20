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


def test_observe_records_the_reason_behind_a_not_ok_reading(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    assert store.observe(home, "ci", "RED", now=1001.0, source="t",
                         reason="unit drift: mishe-wake-genome.path") is True
    assert store.read(home, "ci", now=1002.0).reason == "unit drift: mishe-wake-genome.path"
    # A reason does not make an edge of its own: only the value that carries it does.
    json_before = (home / "signals" / "ci.json").read_text()
    assert store.observe(home, "ci", "RED", now=1003.0, source="t", reason="different cause") is False
    # The latest cause is shown, and the reading file (what path units watch) is untouched.
    assert store.read(home, "ci", now=1004.0).reason == "different cause"
    assert (home / "signals" / "ci.json").read_text() == json_before
    assert store.rev(home, "ci") == 2


def test_a_new_edge_replaces_a_refreshed_cause(home):
    store.observe(home, "ci", "RED", now=1000.0, source="t", reason="first")
    store.observe(home, "ci", "RED", now=1001.0, source="t", reason="second")
    assert store.read(home, "ci", now=1002.0).reason == "second"
    assert store.observe(home, "ci", "GREEN", now=1003.0, source="t", reason="") is True
    assert store.read(home, "ci", now=1004.0).reason == ""
    assert not (home / "signals" / "ci.cause").exists()


def test_observe_defaults_to_no_reason(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    assert store.read(home, "ci", now=1001.0).reason == ""


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


def test_observing_a_state_name_is_refused(home):
    # "UNKNOWN" is a state, not a reading: storing it as a successful observation makes an
    # expired signal fresh and OK, which re-arms its expiry and starts its subscriber again
    # (2026-11-10: the health wake drove itself into systemd's start limit this way).
    for reserved in ("OK", "UNKNOWN", "STALE"):
        with pytest.raises(ValueError, match="not a reading"):
            store.observe(home, "ci", reserved, now=1000.0, source="t")
    assert not (home / "signals" / "ci.json").exists()      # nothing was written


def test_a_refused_observation_leaves_an_expired_signal_expired(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    store.expire(home, now=1061.0)
    with pytest.raises(ValueError):
        store.observe(home, "ci", "UNKNOWN", now=1062.0, source="t")
    assert store.read(home, "ci", now=1062.0).state == UNKNOWN   # still not fresh and OK


def test_observe_unknown_records_the_failure_without_refreshing_freshness(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    seen = home / "signals" / "ci.seen"
    before = seen.stat().st_mtime_ns
    assert store.observe_unknown(home, "ci", now=1100.0, reason="no readable observation", source="t") is True
    assert seen.stat().st_mtime_ns == before          # a failure is not evidence of freshness
    sig = store.read(home, "ci", now=1100.0)
    assert sig.state == UNKNOWN and sig.value is None


def _edges(home):
    path = home / "signals" / "edges.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_each_stored_edge_is_appended_to_history_and_refresh_is_not(home):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t", event="a")
    store.observe(home, "ci", "GREEN", now=1050.0, source="t", event="a")   # refresh: no edge
    store.observe(home, "ci", "RED", now=1060.0, source="t")
    store.observe_unknown(home, "ci", now=1100.0, reason="no input", source="t")
    edges = _edges(home)
    assert [(e["state"], e["value"], e["at"], e["rev"]) for e in edges] == [
        (OK, "GREEN", 1000.0, 1), (OK, "RED", 1060.0, 2), (UNKNOWN, None, 1100.0, 3)]
    assert edges[0]["event"] == "a" and edges[0]["source"] == "t"


def test_retire_removes_registration_and_files_and_keeps_reason_in_history(home, tmp_path):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    # the caller warns the operator to sync when the retired name was watched
    assert store.retire(home, "ci", now=1100.0, reason="experiment closed") == ["genome"]
    assert "ci" not in store.registry(home)
    assert not (tmp_path / "signals" / "ci.json").exists()
    assert not (tmp_path / "signals" / "ci.seen").exists()
    history = (tmp_path / "signals" / "edges.jsonl").read_text().splitlines()
    last = json.loads(history[-1])
    assert last["name"] == "ci" and last["state"] == "RETIRED" and last["reason"] == "experiment closed"
    with pytest.raises(ValueError):
        store.observe(home, "ci", "GREEN", now=1200.0, source="t")


def test_retire_keeps_the_reason_as_given(home, tmp_path):
    store.observe(home, "ci", "GREEN", now=1000.0, source="t")
    store.retire(home, "ci", now=1100.0, reason="  experiment closed\n")
    last = json.loads((tmp_path / "signals" / "edges.jsonl").read_text().splitlines()[-1])
    assert last["reason"] == "  experiment closed\n"


def test_retire_needs_a_registered_name_and_a_reason(home):
    with pytest.raises(ValueError):
        store.retire(home, "nope", now=1.0, reason="closed")
    with pytest.raises(ValueError):
        store.retire(home, "ci", now=1.0, reason="  ")
    assert "ci" in store.registry(home)


def test_registered_ack_survives_a_refresh_without_one(home):
    store.register(home, "ci", max_age=60, subscribers=["genome"], ack="ack {role}")
    store.register(home, "ci", max_age=60, subscribers=["genome"])
    assert store.registry(home)["ci"]["ack"] == "ack {role}"
