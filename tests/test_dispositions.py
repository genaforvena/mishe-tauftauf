"""Dispositions and edge-triggered model-change alerts.

Controlled failures: a persisting finding sends once, escalates on an age
ladder to a wider resolver, goes quiet under an unexpired disposition, returns
when that expires, and never goes quiet because a record or memory is broken.
"""
from __future__ import annotations

import pytest

from mishe_tauftauf import dispositions, model_change

T0 = 1_000_000.0


def test_edge_sends_once_then_ladder():
    state = {}
    assert dispositions.edge(state, "a", "r", now=T0) == (0, T0)
    assert dispositions.edge(state, "a", "r", now=T0 + 120) is None
    assert dispositions.edge(state, "a", "r", now=T0 + 3599) is None
    assert dispositions.edge(state, "a", "r", now=T0 + 3600)[0] == 1
    assert dispositions.edge(state, "a", "r", now=T0 + 3700) is None
    assert dispositions.edge(state, "a", "r", now=T0 + 6 * 3600)[0] == 2
    assert dispositions.edge(state, "a", "r", now=T0 + 24 * 3600)[0] == 3
    assert dispositions.edge(state, "a", "r", now=T0 + 25 * 3600) is None
    assert dispositions.edge(state, "a", "r", now=T0 + 48 * 3600)[0] == 4


def test_changed_signature_is_a_new_finding():
    state = {}
    dispositions.edge(state, "a", "r1", now=T0)
    assert dispositions.edge(state, "a", "r2", now=T0 + 60) == (0, T0 + 60)


def test_disposition_expires_and_requires_evidence(tmp_path):
    dispositions.set_disposition(tmp_path, "model-change:x", "benign", "health", "probe ok", ttl=100, now=T0)
    assert dispositions.active(tmp_path, "model-change:x", now=T0 + 99)
    assert dispositions.active(tmp_path, "model-change:x", now=T0 + 100) is None
    with pytest.raises(ValueError):
        dispositions.set_disposition(tmp_path, "k", "benign", "health", "", ttl=10)
    with pytest.raises(ValueError):
        dispositions.set_disposition(tmp_path, "k", "benign", "health", "e", ttl=0)
    with pytest.raises(ValueError):
        dispositions.set_disposition(tmp_path, "k", "mute", "health", "e", ttl=10)


def test_broken_record_never_mutes(tmp_path):
    (tmp_path / "dispositions").mkdir()
    (tmp_path / "dispositions" / "model-change_x.json").write_text("{not json")
    assert dispositions.active(tmp_path, "model-change:x") is None
    assert dispositions.listing(tmp_path)[0]["state"] == "unreadable"
    assert dispositions.parked_roles(tmp_path) == []


def test_parked_roles(tmp_path):
    dispositions.park(tmp_path, "body-research", "operator", "budget", ttl=3600, now=T0)
    assert dispositions.parked_roles(tmp_path, now=T0 + 1) == ["body-research"]
    assert dispositions.parked(tmp_path, "body-research", now=T0 + 1)
    assert not dispositions.parked(tmp_path, "witness", now=T0 + 1)
    assert dispositions.parked_roles(tmp_path, now=T0 + 3600) == []   # expiry returns the UNKNOWN
    with pytest.raises(ValueError):
        dispositions.set_disposition(tmp_path, "x", "parked", "o", "e", ttl=10)



def test_malformed_parked_record_never_parks_a_role(tmp_path):
    """Witness 127059 controlled failure: a parked record missing owner/evidence,
    or whose role disagrees with its key, must leave the role missing (a failure)."""
    import json
    root = tmp_path / "dispositions"
    root.mkdir()
    good = {"key": "parked:a", "kind": "parked", "role": "a", "owner": "o", "evidence": "e",
            "created": T0, "recheck_at": T0 + 3600}
    cases = {
        "no_owner.json": {k: v for k, v in good.items() if k != "owner"} | {"key": "parked:b", "role": "b"},
        "blank_evidence.json": good | {"key": "parked:c", "role": "c", "evidence": "  "},
        "role_mismatch.json": good | {"key": "parked:d", "role": "e"},
        "no_recheck.json": {k: v for k, v in good.items() if k != "recheck_at"} | {"key": "parked:f", "role": "f"},
    }
    for name, record in cases.items():
        (root / name).write_text(json.dumps(record))
    (root / "parked_a.json").write_text(json.dumps(good))
    assert dispositions.parked_roles(tmp_path, now=T0 + 1) == ["a"]
    for role in "bcdef":
        assert not dispositions.parked(tmp_path, role, now=T0 + 1)
    assert all(row.get("kind") != "parked" or row.get("role") == "a"
               for row in dispositions.listing(tmp_path, now=T0 + 1))


class FakeFeed:
    sent = []

    def __init__(self, home):
        pass

    def append_runtime_once(self, source, body):
        FakeFeed.sent.append(body)


@pytest.fixture
def watch(tmp_path, monkeypatch):
    (tmp_path / "model-changes" / "abc").mkdir(parents=True)
    clock = [T0]
    FakeFeed.sent = []
    monkeypatch.setattr(model_change, "Feed", FakeFeed)
    monkeypatch.setattr(model_change, "now", lambda: clock[0])
    monkeypatch.setattr(model_change, "site_home", lambda home: tmp_path)
    monkeypatch.setattr(model_change, "advance", lambda *a, **k: {
        "id": "abc", "state": "unknown", "reason": "stale receipt", "roles": {}, "checked": clock[0]})
    return tmp_path, clock


def test_watch_dedupes_escalates_and_dispositions(watch):
    home, clock = watch
    for _ in range(5):                       # same finding every 30s: one message
        model_change.watch_once(home)
        clock[0] += 30
    assert len(FakeFeed.sent) == 1 and "to=health" in FakeFeed.sent[0] and "interval=" not in FakeFeed.sent[0]
    clock[0] = T0 + 3600                     # unresolved an hour: escalates to genome
    snap = model_change.watch_once(home)
    assert len(FakeFeed.sent) == 2 and "to=genome" in FakeFeed.sent[1] and snap["state"] == "unknown"
    dispositions.set_disposition(home, "model-change:abc", "benign", "health", "alias; probes ok", ttl=600, now=clock[0])
    snap = model_change.watch_once(home)     # recorded answer: quiet, still listed, monitoring ok
    assert len(FakeFeed.sent) == 2 and snap["dispositioned"] == ["abc"] and snap["state"] == "ok"
    assert snap["reports"][0]["state"] == "unknown"
    clock[0] += 601                          # expiry returns the finding as new
    snap = model_change.watch_once(home)
    assert len(FakeFeed.sent) == 3 and "to=health" in FakeFeed.sent[2] and snap["state"] == "unknown"


def test_unreadable_alert_memory_realerts_not_mutes(watch):
    home, _ = watch
    (home / "checks").mkdir()
    (home / "checks" / "model-change-alerts.json").write_text("garbage")
    model_change.watch_once(home)
    assert len(FakeFeed.sent) == 1
