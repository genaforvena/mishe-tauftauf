import importlib.util
import json
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "checks" / "system1-live-outcomes.py"
spec = importlib.util.spec_from_file_location("system1_live_outcomes", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NOW = 1_800_000_000.0


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_decision_open_without_ledger(tmp_path):
    code, _ = mod.decision_check(tmp_path, NOW, tmp_path / "budget.json")
    assert code == mod.OPEN


def test_decision_escalated_rows_do_not_count(tmp_path):
    _write(tmp_path / mod.DECISION_LOG, [
        {"source": "system1", "reason": "confidence 0.4 below threshold", "at": NOW - 10},
        {"source": "system0", "reason": "path rule", "at": NOW - 10},
    ])
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"date": "2026-11-15", "count": 3, "spent": 0.1}))
    assert mod.decision_check(tmp_path, NOW, budget)[0] == mod.OPEN


def test_decision_done_needs_recent_decision_and_spend(tmp_path):
    _write(tmp_path / mod.DECISION_LOG, [{"source": "system1", "reason": "decided", "at": NOW - 600}])
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"date": "2026-11-15", "count": 3, "spent": 0.1}))
    assert mod.decision_check(tmp_path, NOW, budget)[0] == mod.DONE
    budget.write_text(json.dumps({"date": "2026-11-15", "count": 3, "spent": 0}))
    assert mod.decision_check(tmp_path, NOW, budget)[0] == mod.OPEN


def test_decision_stale_answer_is_open(tmp_path):
    _write(tmp_path / mod.DECISION_LOG, [{"source": "system1", "reason": "decided", "at": NOW - 2 * 86400}])
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"date": "2026-11-15", "count": 3, "spent": 0.1}))
    assert mod.decision_check(tmp_path, NOW, budget)[0] == mod.OPEN


def test_corrupt_ledger_is_unknown_not_green(tmp_path):
    (tmp_path / "decision-log").mkdir()
    (tmp_path / mod.DECISION_LOG).write_text("{not json\n")
    code = subprocess.run([sys.executable, "-I", str(SCRIPT), "--home", str(tmp_path), "--check", "decision",
                           "--budget", str(tmp_path / "b.json")], capture_output=True).returncode
    assert code == mod.UNKNOWN


def test_wake_gate_shadow_rows_never_count(tmp_path):
    _write(tmp_path / "decision-log" / "wake-gate.jsonl", [{"state": "yielded", "decision": None, "at": NOW}])
    assert mod.wake_gate_check(tmp_path, NOW)[0] == mod.OPEN


def test_wake_gate_live_action_counts(tmp_path):
    _write(tmp_path / mod.WAKE_GATE_LIVE_LOG, [{"gate": "live", "action": "skip", "at": NOW - 60}])
    assert mod.wake_gate_check(tmp_path, NOW)[0] == mod.DONE
