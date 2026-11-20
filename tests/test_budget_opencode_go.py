"""budget-opencode-go: the go-a weekly allowance window judged on the policy's own rules,
UNKNOWN when unmeasured, with ledger selections riding along."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "budget_opencode_go", Path(__file__).parent.parent / "checks" / "budget-opencode-go.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

NOW = 10_000_000.0
RESET = NOW + 3600.0


def budget(state="FRESH", used=0.34, **windows):
    def win(name, used=used, **kw):
        return {"state": "FRESH", "used": kw.get("used", used), "reset_at": kw.get("reset_at", RESET),
                "observed_at": NOW - 60, "burn_h": kw.get("burn_h", 0.003), "eta_s": kw.get("eta_s", 10**6)}
    rows = {"5h": win("5h", used=0.04, reset_at=NOW + 4 * 3600),
            "7d": win("7d"), "monthly": win("monthly", used=0.17, reset_at=NOW + 600 * 3600)}
    rows.update({name: win(name, **kw) for name, kw in windows.items()})
    return {"accounts": {"go-a": {"provider": "opencode-go", "state": state, "windows": rows}}}
def ledger(counts=0):
    return {"1h": 0, "24h": counts, "models": {"glm-5.3": counts} if counts else {},
            "coverage_s": None, "complete": True}


def test_green_carries_windows_selections_and_grade():
    code, line = mod.evaluate(budget(), ledger(9), NOW)
    assert code == 0
    assert "7d used 34%" in line and "selections 24h=9" in line and "grade GREEN" in line


def test_unmeasured_weekly_is_unknown_never_admitted():
    payload = budget()
    payload["accounts"]["go-a"]["windows"]["7d"]["state"] = "STALE"
    code, line = mod.evaluate(payload, ledger(), NOW)
    assert code == 3
    assert "unmeasured" in line


def test_missing_account_and_unreadable_file_are_unknown(tmp_path):
    assert mod.evaluate({"accounts": {}}, ledger(), NOW)[0] == 3
    code, report = mod.run(tmp_path, NOW)
    assert code == 3 and report["verdict"] == "UNKNOWN"


def test_main_reports_unknown_when_budget_file_unreadable(tmp_path, capsys):
    """The UNKNOWN path has no `detail`; main() must still print the verdict, not raise KeyError."""
    code = mod.main(["--home", str(tmp_path), "--budget", str(tmp_path / "missing.json")])
    out = capsys.readouterr().out
    assert code == 3
    assert "budget-opencode-go: UNKNOWN" in out
    assert "engine budget unreadable" in out


def test_spent_weekly_window_is_red():
    code, line = mod.evaluate(budget(used=0.95), ledger(), NOW)
    assert code == 2 and "LOW" in line


def test_spent_window_red_reason_carries_eta_and_reset():
    """A LOW verdict must tell the reader when the allowance runs out, not only that it is low."""
    code, line = mod.evaluate(
        budget(**{"7d": {"used": 0.95, "eta_s": 1800.0, "reset_at": NOW + 6 * 3600}}),
        ledger(), NOW)
    assert code == 2
    assert "LOW" in line
    assert "eta 30m to depletion" in line
    assert "reset in 6.0h" in line


def test_burn_runs_out_before_reset_is_red():
    code, line = mod.evaluate(budget(**{"7d": {"eta_s": 600.0}}), ledger(), NOW)
    assert code == 2 and "RUNS OUT BEFORE RESET" in line


def test_early_window_burn_is_not_actionable_overburn():
    # The 2026-11-18 budget-claude class: used 0.01, a short-span eta inside the window.
    code, _ = mod.evaluate(budget(**{"7d": {"used": 0.01, "eta_s": 3600.0}}), ledger(), NOW)
    assert code == 0


def test_monthly_window_is_context_not_a_verdict():
    # Monthly used 0.17 with an eta inside its own window: judged windows stay GREEN.
    code, line = mod.evaluate(budget(), ledger(), NOW)
    assert code == 0
    assert "monthly used 17%" in line


def test_ledger_selection_counts_go_a_only_and_mark_partial_coverage(tmp_path):
    home = tmp_path
    (home / "engine").mkdir(parents=True)
    at = "2026-11-19T18:00:00Z"
    rows = [{"at": at, "event": "select", "key": "omp:opencode-go/glm-5.3:go-a", "role": "docs"},
            {"at": at, "event": "select", "key": "omp:minimax-code/MiniMax-M3.1-Flash:minimax-a"},
            {"at": at, "event": "outcome", "key": "omp:opencode-go/glm-5.3:go-a"},
            {"at": at, "event": "select", "key": "omp:opencode-go/hy4-flash-free:-"}]
    (home / "engine" / "routing-ledger.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    import datetime as dt
    ledger_now = dt.datetime(2026, 11, 19, 20, 0, tzinfo=dt.timezone.utc).timestamp()
    got = mod._ledger_selections(home / "engine" / "routing-ledger.jsonl", ledger_now)
    assert got["models"] == {"glm-5.3": 1, "hy4-flash-free": 1}
    assert got["complete"] is False and got["coverage_s"] == 7200
    assert got["1h"] == 0 and got["24h"] == 2
