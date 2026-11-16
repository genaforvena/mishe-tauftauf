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


def _start(decision=None):
    return {"kind": "start", "role": "pub", "key": "k", "ts": 1, "decision": decision}


def test_shadow_null_rows_never_pass_on_count(tmp_path):
    # Counterexample from the register audit: 263 rows, every start decision null, passed a row count.
    rows = [_start() for _ in range(263)]
    _write(tmp_path / "decision-log" / "wake-gate.jsonl", rows)
    code, detail = mod.shadow_check(tmp_path)
    assert code == mod.OPEN
    assert "0 decided of 263" in detail


def test_shadow_answered_rows_pass_at_fifty(tmp_path):
    answered = [_start({"choice": "wake", "confidence": 0.9}) for _ in range(50)]
    _write(tmp_path / "decision-log" / "wake-gate.jsonl", answered + [_start() for _ in range(20)])
    assert mod.shadow_check(tmp_path)[0] == mod.DONE
    _write(tmp_path / "decision-log" / "wake-gate.jsonl", answered[:49])
    assert mod.shadow_check(tmp_path)[0] == mod.OPEN


def test_shadow_empty_choice_and_end_rows_do_not_count(tmp_path):
    rows = [_start({"choice": ""}) for _ in range(30)] + [_start({"choice": None}) for _ in range(30)]
    rows += [{"kind": "end", "decision": {"choice": "wake"}} for _ in range(30)]
    _write(tmp_path / "decision-log" / "wake-gate.jsonl", rows)
    assert mod.shadow_check(tmp_path)[0] == mod.OPEN


def test_shadow_missing_ledger_is_open_and_corrupt_is_unknown(tmp_path):
    assert mod.shadow_check(tmp_path)[0] == mod.OPEN
    (tmp_path / "decision-log").mkdir()
    (tmp_path / "decision-log" / "wake-gate.jsonl").write_text("{bad\n")
    code = subprocess.run([sys.executable, "-I", str(SCRIPT), "--home", str(tmp_path), "--check", "shadow"],
                          capture_output=True).returncode
    assert code == mod.UNKNOWN


def _main(tmp_path, check, now=NOW, budget=None):
    """Exit code through main(), the real path that turns malformed evidence into UNKNOWN."""
    args = ["--home", str(tmp_path), "--check", check, "--now", str(now),
            "--budget", str(budget or tmp_path / "budget.json")]
    return mod.main(args)


def _budget(tmp_path, spent=1):
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"date": "2026-11-15", "count": 3, "spent": spent}))
    return budget


def test_decision_future_stamp_is_unknown_not_done(tmp_path):
    # Reviewer counterexample: a decided row stamped after now plus spent=1 must not pass.
    _write(tmp_path / mod.DECISION_LOG, [{"source": "system1", "reason": "decided", "at": 2_000_000_000}])
    assert _main(tmp_path, "decision", 1_800_000_000.0, _budget(tmp_path)) == mod.UNKNOWN


def test_wake_gate_future_stamp_is_unknown_not_done(tmp_path):
    _write(tmp_path / mod.WAKE_GATE_LIVE_LOG, [{"gate": "live", "action": "wake", "at": 2_000_000_000}])
    assert _main(tmp_path, "wake-gate", 1_800_000_000.0) == mod.UNKNOWN


def test_nonfinite_stamps_are_unknown_not_done(tmp_path):
    # Python json accepts NaN and Infinity; the ledger must still be rejected, never GREEN.
    for literal in ("NaN", "Infinity", "-Infinity"):
        path = tmp_path / mod.DECISION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"source": "system1", "reason": "decided", "at": %s}\n' % literal)
        assert _main(tmp_path, "decision", NOW, _budget(tmp_path)) == mod.UNKNOWN, literal
        gate = tmp_path / mod.WAKE_GATE_LIVE_LOG
        gate.write_text('{"gate": "live", "action": "skip", "at": %s}\n' % literal)
        assert _main(tmp_path, "wake-gate") == mod.UNKNOWN, literal


def test_nonfinite_spend_is_unknown_not_done(tmp_path):
    _write(tmp_path / mod.DECISION_LOG, [{"source": "system1", "reason": "decided", "at": NOW - 10}])
    budget = tmp_path / "budget.json"
    budget.write_text('{"spent": Infinity}')
    assert _main(tmp_path, "decision", NOW, budget) == mod.UNKNOWN


