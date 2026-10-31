"""A change ID's unobserved state alerts once, not once per wording flip.

Controlled failure: the live watcher alternated between "no named reason" and
"observation deferred: seed lock held elsewhere" on successive cycles, and each
flip reset the edge ladder to level 0, re-sending the DM (chat 124237, 124274).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mishe_tauftauf import model_change


@pytest.fixture
def watcher(tmp_path, monkeypatch):
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
    monkeypatch.setattr(model_change.dispositions, "active", lambda *a, **k: None)
    return tmp_path, sent


def test_alternating_lock_deferral_and_no_reason_sends_one_dm(watcher, monkeypatch):
    home, sent = watcher
    outcomes = [
        {"id": "abc", "state": "unknown", "roles": {}, "checked": 0.0},
        model_change.LockBusy("seed lock held elsewhere for 20s"),
        {"id": "abc", "state": "unknown", "roles": {}, "checked": 0.0},
        model_change.LockBusy("seed lock held elsewhere for 20s"),
    ]

    def fake_advance(home_, name, lock_wait, **kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(model_change, "advance", fake_advance)
    monkeypatch.setattr(model_change, "now", lambda: 1000.0)
    for _ in range(4):
        model_change.watch_once(home, lock_wait=0)
    assert len(sent) == 1
    assert "no named reason" in sent[0]


def test_changed_real_error_still_alerts(watcher, monkeypatch):
    home, sent = watcher
    outcomes = [
        {"id": "abc", "state": "unknown", "roles": {}, "checked": 0.0, "reason": "probe failed"},
        {"id": "abc", "state": "unknown", "roles": {}, "checked": 0.0, "reason": "provider missing"},
    ]
    monkeypatch.setattr(model_change, "advance", lambda h, n, lock_wait, **kwargs: outcomes.pop(0))
    monkeypatch.setattr(model_change, "now", lambda: 1000.0)
    for _ in range(2):
        model_change.watch_once(home, lock_wait=0)
    assert len(sent) == 2
