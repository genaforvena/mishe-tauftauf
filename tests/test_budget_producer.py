import json
import math
from pathlib import Path

import pytest

from mishe_tauftauf import budget_producer as bp

NOW = 1794365500.0


def tap(**overrides):
    base = {
        "five_hour": {"observed_at": NOW - 10, "resets_at": NOW + 3600, "used_percentage": 2},
        "seven_day": {"observed_at": NOW - 10, "resets_at": NOW + 3600, "used_percentage": 99},
        "observed_at": NOW - 10,
        "source": "claude-native-usage",
        "state": "FRESH",
    }
    base.update(overrides)
    return base


def status(**windows):
    return {"at": NOW, "accounts": {"go-a": {"provider": "opencode-go", "state": "FRESH", "windows": windows}}}


def test_claude_fresh_tap_maps_to_used_fraction_and_source():
    accounts = bp.build_accounts(None, tap(), NOW)
    claude = accounts["claude"]
    assert claude["state"] == "FRESH"
    assert claude["windows"]["5h"]["used"] == pytest.approx(0.02)
    assert claude["windows"]["5h"]["remaining"] == pytest.approx(0.98)
    assert claude["windows"]["7d"]["used"] == pytest.approx(0.99)
    assert claude["windows"]["7d"]["source"] == "claude-native-usage"


def test_null_resets_at_omits_the_window_and_never_reads_as_zero():
    # Senses' reproduced crash: used=0 with resets_at null. The window must be absent
    # (fail closed), not a 0% reading.
    accounts = bp.build_accounts(None, tap(five_hour={"observed_at": NOW - 10, "resets_at": None, "used_percentage": 0}), NOW)
    assert "5h" not in accounts["claude"]["windows"]
    assert accounts["claude"]["state"] == "FRESH"  # the 7d window alone is still fresh


def test_valid_zero_percent_with_future_reset_is_fresh_zero():
    accounts = bp.build_accounts(None, tap(five_hour={"observed_at": NOW - 10, "resets_at": NOW + 60, "used_percentage": 0}), NOW)
    assert accounts["claude"]["windows"]["5h"]["used"] == 0
    assert accounts["claude"]["windows"]["5h"]["state"] == "FRESH"


@pytest.mark.parametrize("bad", [math.nan, math.inf, "2", True, None])
def test_non_numeric_or_non_finite_fields_are_omitted(bad):
    accounts = bp.build_accounts(None, tap(seven_day={"observed_at": NOW - 10, "resets_at": NOW + 60, "used_percentage": bad}), NOW)
    assert "7d" not in accounts["claude"]["windows"]


def test_past_reset_is_stale_not_fresh():
    accounts = bp.build_accounts(None, tap(seven_day={"observed_at": NOW - 10, "resets_at": NOW - 1, "used_percentage": 50}), NOW)
    assert accounts["claude"]["windows"]["7d"]["state"] == "STALE"
    assert accounts["claude"]["state"] == "STALE"


def test_old_observation_is_stale_even_with_future_reset():
    old = NOW - bp.MAX_AGE_S - 1
    accounts = bp.build_accounts(None, tap(five_hour={"observed_at": old, "resets_at": NOW + 60, "used_percentage": 2}), NOW)
    assert accounts["claude"]["windows"]["5h"]["state"] == "STALE"


def test_unknown_or_missing_tap_is_unreadable_never_fresh():
    for bad in (None, {}, tap(state="UNKNOWN"), tap(source="other")):
        accounts = bp.build_accounts(None, bad, NOW)
        assert accounts["claude"]["state"] == "UNREADABLE"
        assert accounts["claude"]["windows"] == {}


def test_engine_account_uses_remaining_and_keeps_its_own_provider():
    s = status(**{"7d": {"remaining": 0.25, "reset_at": NOW + 600, "state": "FRESH", "observed_at": NOW - 5}})
    accounts = bp.build_accounts(s, tap(), NOW)
    assert accounts["go-a"]["provider"] == "opencode-go"
    assert accounts["go-a"]["windows"]["7d"]["used"] == pytest.approx(0.75)
    assert accounts["go-a"]["state"] == "FRESH"