def test_valid_window_still_passes_and_old_rows_do_not_count(tmp_path):
    # Valid historical evidence in the window still counts; a stale live action alone does not.
    _write(tmp_path / mod.DECISION_LOG, [
        {"source": "system1", "reason": "decided", "at": NOW - 3 * 86400},
        {"source": "system1", "reason": "decided", "at": NOW - 300},
    ])
    assert mod.decision_check(tmp_path, NOW, _budget(tmp_path))[0] == mod.DONE
    _write(tmp_path / mod.WAKE_GATE_LIVE_LOG, [{"gate": "live", "action": "skip", "at": NOW - 2 * 86400}])
    assert mod.wake_gate_check(tmp_path, NOW)[0] == mod.OPEN
    _write(tmp_path / mod.WAKE_GATE_LIVE_LOG, [{"gate": "live", "action": "skip", "at": NOW + 0}])
    assert mod.wake_gate_check(tmp_path, NOW)[0] == mod.DONE


def test_mixed_overflow_stamp_is_unknown_even_with_valid_recent_row(tmp_path):
    # Steward counterexample (DM 301607): json parses 1e999 to inf; a valid recent row must not hide it.
    path = tmp_path / mod.DECISION_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"source": "system1", "reason": "decided", "at": 1e999}\n'
                    '{"source": "system1", "reason": "decided", "at": %r}\n' % (NOW - 60))
    assert _main(tmp_path, "decision", NOW, _budget(tmp_path)) == mod.UNKNOWN
    gate = tmp_path / mod.WAKE_GATE_LIVE_LOG
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text('{"gate": "live", "action": "skip", "at": 1e999}\n'
                    '{"gate": "live", "action": "wake", "at": %r}\n' % (NOW - 60))
    assert _main(tmp_path, "wake-gate", NOW) == mod.UNKNOWN


def test_malformed_stamp_on_relevant_row_is_unknown(tmp_path):
    # A decided row with a missing or string stamp is malformed evidence, not a row to skip silently.
    _write(tmp_path / mod.DECISION_LOG, [
        {"source": "system1", "reason": "decided", "at": NOW - 60},
        {"source": "system1", "reason": "decided"},
    ])
    assert _main(tmp_path, "decision", NOW, _budget(tmp_path)) == mod.UNKNOWN
    _write(tmp_path / mod.DECISION_LOG, [
        {"source": "system1", "reason": "decided", "at": NOW - 60},
        {"source": "system1", "reason": "decided", "at": "yesterday"},
    ])
    assert _main(tmp_path, "decision", NOW, _budget(tmp_path)) == mod.UNKNOWN
    _write(tmp_path / mod.WAKE_GATE_LIVE_LOG, [
        {"gate": "live", "action": "skip", "at": NOW - 60},
        {"gate": "live", "action": "wake", "at": True},
    ])
    assert _main(tmp_path, "wake-gate", NOW) == mod.UNKNOWN


def test_irrelevant_rows_with_bad_stamps_do_not_block(tmp_path):
    # Only decided System One rows and live wake-gate actions are evidence; other rows are not judged.
    path = tmp_path / mod.DECISION_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"source": "system0", "reason": "path rule", "at": 1e999}\n'
                    '{"source": "system1", "reason": "decided", "at": %r}\n' % (NOW - 60))
    assert _main(tmp_path, "decision", NOW, _budget(tmp_path)) == mod.DONE


def test_shadow_escalated_argmax_rows_do_not_count(tmp_path):
    # 507f2a6d logs below-threshold starts with an argmax choice and escalated true; those are not decided answers.
    escalated = [_start({"choice": "wake", "escalated": True, "confidence": 0.4}) for _ in range(60)]
    _write(tmp_path / "decision-log" / "wake-gate.jsonl", escalated)
    code, detail = mod.shadow_check(tmp_path)
    assert code == mod.OPEN
    assert "0 decided" in detail and "60 escalated" in detail
    decided = [_start({"choice": "wake", "escalated": False, "confidence": 0.9}) for _ in range(50)]
    _write(tmp_path / "decision-log" / "wake-gate.jsonl", escalated + decided)
    assert mod.shadow_check(tmp_path)[0] == mod.DONE
