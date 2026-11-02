"""wall.clear holds rotation only on fresh positive host-capacity pressure."""
import json

import pytest

from mishe_tauftauf import seed, wall


def _write_capacity(home, **fields):
    path = home / "recovery" / "host-capacity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields))


@pytest.mark.parametrize("fields, expected", [
    ({"limited": True, "state": "CRITICAL", "checked_at": 1000.0}, True),
    ({"limited": False, "state": "LIMITED", "checked_at": 1000.0}, True),
    ({"limited": False, "state": "NORMAL", "checked_at": 1000.0}, False),
    ({"limited": False, "state": "UNKNOWN", "checked_at": 1000.0}, False),
    # Stale evidence (older than the bound) must not freeze rotation.
    ({"limited": True, "state": "CRITICAL", "checked_at": 1000.0 - wall.CLEAR_CAPACITY_MAX_AGE - 1}, False),
])
def test_clear_hold_decision_uses_fresh_positive_evidence(tmp_path, fields, expected):
    _write_capacity(tmp_path, **fields)
    assert wall.host_capacity_holds_clear(tmp_path, now=1000.0) is expected


def test_missing_or_corrupt_capacity_evidence_does_not_hold(tmp_path):
    assert wall.host_capacity_holds_clear(tmp_path, now=1000.0) is False
    (tmp_path / "recovery").mkdir()
    (tmp_path / "recovery" / "host-capacity.json").write_text("{not json")
    assert wall.host_capacity_holds_clear(tmp_path, now=1000.0) is False


def test_clear_returns_held_before_touching_seed_state(tmp_path, monkeypatch):
    import time
    _write_capacity(tmp_path, limited=True, state="CRITICAL", checked_at=time.time())
    import mishe_tauftauf.tmux as tmux
    monkeypatch.setattr(tmux, "owns_session", lambda *a, **k: True)

    def no_lock(*a, **k):
        raise AssertionError("seed lock must not be taken while held")
    monkeypatch.setattr(seed, "_lock", no_lock)

    result = wall.clear(tmp_path, "owned-session", "genome")
    assert result.startswith("held: host capacity limited")