def test_account_fetched_time_is_the_window_observation_and_the_top_level_at_is_never_used():
    # the collector writes the engine's fetched_at as the account observed_at; the top-level `at` is only collection time
    s = {"at": NOW - 5, "accounts": {"go-a": {"provider": "opencode-go", "state": "FRESH", "observed_at": NOW - 20,
                                              "windows": {"7d": {"remaining": 0.25, "reset_at": NOW + 600, "state": "FRESH"}}}}}
    accounts = bp.build_accounts(s, tap(), NOW)
    assert accounts["go-a"]["state"] == "FRESH"
    assert accounts["go-a"]["windows"]["7d"]["observed_at"] == NOW - 20


def test_engine_window_with_no_observation_time_is_dropped_not_stamped_with_collection_time():
    # no fetched_at anywhere: never the top-level `at`, which is when the collector ran
    s = {"at": NOW - 5, "accounts": {"go-a": {"provider": "opencode-go", "state": "FRESH",
                                              "windows": {"7d": {"remaining": 0.25, "reset_at": NOW + 600, "state": "FRESH"}}}}}
    assert bp.build_accounts(s, tap(), NOW)["go-a"]["state"] == "UNREADABLE"


def test_engine_window_with_no_observation_time_is_still_dropped():
    s = {"accounts": {"go-a": {"provider": "opencode-go", "windows": {"7d": {"remaining": 0.25, "reset_at": NOW + 600}}}}}
    assert bp.build_accounts(s, tap(), NOW)["go-a"]["state"] == "UNREADABLE"


@pytest.mark.parametrize("source_state", ["STALE", "UNKNOWN", "UNATTRIBUTED", "UNREADABLE"])
def test_engine_window_labelled_non_fresh_stays_non_fresh_even_when_observed_now(source_state):
    # the upstream label wins over the producer's own timing: a STALE or UNKNOWN source is never FRESH
    s = {"at": NOW, "accounts": {"codex-a": {"provider": "openai-codex", "state": "FRESH", "observed_at": NOW - 1,
                                             "windows": {"5h": {"remaining": 0.78, "reset_at": NOW + 600, "state": source_state}}}}}
    accounts = bp.build_accounts(s, tap(), NOW)
    assert accounts["codex-a"]["windows"]["5h"]["state"] == source_state
    assert accounts["codex-a"]["state"] != "FRESH"


@pytest.mark.parametrize("account_state", ["STALE", "UNKNOWN", None])
def test_non_fresh_or_missing_account_state_makes_every_window_non_fresh(account_state):
    row = {"provider": "openai-codex", "observed_at": NOW - 1,
           "windows": {"5h": {"remaining": 0.78, "reset_at": NOW + 600, "state": "FRESH"}}}
    if account_state is not None:
        row["state"] = account_state
    accounts = bp.build_accounts({"accounts": {"codex-a": row}}, tap(), NOW)
    assert accounts["codex-a"]["windows"]["5h"]["state"] != "FRESH"
    assert accounts["codex-a"]["state"] != "FRESH"


def test_build_budget_with_a_fresh_collection_time_still_reports_upstream_stale_windows():
    # the DM's case: quota at=now but the codex-a window was observed as STALE upstream
    s = {"at": NOW, "accounts": {"codex-a": {"provider": "openai-codex", "state": "FRESH", "observed_at": NOW - 1400,
                                             "windows": {"5h": {"remaining": 0.78, "reset_at": NOW + 600, "state": "STALE"}}}}}
    payload = bp.build_budget(s, tap(), NOW)
    assert payload["accounts"]["codex-a"]["state"] == "STALE"
    assert payload["accounts"]["codex-a"]["windows"]["5h"]["state"] == "STALE"


def test_window_observed_past_max_age_is_stale_even_when_labelled_fresh():
    s = {"at": NOW, "accounts": {"codex-a": {"provider": "openai-codex", "state": "FRESH", "observed_at": NOW - bp.MAX_AGE_S - 1,
                                             "windows": {"5h": {"remaining": 0.78, "reset_at": NOW + 600, "state": "FRESH"}}}}}
    assert bp.build_accounts(s, tap(), NOW)["codex-a"]["windows"]["5h"]["state"] == "STALE"


