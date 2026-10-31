"""Swallowed engine_watch read failures must stay visible in watch-errors.jsonl.

The return values are unchanged (None / fallback allowlist); only the silence is removed,
so a malformed selection no longer reads as an absent one.
"""
import json
from pathlib import Path

import pytest

import mishe_tauftauf.engine_watch as ew

NOW_MS = 1_792_712_432_436


def _errors(home: Path):
    path = home / ew.ERRORS_REL
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_malformed_current_selection_is_logged_and_raises(tmp_path):
    cur = tmp_path / "engine" / "current"
    cur.mkdir(parents=True)
    (cur / "health.json").write_text("{not json")

    with pytest.raises(ew.CurrentUnreadable):
        ew._load_current(tmp_path, "health")

    wheres = [e["where"] for e in _errors(tmp_path)]
    assert "load_current:health" in wheres


def test_missing_current_selection_is_not_logged(tmp_path):
    assert ew._load_current(tmp_path, "health") is None
    assert _errors(tmp_path) == []


def test_malformed_policy_is_logged_and_allowlist_still_returns(tmp_path):
    (tmp_path / "engine").mkdir(parents=True)
    (tmp_path / "engine" / "policy.json").write_text("{not json")

    allow = ew._load_allowlist(tmp_path, {})

    assert isinstance(allow, dict)
    assert "load_allowlist:policy" in [e["where"] for e in _errors(tmp_path)]


def _fake_plant(home: Path, monkeypatch):
    import mishe_tauftauf.engine_usage as eu
    import mishe_tauftauf.token_usage as tu
    status = {"state": "FRESH", "observed_at": "2026-10-31T10:00:00Z", "accounts": {}}
    monkeypatch.setattr(eu, "status", lambda h, now_ms=None, omp="omp": status)
    monkeypatch.setattr(tu, "report", lambda h, now_ms=None: {"state": "FRESH", "observed_at": "2026-10-31T10:00:00Z"})
    (home / "engine").mkdir(parents=True, exist_ok=True)
    (home / "engine" / "policy.json").write_text(json.dumps({"health": [{"provider": "opencode-go", "alias": "go-a", "model": "m"}]}))


def test_malformed_current_fails_closed_end_to_end(tmp_path, monkeypatch):
    _fake_plant(tmp_path, monkeypatch)
    (tmp_path / "engine" / "current").mkdir(parents=True)
    (tmp_path / "engine" / "current" / "health.json").write_text("{not json")

    cache = ew.once(tmp_path, now_ms=NOW_MS)

    assert cache["unreadable"] == ["current:health"]
    assert cache["decisions"]["health"]["decision"] == "hold"
    assert not (tmp_path / ew.PROPOSALS_REL).exists()  # no proposal from an unreadable current
    result = ew.check(tmp_path, now_ms=NOW_MS)
    assert result["status"] == "RED"
    assert "current:health" in result["reason"]
    alerts = (tmp_path / ew.ALERT_REL).read_text()
    assert "current:health" in alerts


def test_unreadable_policy_holds_every_role(tmp_path, monkeypatch):
    _fake_plant(tmp_path, monkeypatch)
    (tmp_path / "engine" / "policy.json").write_text("{not json")

    cache = ew.once(tmp_path, now_ms=NOW_MS)

    assert "policy" in cache["unreadable"]
    assert all(d["decision"] == "hold" for d in cache["decisions"].values())
    assert not (tmp_path / ew.PROPOSALS_REL).exists()
    assert ew.check(tmp_path, now_ms=NOW_MS)["status"] == "RED"


def test_missing_cache_is_red_not_green(tmp_path):
    result = ew.check(tmp_path, now_ms=NOW_MS)
    assert result["status"] == "RED"
    assert result["state"] == "UNKNOWN"
