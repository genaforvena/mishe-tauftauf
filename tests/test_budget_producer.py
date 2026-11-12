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
    # the malformed window is kept as evidence and the account fails closed (DM 300043)
    assert accounts["claude"]["state"] == "UNREADABLE" and accounts["claude"]["malformed"] == ["5h"]


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


def test_router_does_not_admit_an_account_with_a_malformed_window():
    from mishe_tauftauf import engine_router as er

    payload = {"schema": 1, "produced_at": NOW, "accounts": {"codex-a": {"provider": "openai-codex", "state": "FRESH", "windows": {
        "5h": {"remaining": 0.8, "reset_at": NOW + 600, "observed_at": NOW - 1, "state": "FRESH"},
        "30d": {"remaining": 0, "observed_at": NOW - 1, "state": "FRESH"}}}}}
    row = er.status_from_payload(payload, NOW)["accounts"]["codex-a"]
    assert row["state"] == "UNREADABLE"
    assert "30d" in row["reason"]


def test_producer_marks_account_with_malformed_window_unreadable_and_keeps_evidence():
    from mishe_tauftauf import budget_producer as bp

    now = 1000.0
    good = {"remaining": 0.5, "reset_at": 2000.0, "observed_at": 990.0, "state": "FRESH"}
    status = {"accounts": {"a": {"provider": "p", "state": "FRESH", "observed_at": 990.0,
                                 "windows": {"5h": good, "7d": {"remaining": None, "reset_at": 2000.0}}}}}
    acc = bp.build_accounts(status, None, now)["a"]
    assert acc["state"] == "UNREADABLE" and acc["malformed"] == ["7d"] and "5h" in acc["windows"]


def test_cooling_section_mirrors_router_state_and_drops_non_numeric_until(tmp_path):
    from mishe_tauftauf import budget_producer as bp
    from mishe_tauftauf import engine_router as er

    state_dir = er._dir(tmp_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / er.STATE).write_text(json.dumps({"cooling": {
        "genome|omp:x:-": NOW * 1000 + 60000,
        "docs|omp:y:-": NOW * 1000 - 60000,
        "bad|omp:z:-": "soon"}}), encoding="utf-8")
    cooling = bp.build_cooling(tmp_path, NOW)
    assert cooling == {"genome|omp:x:-": {"until": NOW * 1000 + 60000, "active": True},
                       "docs|omp:y:-": {"until": NOW * 1000 - 60000, "active": False}}


def _ev(event, role="genome", key="omp:m:acc", wake="w1", at="2026-11-12T06:00:00Z", **extra):
    row = {"event": event, "role": role, "key": key, "wake": wake, "at": at}
    row.update(extra)
    return row


def test_tally_counts_timeouts_and_leaves_open_selects_pending():
    from mishe_tauftauf import budget_producer as bp
    now = 1794464000.0  # 2026-11-12T06:00:00Z-ish; the ledger times below sit inside the 1h window
    events = [
        _ev("select", wake="a"), _ev("outcome", wake="a", outcome="yielded"),
        _ev("select", wake="b"), _ev("outcome", wake="b", outcome="timeout"),
        _ev("select", wake="c"),  # still running: pending, not a turn
    ]
    t = bp._ledger_tally(events, now)
    assert t["counts"][("genome", "omp:m:acc")]["1h"] == 2  # the timeout counts as a turn
    assert t["pending"] == {("genome", "omp:m:acc"): 1}
    assert t["last"][("genome", "omp:m:acc")][1]["outcome"] == "timeout"


def test_fallback_closes_a_select_without_counting_a_turn():
    from mishe_tauftauf import budget_producer as bp
    events = [_ev("select", wake="a"), _ev("fallback", wake="a")]
    t = bp._ledger_tally(events, 1794464000.0)
    assert t["pending"] == {}
    assert ("genome", "omp:m:acc") not in t["counts"]


def test_unreadable_outcome_timestamp_is_not_counted_and_is_reported():
    from mishe_tauftauf import budget_producer as bp
    events = [_ev("outcome", wake="a", outcome="yielded", at="not-a-time")]
    t = bp._ledger_tally(events, 1794464000.0)
    assert t["unreadable"] == 1
    assert t["counts"] == {}


