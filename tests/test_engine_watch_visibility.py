"""Swallowed engine_watch read failures must stay visible in watch-errors.jsonl.

The return values are unchanged (None / fallback allowlist); only the silence is removed,
so a malformed selection no longer reads as an absent one.
"""
import json
from pathlib import Path

import mishe_tauftauf.engine_watch as ew


def _errors(home: Path):
    path = home / ew.ERRORS_REL
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_malformed_current_selection_is_logged_and_still_none(tmp_path):
    cur = tmp_path / "engine" / "current"
    cur.mkdir(parents=True)
    (cur / "health.json").write_text("{not json")

    assert ew._load_current(tmp_path, "health") is None

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
