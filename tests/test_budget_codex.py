"""budget-codex: the codex-a 5h and 7d windows judged on the spent-window rule,
UNKNOWN when a window is not FRESH or malformed (never read as empty)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "budget_codex", Path(__file__).parent.parent / "checks" / "budget-codex.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def budget(w5=None, w7=None):
    def win(used, state="FRESH"):
        return {"state": state, "used": used, "burn_source": "history", "eta_s": None}
    windows = {}
    if w5 is not None:
        windows["5h"] = win(*w5) if isinstance(w5, tuple) else win(w5)
    if w7 is not None:
        windows["7d"] = win(*w7) if isinstance(w7, tuple) else win(w7)
    return {"accounts": {"codex-a": {"provider": "openai-codex", "state": "FRESH", "windows": windows}}}


def test_fresh_unspent_windows_are_green():
    code, detail = mod.evaluate(budget(w5=0.0, w7=0.81))
    assert code == mod.GREEN
    assert "7d 81% used" in detail


def test_spent_window_is_red():
    code, detail = mod.evaluate(budget(w5=0.2, w7=0.95))
    assert code == mod.RED
    assert "7d LOW" in detail


def test_stale_window_is_unknown_not_green():
    code, _ = mod.evaluate(budget(w5=0.0, w7=(0.1, "STALE")))
    assert code == mod.UNKNOWN


def test_missing_window_is_unknown():
    code, _ = mod.evaluate(budget(w5=0.0))
    assert code == mod.UNKNOWN


def test_absent_account_is_unknown():
    assert mod.evaluate({"accounts": {}})[0] == mod.UNKNOWN


def test_run_reads_engine_budget_json(tmp_path):
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "budget.json").write_text(json.dumps(budget(w5=0.0, w7=0.5)))
    assert mod.run(tmp_path)[0] == mod.GREEN


def test_run_unreadable_file_is_unknown(tmp_path):
    assert mod.run(tmp_path)[0] == mod.UNKNOWN
