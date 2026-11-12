import copy
import pytest
from mishe_tauftauf import provider_budget as budget, signal_store


def rows():
    return [dict(account="claude", window=w, state="FRESH", used=.2, reset=200, verdict="ok")
            for w in ("5h", "7d")]


def test_other_provider_forecast_does_not_hold_healthy_claude():
    evidence = rows() + [dict(account="codex-a", window="7d", state="FRESH", verdict="RUNS OUT BEFORE RESET")]
    assert budget.evaluate(evidence, "claude", now=100)[0] == "GREEN"


@pytest.mark.parametrize("change", ["missing", "duplicate", "stale", "nan", "unknown", "expired", "accounting"])
def test_missing_or_broken_provider_evidence_stays_unknown(change):
    evidence = rows()
    if change == "missing": evidence.pop()
    if change == "duplicate": evidence.append(copy.deepcopy(evidence[0]))
    if change == "stale": evidence[0]["state"] = "STALE"
    if change == "nan": evidence[0]["used"] = float("nan")
    if change == "unknown": evidence[0]["verdict"] = "unknown"
    if change == "expired": evidence[0]["reset"] = 99
    if change == "accounting": evidence.append(dict(error="usage observation incomplete"))
    assert budget.evaluate(evidence, "claude", now=100)[0] == "UNKNOWN"


@pytest.mark.parametrize("verdict", ["LOW", "RUNS OUT BEFORE RESET"])
def test_existing_provider_policy_still_holds_work(verdict):
    evidence = rows(); evidence[0]["verdict"] = verdict
    assert budget.evaluate(evidence, "claude", now=100)[0] == "RED"


def test_provider_signal_missing_stale_and_recovery(tmp_path):
    signal_store.register(tmp_path, "budget-claude", max_age=900, subscribers=[])
    assert signal_store.read(tmp_path, "budget-claude", now=100).state != "OK"
    assert budget.publish(tmp_path, rows(), now=100)["state"] == "GREEN"
    assert signal_store.read(tmp_path, "budget-claude", now=101).value == "GREEN"
    assert signal_store.read(tmp_path, "budget-claude", now=1001).state != "OK"
    assert budget.publish(tmp_path, [], now=1002)["state"] == "UNKNOWN"
    assert signal_store.read(tmp_path, "budget-claude", now=1002).state == "UNKNOWN"


def _engine_budget(now, **over):
    window = dict(used=0.2, reset_at=now + 3600, observed_at=now - 10, state="FRESH", eta_s=None)
    window.update(over)
    return {"accounts": {"claude": {"state": "FRESH", "windows": {"5h": dict(window), "7d": dict(window)}}}}


def _write_engine_budget(home, data):
    import json
    (home / "engine").mkdir(parents=True, exist_ok=True)
    (home / "engine" / "budget.json").write_text(json.dumps(data), encoding="utf-8")


def test_engine_budget_fresh_claude_publishes_green(tmp_path):
    now = 10_000.0
    _write_engine_budget(tmp_path, _engine_budget(now))
    signal_store.register(tmp_path, "budget-claude", max_age=900, subscribers=[])
    result = budget.publish(tmp_path, budget.rows_from_engine_budget(tmp_path, now), now=now)
    assert result["state"] == "GREEN"


def test_engine_budget_missing_file_stays_unknown(tmp_path):
    rows = budget.rows_from_engine_budget(tmp_path, 10_000.0)
    assert budget.evaluate(rows, "claude", now=10_000.0)[0] == "UNKNOWN"


def test_engine_budget_stale_observation_stays_unknown(tmp_path):
    now = 10_000.0
    _write_engine_budget(tmp_path, _engine_budget(now, observed_at=now - 3000))
    assert budget.evaluate(budget.rows_from_engine_budget(tmp_path, now), "claude", now=now)[0] == "UNKNOWN"


def test_engine_budget_spent_or_burning_window_holds_work(tmp_path):
    now = 10_000.0
    _write_engine_budget(tmp_path, _engine_budget(now, used=0.95))
    assert budget.evaluate(budget.rows_from_engine_budget(tmp_path, now), "claude", now=now)[0] == "RED"
    _write_engine_budget(tmp_path, _engine_budget(now, eta_s=100))
    assert budget.evaluate(budget.rows_from_engine_budget(tmp_path, now), "claude", now=now)[0] == "RED"


def test_engine_budget_malformed_account_stays_unknown(tmp_path):
    now = 10_000.0
    data = _engine_budget(now)
    data["accounts"]["claude"]["windows"]["5h"]["used"] = "0.2"
    _write_engine_budget(tmp_path, data)
    assert budget.evaluate(budget.rows_from_engine_budget(tmp_path, now), "claude", now=now)[0] == "UNKNOWN"


def test_other_account_error_does_not_blind_healthy_claude():
    # The live shape: claude FRESH, codex-a STALE contributes a tagged error row.
    evidence = rows() + [dict(account="codex-a", error="codex-a: account state STALE")]
    assert budget.evaluate(evidence, "claude", now=100)[0] == "GREEN"


def test_untagged_error_still_holds_claude_unknown():
    evidence = rows() + [dict(error="engine/budget.json unreadable")]
    assert budget.evaluate(evidence, "claude", now=100)[0] == "UNKNOWN"
