"""A parked (resting) role with no live window is not an unknown model change.

Genome wake 202373 item 2: a change whose receipt names a role the operator has
parked (inference-research, body-research, research-methods) kept reporting
UNKNOWN with reason 'mind window missing' every cycle, which made the aggregate
guard exit 3 even though the role was deliberately not running. A resting role
has nothing to activate or observe; its launcher is already the desired state
and a fresh start picks it up. The row must read 'parked' with the reason, and
an unparked role with no window must still be UNKNOWN.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mishe_tauftauf import model_boundary, model_change, dispositions


ROLE_BYTES = b"launcher-after\n"


@pytest.fixture
def site(tmp_path, monkeypatch):
    home = tmp_path
    (home / "minds").mkdir()
    (home / "minds" / "inference-research").write_bytes(ROLE_BYTES)
    (home / "minds" / "docs").write_bytes(ROLE_BYTES)
    root = home / "model-changes" / "chg"
    root.mkdir(parents=True)
    digest = model_boundary.digest(ROLE_BYTES)
    receipt = {
        "source": "old-model",
        "target": "new-model",
        "created": 1.0,
        "unknown": None,
        "roles": {
            "inference-research": {"before": "0" * 64, "after": digest, "state": "unknown", "reason": "mind window missing", "mode": 0o644},
            "docs": {"before": "0" * 64, "after": digest, "state": "unknown", "reason": "mind window missing", "mode": 0o644},
        },
    }
    monkeypatch.setattr(model_change, "site_home", lambda h: Path(h))
    monkeypatch.setattr(model_change.boundary, "receipt", lambda h, c: (root, receipt))
    monkeypatch.setattr(model_change, "now", lambda: 1793634703.0)
    return home


def _observe_missing_window(home, role, change, target, directory):
    # The real observe() maps boundary.process's gate failure to this exact shape.
    return {"state": "unknown", "reason": "mind window missing"}


def test_parked_role_without_window_is_parked_not_unknown(site, monkeypatch):
    monkeypatch.setattr(model_change.dispositions, "parked_roles", lambda home, now=None: ["inference-research"])
    monkeypatch.setattr(model_change.boundary, "observe", _observe_missing_window)

    report = model_change._status(site, "chg")

    assert report["roles"]["inference-research"]["state"] == "parked"
    assert "window" in report["roles"]["inference-research"]["reason"]
    assert report["roles"]["docs"]["state"] == "unknown"
    # One unparked role is still UNKNOWN, so the aggregate does not go quiet.
    assert report["state"] == "unknown"


def test_all_parked_change_reads_parked_not_unknown(site, monkeypatch):
    monkeypatch.setattr(model_change.dispositions, "parked_roles", lambda home, now=None: ["inference-research", "docs"])
    monkeypatch.setattr(model_change.boundary, "observe", _observe_missing_window)

    report = model_change._status(site, "chg")

    assert report["roles"]["inference-research"]["state"] == "parked"
    assert report["roles"]["docs"]["state"] == "parked"
    assert report["state"] == "parked"


def test_unparked_missing_window_stays_unknown(site, monkeypatch):
    monkeypatch.setattr(model_change.dispositions, "parked_roles", lambda home, now=None: [])
    monkeypatch.setattr(model_change.boundary, "observe", _observe_missing_window)

    report = model_change._status(site, "chg")

    assert report["roles"]["inference-research"]["state"] == "unknown"
    assert report["state"] == "unknown"