def test_build_ledger_splits_free_and_paid_and_keeps_unrouted_out(tmp_path):
    import json
    engine = tmp_path / "engine"
    engine.mkdir()
    routes = {"version": 1, "cool_off_s": 600, "env": {}, "roles": {"genome": [
        {"engine": "omp", "model": "free/m", "free": True},
        {"engine": "omp", "model": "paid/m", "account": "acc-a"},
    ]}}
    (engine / "routes.json").write_text(json.dumps(routes))
    rows = [
        _ev("outcome", key="omp:free/m:-", wake="a", outcome="yielded", at="2026-11-12T06:00:00Z"),
        _ev("select", key="omp:paid/m:acc-a", wake="b", at="2026-11-12T06:00:00Z"),
        _ev("outcome", key="omp:longcat:-", wake="c", outcome="yielded", at="2026-11-12T06:00:00Z"),
    ]
    (engine / "routing-ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = bp.build_ledger(tmp_path, 1794464000.0)
    assert set(out["free_models"]) == {"omp:free/m:-"}
    assert out["free_models"]["omp:free/m:-"]["verdict"] == "FREE"
    assert out["free_models"]["omp:free/m:-"]["turns_1h"] == 1
    assert set(out["paid_models"]) == {"omp:paid/m:acc-a"}
    assert out["paid_models"]["omp:paid/m:acc-a"]["pending"] == 1
    assert out["paid_models"]["omp:paid/m:acc-a"]["account"] == "acc-a"
    assert "omp:longcat:-" not in out["free_models"] and "omp:longcat:-" not in out["paid_models"]
    assert any("not classified" in u and "omp:longcat:-" in u for u in out["ledger_unknowns"])
    assert out["per_mind"]["genome"]["omp:longcat:-"]["turns_1h"] == 1
    un = out["unclassified_models"]["omp:longcat:-"]
    assert un["in_routes"] is False and un["verdict"] == "UNKNOWN" and un["free"] is None
    assert un["turns_1h"] == 1 and un["last_outcome"]["outcome"] == "yielded"


def test_unclassified_keeps_last_outcome_older_than_24h(tmp_path):
    import json
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "routes.json").write_text(json.dumps({"version": 1, "cool_off_s": 600, "env": {}, "roles": {}}))
    rows = [_ev("outcome", key="omp:old:-", wake="z", outcome="timeout", at="2026-11-01T06:00:00Z")]
    (engine / "routing-ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    from mishe_tauftauf import budget_producer as bp
    out = bp.build_ledger(tmp_path, 1794464000.0)
    un = out["unclassified_models"]["omp:old:-"]
    assert un["turns_24h"] == 0 and un["last_outcome"]["outcome"] == "timeout"


def test_malformed_ledger_rows_are_counted_and_reported(tmp_path):
    import json
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "routes.json").write_text(json.dumps({"version": 1, "cool_off_s": 600, "env": {}, "roles": {}}))
    good = json.dumps(_ev("outcome", key="omp:x:-", wake="a", outcome="yielded", at="2026-11-12T06:00:00Z"))
    (engine / "routing-ledger.jsonl").write_text(good + "\n{truncated\n[1, 2]\n" + good + "\n")
    from mishe_tauftauf import budget_producer as bp
    out = bp.build_ledger(tmp_path, 1794464000.0)
    assert out["ledger"]["malformed_skipped"] == 2
    assert out["ledger"]["events"] == 2
    assert out["unclassified_models"]["omp:x:-"]["turns_1h"] == 2
    assert any("2 ledger row(s) not a JSON object skipped" in u for u in out["ledger_unknowns"])


def test_ledger_meta_says_idle_or_missing_not_fresh(tmp_path):
    from mishe_tauftauf import budget_producer as bp
    out = bp.build_ledger(tmp_path, 1794464000.0)
    assert out["ledger"] == {"present": False, "events": 0, "malformed_skipped": 0,
                             "newest_event_at": None, "produced_at": 1794464000.0}


def test_build_ledger_without_ledger_file_is_unknown_not_zero(tmp_path):
    import json
    engine = tmp_path / "engine"
    engine.mkdir()
    routes = {"version": 1, "cool_off_s": 600, "env": {}, "roles": {"genome": [
        {"engine": "omp", "model": "free/m", "free": True}]}}
    (engine / "routes.json").write_text(json.dumps(routes))
    from mishe_tauftauf import budget_producer as bp
    out = bp.build_ledger(tmp_path, 1794464000.0)
    row = out["free_models"]["omp:free/m:-"]
    assert row["turns_1h"] is None and row["pending"] is None and row["observed_at"] is None
    assert any("ledger missing" in u for u in out["ledger_unknowns"])


def test_identityless_outcome_is_counted_as_malformed_not_dropped():
    from mishe_tauftauf import budget_producer as bp
    events = [{"event": "outcome", "wake": "a", "at": "2026-11-12T06:00:00Z", "outcome": "yielded"},
              _ev("outcome", wake="b", outcome="yielded")]
    t = bp._ledger_tally(events, 1794464000.0)
    assert t["malformed"] == 1
    assert t["counts"][("genome", "omp:m:acc")]["1h"] == 1


