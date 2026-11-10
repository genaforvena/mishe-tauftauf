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
