import json

import pytest

from mishe_tauftauf import system1_savings as s

NOW = 1_800_000_000.0


def _write_rows(home, rows):
    path = home / "decision-log" / "wake-gate.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _budget(home, observed=NOW):
    path = home / "engine" / "budget.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"routerai": {"decision": {
        "calls_today": 3, "daily_cap": 2000, "spent": 0.01, "spend_limit": 50.0, "observed_at": observed}}}),
        encoding="utf-8")


def _wake(key, ts, trigger, same, result):
    return [
        {"kind": "start", "key": key, "role": "x", "ts": ts, "trigger": trigger, "same_dossier_as_last": same},
        {"kind": "end", "key": key, "role": "x", "ts": ts + 5, "state": "yielded", "result": result},
    ]


def _signal_wakes(name, count, changed):
    """Count wakes co-firing on one signal, with `changed` of them yielding changed."""
    rows = []
    for i in range(count):
        key = f"{name}-{i}"
        result = "changed" if i < changed else "verified"
        rows += [
            {"kind": "start", "key": key, "role": "x", "ts": NOW - 100 - i,
             "trigger": "signal:" + name, "reasons": ["signal:" + name],
             "same_dossier_as_last": False},
            {"kind": "end", "key": key, "role": "x", "ts": NOW - 95 - i,
             "state": "yielded", "result": result},
        ]
    return rows


def test_measures_trigger_classes_and_projected_skip(tmp_path):
    rows = []
    rows += _wake("a", NOW - 100, "signal:ci", False, "changed")
    rows += _wake("b", NOW - 200, "inbox", True, "verified")
    rows += _wake("c", NOW - 300, "intention:again", True, "changed")
    rows += _wake("old", NOW - 2 * 86400, "inbox", False, "changed")  # outside the window
    _write_rows(tmp_path, rows)
    _budget(tmp_path)
    out = s.measure(tmp_path, now=NOW)
    assert out["starts"] == 3
    assert out["starts_by_trigger"] == {"signal": 1, "inbox": 1, "intention": 1}
    assert out["changed_by_trigger"] == {
        "inbox": {"ended": 1, "yielded": 1, "changed": 0, "changed_rate": 0.0},
        "intention": {"ended": 1, "yielded": 1, "changed": 1, "changed_rate": 1.0},
        "signal": {"ended": 1, "yielded": 1, "changed": 1, "changed_rate": 1.0},
    }
    assert out["gate"] == "off" and out["avoided"] == 0
    assert out["projected_skip"] == 2
    assert out["changed_rate_all"] == round(2 / 3, 4)
    assert out["changed_rate_projected_skip"] == 0.5
    assert out["decision"]["calls_today"] == 3


def test_trigger_rates_use_clean_yields_and_retain_failed_end_counts(tmp_path):
    rows = _wake("changed", NOW - 100, "signal:ci", False, "changed")
    rows += _wake("blocked", NOW - 90, "signal:ci", False, "blocked")
    rows += _wake("verified", NOW - 85, "signal:ci", False, "verified")
    rows += _wake("failed", NOW - 80, "signal:ci", False, "changed")
    rows[-1]["state"] = "unknown"  # the engine failed after writing the receipt
    rows += _wake("no-result", NOW - 70, "signal:ci", False, None)
    rows += _wake("failed-only", NOW - 60, "inbox", False, None)
    rows[-1]["state"] = "unknown"
    rows += _wake("running", NOW - 50, "intention:again", False, None)[:1]
    _write_rows(tmp_path, rows)
    _budget(tmp_path)

    out = s.measure(tmp_path, now=NOW)

    assert out["changed_by_trigger"] == {
        "inbox": {"ended": 1, "yielded": 0, "changed": 0, "changed_rate": None},
        "intention": {"ended": 0, "yielded": 0, "changed": 0, "changed_rate": None},
        "signal": {"ended": 5, "yielded": 3, "changed": 1, "changed_rate": 0.3333},
    }
    assert out["ended"] == 6
    assert out["results"] == {"blocked": 1, "changed": 2, "none": 2, "verified": 1}


def test_per_signal_rates_from_reasons_with_cofiring(tmp_path):
    rows = [
        {"kind": "start", "key": "w1", "role": "x", "ts": NOW - 100,
         "trigger": "signal:a",
         "reasons": ["signal:a", "signal:b", "signal:a", "signal:", 42, None, "intention:again"],
         "same_dossier_as_last": False},
        {"kind": "end", "key": "w1", "role": "x", "ts": NOW - 95, "state": "yielded", "result": "changed"},
        {"kind": "start", "key": "w2", "role": "x", "ts": NOW - 90,
         "trigger": "signal:b", "reasons": ["signal:b"], "same_dossier_as_last": False},
        {"kind": "end", "key": "w2", "role": "x", "ts": NOW - 85, "state": "yielded", "result": "verified"},
    ]
    rows += _wake("w3", NOW - 80, "inbox", False, "changed")  # no signal reasons
    _write_rows(tmp_path, rows)
    _budget(tmp_path)
    out = s.measure(tmp_path, now=NOW)
    assert out["changed_by_signal"] == {
        "a": {"ended": 1, "yielded": 1, "changed": 1, "changed_rate": 1.0},
        "b": {"ended": 2, "yielded": 2, "changed": 1, "changed_rate": 0.5},
    }
    assert out["retirement_candidates"] == []


