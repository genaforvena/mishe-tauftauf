import json
from datetime import datetime, timezone

import pytest


NOW = datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc).timestamp()


def report():
    return dict(type="result", subtype="success", is_error=False, local_command="usage",
                duration_api_ms=0, num_turns=0, total_cost_usd=0, modelUsage={},
                usage=dict(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0),
                result="You are currently using your subscription to power your Claude Code usage\n"
                       "Current session: 20% used · resets Nov 6, 4:30pm (UTC)\n"
                       "Current week (all models): 43% used · resets Nov 11, 3am (UTC)\n")


def test_native_usage_without_inference():
    from mishe_tauftauf.claude_quota_text import read_usage
    row = read_usage(json.dumps(report()), now=NOW)
    assert row["five_hour"]["used_percentage"] == 20
    assert row["seven_day"]["resets_at"] == datetime(2026, 11, 11, 3, tzinfo=timezone.utc).timestamp()
    assert row["source"] == "claude-native-usage"
    assert row["reset_precision_seconds"] == 60


@pytest.mark.parametrize("change", [dict(local_command="other"), dict(num_turns=1),
    dict(duration_api_ms=1), dict(modelUsage={"haiku": {}}), dict(total_cost_usd=.01),
    dict(is_error=True), dict(usage={}), dict(usage=dict(input_tokens=1)),
    dict(result="Current session: 20% used · resets Nov 6, 4:30pm (UTC)")])
def test_non_native_or_incomplete_report_is_rejected(change):
    from mishe_tauftauf.claude_quota_text import read_usage
    r = report(); r.update(change)
    with pytest.raises(ValueError): read_usage(json.dumps(r), now=NOW)


@pytest.mark.parametrize("old,new", [("20%", "101%"), ("4:30pm", "1pm"),
    ("Nov 11", "Nov 30"), ("(UTC)", "(PST)"), ("20%", "NaN%")])
def test_invalid_percent_reset_or_timezone_is_rejected(old, new):
    from mishe_tauftauf.claude_quota_text import read_usage
    r = report(); r["result"] = r["result"].replace(old, new)
    with pytest.raises(ValueError): read_usage(json.dumps(r), now=NOW)


def test_duplicate_window_is_rejected():
    from mishe_tauftauf.claude_quota_text import read_usage
    r = report(); r["result"] += r["result"].splitlines()[1] + "\n"
    with pytest.raises(ValueError): read_usage(json.dumps(r), now=NOW)


def test_year_rollover():
    from mishe_tauftauf.claude_quota_text import read_usage
    now = datetime(2026, 12, 31, 23, tzinfo=timezone.utc).timestamp()
    r = report(); r["result"] = r["result"].replace("Nov 6, 4:30pm", "Jan 1, 1am").replace("Nov 11, 3am", "Jan 4, 3am")
    assert read_usage(json.dumps(r), now=now)["five_hour"]["resets_at"] == datetime(2027, 1, 1, 1, tzinfo=timezone.utc).timestamp()


def test_failure_preserves_raw_and_last_good_and_never_refreshes_windows(tmp_path):
    from mishe_tauftauf.claude_quota import collect
    from mishe_tauftauf.state_files import read_json
    import subprocess
    def good(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, json.dumps(report()), "")
    assert collect(tmp_path, now=NOW, runner=good)["state"] == "FRESH"
    before = read_json(tmp_path / "operator/budget/claude-native-quota.json")
    def bad(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, "malformed-native-output", "failed")
    assert collect(tmp_path, now=NOW + 600, runner=bad)["state"] == "UNKNOWN"
    current = read_json(tmp_path / "operator/budget/claude-native-quota.json")
    assert "five_hour" not in current
    evidence = [read_json(p) for p in (tmp_path / "operator/budget/native-evidence").glob("*.json")]
    assert any(e.get("previous") == before and e.get("stdout") == "malformed-native-output" for e in evidence)


def test_timeout_is_unknown(tmp_path):
    from mishe_tauftauf.claude_quota import collect
    import subprocess
    def timeout(*args, **kwargs): raise subprocess.TimeoutExpired(args[0], 45, output=b"partial")
    assert collect(tmp_path, now=NOW, runner=timeout)["state"] == "UNKNOWN"


# Fixture: the /usage text captured at the 5 h reset instant, 02:20Z 2026-11-11
# (operator/budget/native-evidence/1794363599532186770-340232cf31e54e25bf0ce512b87f3c75.json).
RESET_INSTANT_NOW = 1794363599.5321822
RESET_INSTANT_TEXT = ("You are currently using your subscription to power your Claude Code usage\n\n"
                      "Current session: 0% used\n"
                      "Current week (all models): 99% used · resets Nov 11, 2:59am (UTC)\n"
                      "Current week (Fable): 0% used · resets Nov 11, 3am (UTC)\n\n"
                      "What's contributing to your usage?\n")


def reset_instant_report(text=RESET_INSTANT_TEXT):
    row = report()
    row["result"] = text
    return row


def test_reset_instant_without_session_reset_is_unknown_and_never_zero():
    from mishe_tauftauf.claude_quota_text import read_usage
    result = read_usage(json.dumps(reset_instant_report()), now=RESET_INSTANT_NOW)
    assert result["state"] == "UNKNOWN"
    assert "five_hour" not in result
    assert result["seven_day"]["used_percentage"] == 99
    assert result["seven_day"]["resets_at"] == datetime(2026, 11, 11, 2, 59, tzinfo=timezone.utc).timestamp()


def test_reset_instant_collect_is_unknown_and_writes_no_rate_history(tmp_path):
    from mishe_tauftauf.claude_quota import collect
    import subprocess
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, json.dumps(reset_instant_report()), "")
    assert collect(tmp_path, now=RESET_INSTANT_NOW, runner=runner)["state"] == "UNKNOWN"
    assert not (tmp_path / "operator/budget/claude-rate-history.jsonl").exists()


def test_session_without_reset_is_rejected_unless_zero_percent():
    from mishe_tauftauf.claude_quota_text import read_usage
    bad = RESET_INSTANT_TEXT.replace("Current session: 0% used", "Current session: 20% used")
    with pytest.raises(ValueError):
        read_usage(json.dumps(reset_instant_report(bad)), now=RESET_INSTANT_NOW)
