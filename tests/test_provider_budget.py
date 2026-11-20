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
    state, reason = budget.evaluate(evidence, "claude", now=100)
    assert state == "RED"
    assert f"5h {verdict} (20% used)" in reason



def test_provider_signal_missing_stale_and_recovery(tmp_path):
    signal_store.register(tmp_path, "budget-claude", max_age=900, subscribers=[])
    assert signal_store.read(tmp_path, "budget-claude", now=100).state != "OK"
    assert budget.publish(tmp_path, rows(), now=100)["state"] == "GREEN"
    assert signal_store.read(tmp_path, "budget-claude", now=101).value == "GREEN"
    assert signal_store.read(tmp_path, "budget-claude", now=1001).state != "OK"
    assert budget.publish(tmp_path, [], now=1002)["state"] == "UNKNOWN"
    assert signal_store.read(tmp_path, "budget-claude", now=1002).state == "UNKNOWN"


def test_red_and_green_readings_carry_their_cause(tmp_path):
    # A subscriber woken by a RED edge must be able to read why without re-deriving it; the
    # verdict's reason was previously dropped for every non-UNKNOWN state (2026-11-16).
    signal_store.register(tmp_path, "budget-claude", max_age=900, subscribers=[])
    burning = rows(); burning[1]["verdict"] = "RUNS OUT BEFORE RESET"
    assert budget.publish(tmp_path, burning, now=100)["state"] == "RED"
    red = signal_store.read(tmp_path, "budget-claude", now=101)
    assert red.value == "RED"
    assert red.reason == ("provider's existing quota or burn rule holds spontaneous work: "
                          "7d RUNS OUT BEFORE RESET (20% used)")
    assert budget.publish(tmp_path, rows(), now=110)["state"] == "GREEN"
    green = signal_store.read(tmp_path, "budget-claude", now=111)
    assert green.value == "GREEN"
    assert green.reason == "both provider windows satisfy the existing budget policy"


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


@pytest.mark.parametrize("used,expected", [(0.01, "GREEN"), (0.1, "RED")])
def test_engine_budget_forecast_needs_a_nontrivial_used_fraction(tmp_path, used, expected):
    # 2026-11-18: a 1%-used 7d window ~31 min after reset had eta ~52 h against a
    # reset ~161 h away, from one 1% step. That short extrapolation must not hold work.
    now = 10_000.0
    _write_engine_budget(tmp_path, _engine_budget(now, used=used, eta_s=100, reset_at=now + 500_000))
    signal_store.register(tmp_path, "budget-claude", max_age=900, subscribers=[])
    result = budget.publish(tmp_path, budget.rows_from_engine_budget(tmp_path, now), now=now)
    assert result["state"] == expected


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


def test_unknown_reason_names_the_failing_account_read():
    # 2026-11-17: the live claude UNKNOWN said only "collection or accounting incomplete" while the
    # account row already carried the cause; a subscriber re-derives nothing from a fixed phrase.
    evidence = rows() + [dict(account="claude",
                              error="claude: account state UNREADABLE (five_hour reset not yet reported)")]
    state, reason = budget.evaluate(evidence, "claude", now=100)
    assert state == "UNKNOWN"
    assert "account state UNREADABLE" in reason
    assert "five_hour reset not yet reported" in reason


def test_unsupported_provider_contract_stays_unknown():
    assert budget.evaluate(rows(), "go-a", now=100)[0] == "UNKNOWN"


def test_codex_forecast_is_published_beside_claude(tmp_path):
    now = 10_000.0
    data = _engine_budget(now)
    data["accounts"]["codex-a"] = {"state": "FRESH", "windows": {
        "5h": dict(used=0.2, reset_at=now + 3600, observed_at=now - 10, state="FRESH", eta_s=None),
        "7d": dict(used=0.47, reset_at=now + 100_000, observed_at=now - 10, state="FRESH", eta_s=1000),
    }}
    _write_engine_budget(tmp_path, data)
    result = budget.publish(tmp_path, budget.rows_from_engine_budget(tmp_path, now), now=now)
    assert result["state"] == "GREEN"
    assert result["accounts"]["codex-a"]["state"] == "RED"
    assert "7d RUNS OUT BEFORE RESET (47% used)" in result["accounts"]["codex-a"]["reason"]
    assert "budget-codex" not in signal_store.registry(tmp_path)
    assert not (tmp_path / "signals/budget-codex.json").exists()


def test_publish_records_cadence_beat_even_when_input_unreadable(tmp_path):
    # A producer that runs on time and declares budget-claude UNKNOWN must leave a cadence beat:
    # observe_unknown omits .seen on purpose, and coverage reads .ran to tell a live producer with
    # a bad input from one that missed its cadence (2026-11-20 signal-coverage false cause).
    signal_store.register(tmp_path, "budget-claude", max_age=900, subscribers=[])
    bad = rows() + [dict(account="claude", error="claude: five_hour reset not yet reported")]
    result = budget.publish(tmp_path, bad, now=10_000.0)
    assert result["state"] == "UNKNOWN"
    assert (tmp_path / "signals" / "budget-claude.ran").exists()
    assert signal_store.recorded(tmp_path, "budget-claude")["state"] == "UNKNOWN"
    assert not (tmp_path / "signals" / "budget-claude.seen").exists()
