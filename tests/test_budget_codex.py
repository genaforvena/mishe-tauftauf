"""budget-codex: the codex-a 5h and 7d windows judged on the spent-window rule,
UNKNOWN when a window is not FRESH or malformed (never read as empty)."""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "budget_codex", Path(__file__).parent.parent / "checks" / "budget-codex.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
NOW = 1795884200.0


def budget(w5=None, w7=None, *, now=NOW):
    def win(used, state="FRESH"):
        return {"state": state, "used": used, "burn_source": "history", "eta_s": None,
                "observed_at": now - 60, "reset_at": now + 3600}
    windows = {}
    if w5 is not None:
        windows["5h"] = win(*w5) if isinstance(w5, tuple) else win(w5)
    if w7 is not None:
        windows["7d"] = win(*w7) if isinstance(w7, tuple) else win(w7)
    return {"schema": 1, "produced_at": now,
            "accounts": {"codex-a": {"provider": "openai-codex", "state": "FRESH", "windows": windows}}}


def test_fresh_unspent_windows_are_green():
    code, detail = mod.evaluate(budget(w5=0.0, w7=0.81), now=NOW)
    assert code == mod.GREEN
    assert "7d 81% used" in detail


def test_spent_window_is_red():
    code, detail = mod.evaluate(budget(w5=0.2, w7=0.95), now=NOW)
    assert code == mod.RED
    assert "7d LOW" in detail


def test_stale_window_is_unknown_not_green():
    code, _ = mod.evaluate(budget(w5=0.0, w7=(0.1, "STALE")), now=NOW)
    assert code == mod.UNKNOWN


def test_missing_window_is_unknown():
    code, _ = mod.evaluate(budget(w5=0.0), now=NOW)
    assert code == mod.UNKNOWN


def test_absent_account_is_unknown():
    assert mod.evaluate(budget() | {"accounts": {}}, now=NOW)[0] == mod.UNKNOWN


def test_run_reads_engine_budget_json(tmp_path):
    (tmp_path / "engine").mkdir()
    report = budget(w5=0.0, w7=0.5, now=time.time())
    (tmp_path / "engine" / "budget.json").write_text(json.dumps(report))
    assert mod.run(tmp_path)[0] == mod.GREEN


def test_run_unreadable_file_is_unknown(tmp_path):
    assert mod.run(tmp_path)[0] == mod.UNKNOWN


@pytest.mark.parametrize("accounts", [[], {"codex-a": []},
                                      {"codex-a": {"windows": ["5h", "7d"]}},
                                      {"codex-a": {"windows": None}}])
def test_malformed_accounts_or_windows_are_unknown(accounts):
    assert mod.evaluate(budget() | {"accounts": accounts}, now=NOW)[0] == mod.UNKNOWN


def test_measured_spent_window_is_red_even_when_sibling_unmeasured():
    code, detail = mod.evaluate(budget(w5=0.95), now=NOW)
    assert code == mod.RED
    assert "5h LOW" in detail and "7d unmeasured" in detail


def test_run_non_object_budget_is_unknown(tmp_path):
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "budget.json").write_text("[]")
    assert mod.run(tmp_path)[0] == mod.UNKNOWN


@pytest.mark.parametrize("failure", ["old file", "old observation", "passed reset", "missing timestamp"])
@pytest.mark.parametrize("used", [0.2, 0.97])
def test_run_rejects_expired_evidence_with_fresh_labels(tmp_path, failure, used):
    now = time.time()
    report = budget(w5=0.1, w7=used, now=now)
    windows = report["accounts"]["codex-a"]["windows"]
    if failure == "old file":
        report["produced_at"] = now - 1200
    elif failure == "old observation":
        windows["7d"]["observed_at"] = now - 1200
    elif failure == "passed reset":
        windows["7d"]["reset_at"] = now - 1
    else:
        del windows["7d"]["observed_at"]

    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "budget.json").write_text(json.dumps(report))
    assert mod.run(tmp_path)[0] == mod.UNKNOWN