def test_retirement_candidate_needs_volume_and_low_rate(tmp_path):
    rows = []
    rows += _signal_wakes("wasteful", 20, 1)    # 0.05 -> flag
    rows += _signal_wakes("productive", 20, 4)  # 0.20 -> no flag
    rows += _signal_wakes("rare", 5, 0)         # low volume -> exempt
    _write_rows(tmp_path, rows)
    _budget(tmp_path)
    out = s.measure(tmp_path, now=NOW)
    assert out["changed_by_signal"]["wasteful"]["changed_rate"] == 0.05
    assert out["retirement_candidates"] == ["wasteful"]


def test_retirement_candidate_excludes_signals_with_no_clean_yields(tmp_path):
    rows = []
    for i in range(20):
        key = f"unknown-{i}"
        rows += [
            {"kind": "start", "key": key, "role": "x", "ts": NOW - 100 - i,
             "trigger": "signal:unknown", "reasons": ["signal:unknown"], "same_dossier_as_last": False},
            {"kind": "end", "key": key, "role": "x", "ts": NOW - 95 - i,
             "state": "yielded", "result": "none"},
        ]
    _write_rows(tmp_path, rows)
    _budget(tmp_path)
    out = s.measure(tmp_path, now=NOW)
    assert out["changed_by_signal"]["unknown"]["changed_rate"] is None
    assert out["retirement_candidates"] == []


def test_live_gate_counts_skips_in_window(tmp_path):
    _write_rows(tmp_path, _wake("a", NOW - 10, "inbox", True, "verified"))
    _budget(tmp_path)
    live = tmp_path / "decision-log" / "wake-gate-live.jsonl"
    live.write_text(json.dumps({"action": "skip", "ts": NOW - 10}) + "\n"
                    + json.dumps({"action": "skip", "ts": NOW - 2 * 86400}) + "\n", encoding="utf-8")
    out = s.measure(tmp_path, now=NOW)
    assert out["gate"] == "live" and out["avoided"] == 1


def test_missing_wake_ledger_is_unknown_not_green(tmp_path):
    _budget(tmp_path)
    code, line = s.duty(tmp_path, now=NOW)
    assert code == s.UNKNOWN and line.startswith("UNKNOWN")


def test_stale_or_missing_budget_is_unknown(tmp_path):
    _write_rows(tmp_path, _wake("a", NOW - 10, "inbox", False, "changed"))
    _budget(tmp_path, observed=NOW - 7 * 3600)
    assert s.duty(tmp_path, now=NOW)[0] == s.UNKNOWN
    (tmp_path / "engine" / "budget.json").unlink()
    assert s.duty(tmp_path, now=NOW)[0] == s.UNKNOWN


@pytest.mark.parametrize("ledger", [
    "",
    "{not json\n",
    'null\n{"kind":"end","key":"orphan"}\n',
    '{"kind":"start","key":"missing-timestamp"}\n',
    '{"kind":"start","key":"nonfinite-timestamp","ts":"Infinity"}\n',
])
def test_ledger_without_readable_start_is_unknown(tmp_path, ledger):
    path = tmp_path / "decision-log" / "wake-gate.jsonl"
    path.parent.mkdir()
    path.write_text(ledger, encoding="utf-8")
    _budget(tmp_path)
    code, line = s.duty(tmp_path, now=NOW)
    assert code == s.UNKNOWN
    assert "wake-gate ledger has no readable start rows" in line


def test_torn_wake_line_preserves_readable_history_outside_window(tmp_path):
    _write_rows(tmp_path, _wake("old", NOW - 86401, "inbox", False, "changed"))
    path = tmp_path / "decision-log" / "wake-gate.jsonl"
    path.write_text("{torn\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    _budget(tmp_path)
    code, line = s.duty(tmp_path, now=NOW)
    assert code == s.GREEN
    numbers = json.loads(line.removeprefix("GREEN: "))
    assert numbers["starts"] == numbers["ended"] == 0


def test_main_prints_one_line_verdict(tmp_path, capsys):
    # main reads the real clock, so the budget observed_at is not fresh here: the verdict is UNKNOWN, and printed.
    _write_rows(tmp_path, _wake("a", NOW - 10, "inbox", False, "changed"))
    _budget(tmp_path)
    code = s.main(["--home", str(tmp_path)])
    out = capsys.readouterr().out
    assert code in (s.GREEN, s.UNKNOWN)
    assert out.startswith("mishe: system1-savings: ")
