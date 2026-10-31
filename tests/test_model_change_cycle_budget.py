"""A contended watch cycle is bounded by one lock budget, not one per change.

Controlled failure: with the seed lock held elsewhere, every recorded change
waited the full LOCK_WAIT_SECONDS in turn. Seven changes made a cycle about
140s long, beyond check_watch's 90s freshness window, so the guard read
"stale" on every tick even though the watcher was alive.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from mishe_tauftauf import model_change


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    for name in ("a", "b", "c", "d", "e"):
        (tmp_path / "model-changes" / name).mkdir(parents=True)
    (tmp_path / "checks").mkdir()

    class FakeFeed:
        def __init__(self, home):
            pass

        def append_runtime_once(self, kind, text):
            pass

    monkeypatch.setattr(model_change, "site_home", lambda home: Path(home))
    monkeypatch.setattr(model_change, "Feed", FakeFeed)
    monkeypatch.setattr(model_change.dispositions, "active", lambda *a, **k: None)
    return tmp_path


def test_contended_cycle_spends_one_budget_not_one_per_change(watcher, monkeypatch):
    budget = 0.5
    granted = []

    def busy_advance(home, name, lock_wait):
        granted.append(lock_wait)
        # Model the contended lock: each attempt waits what it was granted, then gives up.
        time.sleep(lock_wait)
        raise model_change.LockBusy(f"seed lock held elsewhere for {lock_wait:g}s")

    monkeypatch.setattr(model_change, "advance", busy_advance)
    started = time.monotonic()
    snapshot = model_change.watch_once(watcher, lock_wait=budget)
    elapsed = time.monotonic() - started

    assert len(snapshot["reports"]) == 5, "every change still gets a named deferral"
    assert all("observation deferred" in r["reason"] for r in snapshot["reports"])
    assert elapsed < budget + 0.5, f"cycle took {elapsed:.2f}s; one budget is {budget}s"
    assert granted[0] == pytest.approx(budget, abs=0.05)
    assert all(g <= 0.05 for g in granted[1:])


def test_uncontended_changes_each_observe_with_the_remaining_budget(watcher, monkeypatch):
    calls = []

    def free_advance(home, name, lock_wait):
        calls.append((name, lock_wait))
        return {"id": name, "state": "ok", "roles": {}, "checked": 0.0}

    monkeypatch.setattr(model_change, "advance", free_advance)
    snapshot = model_change.watch_once(watcher, lock_wait=20)
    assert [c[0] for c in calls] == ["a", "b", "c", "d", "e"]
    assert all(0 <= w <= 20 for _, w in calls)
    assert snapshot["state"] == "ok"
