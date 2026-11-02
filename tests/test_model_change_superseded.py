"""An interrupted activation superseded by a normal rotation is reconciled only on live evidence.

Controlled failure: model-change 97892f stayed `activating` after its steward process rotated onto the
target model without a marker, so the guard failed every minute. The row may close as delivered only
when the live process started after activation_at, selects the target, carries no marker of this
change, and completes a provider turn. Every other combination stays activating.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mishe_tauftauf import model_boundary as boundary
from mishe_tauftauf import model_change

TARGET = "opencode-go/muse-spark-1.2-contributor"
CHANGE = "97892f36aa304848966ddc6dfdcc0f81"
ROW = {"state": "activating", "activation_at": 1793570796.0}


def _proc(**over):
    proc = {"model": TARGET, "marker": None, "pid": 2158450, "started": 1793576824.0}
    proc.update(over)
    return proc


def _patch(monkeypatch, proc=None, error=None, completion="verified"):
    def fake_process(home, role):
        if error is not None:
            raise error
        return ("sess", proc)

    monkeypatch.setattr(boundary, "process", fake_process)
    monkeypatch.setattr(boundary, "completion", lambda directory, target, started, **kwargs: completion)


def _reconcile(root: Path = Path("/nonexistent")):
    return model_change._superseded_activation(Path("/home"), root, "steward", ROW, CHANGE, TARGET)


def test_rotated_target_process_with_completed_turn_is_delivered(monkeypatch):
    _patch(monkeypatch, proc=_proc(), completion="verified")
    result = _reconcile()
    assert result["state"] == "delivered"
    assert result["superseded_process"]["pid"] == 2158450
    assert "canonical rotation" in result["reason"]


def test_process_started_before_activation_stays_activating(monkeypatch):
    _patch(monkeypatch, proc=_proc(started=1793570709.0), completion="verified")
    assert _reconcile()["state"] == "activating"


def test_process_bound_to_this_change_is_not_superseded(monkeypatch):
    _patch(monkeypatch, proc=_proc(marker=CHANGE), completion="verified")
    assert _reconcile()["state"] == "activating"


def test_process_on_another_model_stays_activating(monkeypatch):
    _patch(monkeypatch, proc=_proc(model="openai-codex/gpt-6.1-terra"), completion="verified")
    assert _reconcile()["state"] == "activating"


def test_pending_provider_turn_stays_activating(monkeypatch):
    _patch(monkeypatch, proc=_proc(), completion="waiting")
    result = _reconcile()
    assert result["state"] == "activating"
    assert "waiting" in result["reason"]


def test_unreadable_live_process_stays_activating(monkeypatch):
    _patch(monkeypatch, error=ValueError("mind window missing"))
    result = _reconcile()
    assert result["state"] == "activating"
    assert "unreadable" in result["reason"]


def test_missing_activation_time_stays_activating(monkeypatch):
    _patch(monkeypatch, proc=_proc(), completion="verified")
    result = model_change._superseded_activation(
        Path("/home"), Path("/nonexistent"), "steward", {"state": "activating"}, CHANGE, TARGET)
    assert result["state"] == "activating"


@pytest.mark.parametrize("completion", ["failed", "queued"])
def test_non_verified_completion_never_reads_delivered(monkeypatch, completion):
    _patch(monkeypatch, proc=_proc(), completion=completion)
    assert _reconcile()["state"] == "activating"
