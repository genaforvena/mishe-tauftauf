"""Controlled-failure tests for checks/overdue-intention.py (stdlib only, run from the committed check file)."""
import importlib.util
import json
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[1] / "checks" / "overdue-intention.py"
spec = importlib.util.spec_from_file_location("overdue_intention", CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

NOW = 100_000.0


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _site(tmp_path, *, due=NOW - 3600, spontaneous=True, starts=None, budget=None, engine=True):
    home = tmp_path
    _write(home / "intentions" / "docs" / "idle-initiative.json",
           {"id": "idle-initiative", "due": due, "spontaneous": spontaneous, "note": "x"})
    if engine:
        _write(home / "engines" / "docs.json", {"budget_signal": "budget-claude"})
    if budget is not None:
        _write(home / "signals" / "budget-claude.json", budget)
    _write(home / "wake-start-ledger.json", starts if starts is not None else {})
    return home


def _budget(state, value, observed=NOW - 10):
    return {"state": state, "value": value, "observed_at": observed, "reason": ""}


def test_no_overdue_intention_is_clean(tmp_path):
    home = _site(tmp_path, due=NOW + 60, budget=_budget("OK", "GREEN"))
    assert check.evaluate(home, NOW)[0] == 0


def test_overdue_with_green_budget_and_no_wake_is_red(tmp_path):
    home = _site(tmp_path, budget=_budget("OK", "GREEN"))
    code, lines = check.evaluate(home, NOW)
    assert code == 2 and any("not woken" in line for line in lines)


def test_wake_after_due_serves_the_intention(tmp_path):
    home = _site(tmp_path, starts={"docs": NOW - 60}, budget=_budget("UNKNOWN", None))
    assert check.evaluate(home, NOW)[0] == 0


def test_real_quota_hold_is_reported_as_held_not_red(tmp_path):
    home = _site(tmp_path, budget=_budget("RED", "RED"))
    code, lines = check.evaluate(home, NOW)
    assert code == 0 and any(line.startswith("HELD:") for line in lines)


@pytest.mark.parametrize("budget", [_budget("UNKNOWN", None), _budget("OK", "GREEN", observed=NOW - 5000), None])
def test_missing_or_stale_budget_evidence_is_unknown_not_held(tmp_path, budget):
    home = _site(tmp_path, budget=budget)
    code, lines = check.evaluate(home, NOW)
    assert code == 3 and any(line.startswith("UNKNOWN:") for line in lines)


def test_unreadable_intention_is_unknown(tmp_path):
    home = _site(tmp_path, due=NOW + 60, budget=_budget("OK", "GREEN"))
    (home / "intentions" / "docs" / "broken.json").write_text("{not json", encoding="utf-8")
    code, lines = check.evaluate(home, NOW)
    assert code == 3 and any("unreadable" in line for line in lines)


def test_unreadable_ledger_with_overdue_intention_is_unknown(tmp_path):
    home = _site(tmp_path, budget=_budget("OK", "GREEN"))
    (home / "wake-start-ledger.json").write_text("[", encoding="utf-8")
    assert check.evaluate(home, NOW)[0] == 3


def test_non_spontaneous_overdue_without_wake_is_red(tmp_path):
    home = _site(tmp_path, spontaneous=False, engine=False)
    assert check.evaluate(home, NOW)[0] == 2


def test_missing_intentions_root_is_unknown_not_empty_schedule(tmp_path):
    home = tmp_path  # no intentions/ directory at all
    _write(home / "wake-start-ledger.json", {})
    code, lines = check.evaluate(home, NOW)
    assert code == 3 and any("intentions root missing" in line for line in lines)


def test_non_numeric_due_is_unknown_not_skipped(tmp_path):
    home = _site(tmp_path, due=NOW + 60, budget=_budget("OK", "GREEN"))  # base intention not overdue
    _write(home / "intentions" / "docs" / "bad-due.json", {"id": "bad-due", "due": "tomorrow"})
    code, lines = check.evaluate(home, NOW)
    assert code == 3 and any("non-numeric due" in line for line in lines)