def test_invalid_utf8_ledger_line_is_skipped_not_replaced(tmp_path):
    import json
    from mishe_tauftauf.router_text import ledger_events
    path = tmp_path / "routing-ledger.jsonl"
    good = json.dumps(_ev("outcome", outcome="yielded")).encode()
    bad = b'{"event":"outcome","role":"genome","key":"bad\xff\xfekey","at":"2026-11-12T06:00:00Z"}'
    path.write_bytes(good + b"\n" + bad + b"\n")
    skipped: list = []
    events = list(ledger_events(path, skipped))
    assert len(events) == 1 and events[0]["key"] == "omp:m:acc"
    assert skipped == [2]


def _hist(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_recent_burn_is_used_delta_per_hour_over_the_observed_span():
    reset = NOW + 3600
    pts = [(NOW - 3000, 0.10, reset), (NOW, 0.40, reset)]
    assert bp.recent_burn(pts, reset) == pytest.approx(0.30 / (3000 / 3600))


def test_recent_burn_needs_span_and_same_window_and_never_goes_negative():
    reset = NOW + 3600
    assert bp.recent_burn([(NOW - 600, 0.1, reset), (NOW, 0.2, reset)], reset) is None  # span too short
    assert bp.recent_burn([(NOW - 3000, 0.1, reset - 7200), (NOW, 0.2, reset)], reset) is None  # other window
    assert bp.recent_burn([(NOW - 3000, 0.5, reset), (NOW, 0.2, reset)], reset) == 0.0  # reset inside span


def test_stale_window_gets_no_burn_or_eta_even_with_history(tmp_path: Path):
    budget = tmp_path / "operator" / "budget"
    old = NOW - 3000
    _hist(budget / bp.CLAUDE_HISTORY, [
        {"at": old, "five_hour": [10, NOW + 3600], "seven_day": [50, NOW + 3600]},
        {"at": NOW, "five_hour": [40, NOW + 3600], "seven_day": [50, NOW + 3600]},
    ])
    stale_tap = tap(five_hour={"observed_at": NOW - 5000, "resets_at": NOW + 3600, "used_percentage": 40})
    accounts = bp.build_accounts(None, stale_tap, NOW, bp.load_burn_history(tmp_path, NOW))
    five = accounts["claude"]["windows"]["5h"]
    assert five["state"] == "STALE" and five["burn_h"] is None and five["eta_s"] is None


def test_fresh_claude_window_gets_burn_and_eta_from_history(tmp_path: Path):
    budget = tmp_path / "operator" / "budget"
    _hist(budget / bp.CLAUDE_HISTORY, [
        {"at": NOW - 3000, "five_hour": [10, NOW + 3600], "seven_day": [50, NOW + 3600]},
        {"at": NOW, "five_hour": [40, NOW + 3600], "seven_day": [50, NOW + 3600]},
    ])
    accounts = bp.build_accounts(None, tap(), NOW, bp.load_burn_history(tmp_path, NOW))
    five = accounts["claude"]["windows"]["5h"]
    burn = 0.30 / (3000 / 3600)
    assert five["burn_h"] == pytest.approx(burn)
    assert five["eta_s"] == pytest.approx((1 - 0.02) / burn * 3600)


def test_engine_window_burn_uses_remaining_inverted_and_undecodable_lines_are_skipped(tmp_path: Path):
    budget = tmp_path / "operator" / "budget"
    budget.mkdir(parents=True)
    (budget / bp.ENGINE_HISTORY).write_bytes(
        b'{"at": %f, "q": {"go-a/5h": [0.9, %f]}}\n' % (NOW - 3000, NOW + 3600)
        + b"\xff\xfe not json\n"
        + b'{"at": %f, "q": {"go-a/5h": [0.6, %f]}}\n' % (NOW, NOW + 3600)
    )
    accounts = bp.build_accounts(status(**{"5h": {"remaining": 0.6, "reset_at": NOW + 3600, "observed_at": NOW, "state": "FRESH"}}),
                                 None, NOW, bp.load_burn_history(tmp_path, NOW))
    row = accounts["go-a"]["windows"]["5h"]
    assert row["burn_h"] == pytest.approx(0.30 / (3000 / 3600))


def test_without_history_burn_and_eta_are_none_not_zero():
    accounts = bp.build_accounts(None, tap(), NOW)
    five = accounts["claude"]["windows"]["5h"]
    assert five["state"] == "FRESH" and five["burn_h"] is None and five["eta_s"] is None
