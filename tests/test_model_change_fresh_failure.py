"""A historical disposition must not answer a fresh observation failure.

Witness wake 132060 control: with a `known` disposition held for a change, a
new LockBusy (or read error) in the current cycle was reported as dispositioned
and the snapshot read `ok`. Fresh failures must stay UNKNOWN and reach a resolver.
Historical dispositions still apply to observed reports.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mishe_tauftauf import model_change


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "model-changes" / "abc").mkdir(parents=True)
    (tmp_path / "checks").mkdir()
    sent = []

    class FakeFeed:
        def __init__(self, home):
            pass

        def append_runtime_once(self, kind, text):
            sent.append(text)

    monkeypatch.setattr(model_change, "site_home", lambda home: Path(home))
    monkeypatch.setattr(model_change, "Feed", FakeFeed)
    monkeypatch.setattr(
        model_change.dispositions,
        "active",
        lambda *a, **k: {"kind": "known", "owner": "health", "evidence": "x", "recheck_at": "later"},
    )
    return tmp_path, sent


def test_lock_busy_is_not_masked_by_old_disposition(site, monkeypatch):
    home, sent = site

    def contended(home, name, lock_wait, **kwargs):
        raise model_change.LockBusy("controlled lock contention")

    monkeypatch.setattr(model_change, "advance", contended)
    snapshot = model_change.watch_once(home, lock_wait=0.0)

    assert snapshot["state"] == "unknown"
    assert snapshot["dispositioned"] == []
    assert "disposition" not in snapshot["reports"][0]
    assert sent, "a fresh unobserved failure must be routed to a resolver"


def test_read_error_is_not_masked_by_old_disposition(site, monkeypatch):
    home, sent = site

    def broken(home, name, lock_wait, **kwargs):
        raise OSError("record unreadable")

    monkeypatch.setattr(model_change, "advance", broken)
    snapshot = model_change.watch_once(home, lock_wait=0.0)

    assert snapshot["state"] == "unknown"
    assert "disposition" not in snapshot["reports"][0]
    assert sent


def test_observed_unknown_still_uses_recorded_disposition(site, monkeypatch):
    home, sent = site

    def observed_unknown(home, name, lock_wait, **kwargs):
        return {"id": name, "state": "unknown", "roles": {}, "checked": model_change.now(), "reason": "model status unknown"}

    monkeypatch.setattr(model_change, "advance", observed_unknown)
    snapshot = model_change.watch_once(home, lock_wait=0.0)

    # Historical answer for an observed finding: recorded, not re-sent, snapshot OK.
    assert snapshot["state"] == "ok"
    assert snapshot["dispositioned"] == ["abc"]
    assert snapshot["reports"][0]["disposition"]["kind"] == "known"
    assert sent == []


def test_status_read_failure_is_not_masked_by_old_disposition(site, monkeypatch, tmp_path):
    """Witness wake 132295 control: the real advance/_status path, not a fake advance.

    The read failure happens inside status; the old code swallowed it into an
    ordinary unknown report and applied the historical disposition.
    """
    home, sent = site
    monkeypatch.setattr(model_change, "_seed_lock", lambda *a, **k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(model_change.boundary, "receipt", lambda h, c: (tmp_path, {"roles": {}, "source": "s", "target": "t", "created": model_change.now(), "unknown": []}))

    def failing_status_read(home, change):
        raise OSError("controlled status read failure")

    monkeypatch.setattr(model_change, "_status", failing_status_read)
    snapshot = model_change.watch_once(home, lock_wait=0.0)

    assert snapshot["state"] == "unknown"
    assert snapshot["dispositioned"] == []
    assert "disposition" not in snapshot["reports"][0]
    assert sent, "a fresh status-read failure must be routed to a resolver"


def test_observed_status_still_uses_recorded_disposition_through_real_path(site, monkeypatch, tmp_path):
    """Normal observed-disposition control through the real advance/_status path."""
    home, sent = site
    monkeypatch.setattr(model_change, "_seed_lock", lambda *a, **k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(model_change.boundary, "receipt", lambda h, c: (tmp_path, {"roles": {}, "source": "s", "target": "t", "created": model_change.now(), "unknown": []}))
    monkeypatch.setattr(model_change, "_status", lambda h, c: {"id": c, "state": "unknown", "roles": {}, "checked": model_change.now(), "reason": "model status unknown"})
    snapshot = model_change.watch_once(home, lock_wait=0.0)

    assert snapshot["state"] == "ok"
    assert snapshot["dispositioned"] == ["abc"]
    assert sent == []