def test_engine_account_without_windows_is_unreadable():
    s = {"accounts": {"codex-a": {"provider": "openai-codex", "state": "UNKNOWN", "windows": {}}}}
    assert bp.build_accounts(s, tap(), NOW)["codex-a"]["state"] == "UNREADABLE"


def test_unreadable_engine_status_still_produces_claude_and_says_so():
    payload = bp.build_budget(None, tap(), NOW)
    assert payload["schema"] == 1
    assert "accounts" in payload and "claude" in payload["accounts"]
    assert payload["unknowns"] == ["engine status unavailable"]


def test_write_atomic_replaces_whole_file_and_leaves_no_temp(tmp_path: Path):
    out = tmp_path / "engine" / "budget.json"
    bp.write_atomic(out, bp.build_budget(None, tap(), NOW))
    first = json.loads(out.read_text())
    bp.write_atomic(out, bp.build_budget(None, None, NOW))
    second = json.loads(out.read_text())
    assert first["accounts"]["claude"]["state"] == "FRESH"
    assert second["accounts"]["claude"]["state"] == "UNREADABLE"
    assert [p.name for p in out.parent.iterdir()] == ["budget.json"]


def test_cli_dry_run_reads_real_shaped_files(tmp_path: Path, capsys):
    budget = tmp_path / "operator" / "budget"
    budget.mkdir(parents=True)
    (budget / "claude-native-quota.json").write_text(json.dumps(tap(five_hour={"observed_at": NOW, "resets_at": None, "used_percentage": 0})))
    assert bp.main(tmp_path) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == 1
    assert "5h" not in out["accounts"]["claude"]["windows"]


def test_routing_without_routes_file_is_unknown_not_green(tmp_path: Path):
    payload = bp.build_budget(None, tap(), NOW)
    routing = bp.build_routing(tmp_path, payload, NOW)
    assert routing["state"] == "UNKNOWN"
    assert "routes.json" in routing["reason"]


def test_router_reages_a_window_admitted_on_a_stale_label(tmp_path: Path):
    # the repro: produced 800 s ago, observation 1400 s ago, labelled FRESH by the producer at that time
    from mishe_tauftauf import engine_router as er

    payload = {"schema": 1, "produced_at": NOW - 800, "accounts": {"codex-a": {
        "provider": "openai-codex", "state": "FRESH", "windows": {
            "5h": {"remaining": 0.78, "reset_at": NOW + 600, "observed_at": NOW - 1400, "state": "FRESH"}}}}}
    status = er.status_from_payload(payload, NOW)
    row = status["accounts"]["codex-a"]
    assert row["state"] == "STALE"
    assert row["windows"][0]["state"] == "STALE"


def test_router_keeps_a_window_that_is_still_fresh_at_decision_time():
    from mishe_tauftauf import engine_router as er

    payload = {"schema": 1, "produced_at": NOW - 10, "accounts": {"codex-a": {
        "provider": "openai-codex", "state": "FRESH", "windows": {
            "5h": {"remaining": 0.78, "reset_at": NOW + 600, "observed_at": NOW - 60, "state": "FRESH"}}}}}
    row = er.status_from_payload(payload, NOW)["accounts"]["codex-a"]
    assert row["state"] == "FRESH"


def test_router_never_upgrades_a_non_fresh_row_and_drops_a_fresh_row_without_windows():
    from mishe_tauftauf import engine_router as er

    payload = {"schema": 1, "produced_at": NOW, "accounts": {
        "codex-a": {"provider": "openai-codex", "state": "UNKNOWN", "windows": {
            "5h": {"remaining": 0.78, "reset_at": NOW + 600, "observed_at": NOW - 1, "state": "FRESH"}}},
        "go-a": {"provider": "opencode-go", "state": "FRESH", "windows": {}}}}
    accounts = er.status_from_payload(payload, NOW)["accounts"]
    assert accounts["codex-a"]["state"] == "UNKNOWN"
    assert accounts["go-a"]["state"] == "UNREADABLE"
