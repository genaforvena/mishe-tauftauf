import json

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
    assert out["gate"] == "off" and out["avoided"] == 0
    assert out["projected_skip"] == 2
    assert out["changed_rate_all"] == round(2 / 3, 4)
    assert out["changed_rate_projected_skip"] == 0.5
    assert out["decision"]["calls_today"] == 3


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


def test_torn_wake_line_is_skipped_not_fatal(tmp_path):
    # read_json_lines skips a torn line; the duty still measures the rows it can read and never reports RED.
    path = tmp_path / "decision-log" / "wake-gate.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{not json\n", encoding="utf-8")
    _budget(tmp_path)
    code, line = s.duty(tmp_path, now=NOW)
    assert code == s.GREEN
    assert '"starts": 0' in line


def test_main_prints_one_line_verdict(tmp_path, capsys):
    # main reads the real clock, so the budget observed_at is not fresh here: the verdict is UNKNOWN, and printed.
    _write_rows(tmp_path, _wake("a", NOW - 10, "inbox", False, "changed"))
    _budget(tmp_path)
    code = s.main(["--home", str(tmp_path)])
    out = capsys.readouterr().out
    assert code in (s.GREEN, s.UNKNOWN)
    assert out.startswith("mishe: system1-savings: ")
