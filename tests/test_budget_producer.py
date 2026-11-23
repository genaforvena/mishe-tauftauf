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


def test_a_probe_that_stamps_its_reading_ahead_does_not_flap_codex_a_to_stale(tmp_path: Path, capsys, monkeypatch):
    # 2026-11-16 incident: the codex probe ran ~8 s and the CLI's rollout event landed 6.6 s after the
    # producer's pre-probe `now`; account_row was re-checked by engine_windows against that stale `now`,
    # so a just-collected reading read as future-dated and codex-a flapped to STALE for the tick.
    from mishe_tauftauf import codex_usage

    clock = {"t": NOW}
    monkeypatch.setattr(bp.time, "time", lambda: clock["t"])
    seen = {}

    def fake_refresh(now, **kwargs):
        clock["t"] = NOW + 8.0  # the probe took ~8 s
        return True

    def fake_account_row(now, **kwargs):
        seen["now"] = now
        obs = NOW + 6.6  # the CLI stamps its own rollout after the pre-probe clock
        return {"provider": "openai-codex", "state": "FRESH", "observed_at": obs, "windows": {
            "5h": {"remaining": 0.58, "reset_at": NOW + 6000, "observed_at": obs, "state": "FRESH"}}}

    monkeypatch.setattr(codex_usage, "refresh", fake_refresh)
    monkeypatch.setattr(codex_usage, "account_row", fake_account_row)
    assert bp.main(tmp_path) == 0
    out = json.loads(capsys.readouterr().out)
    assert seen["now"] == NOW + 8.0
    assert out["accounts"]["codex-a"]["state"] == "FRESH"
    assert out["accounts"]["codex-a"]["windows"]["5h"]["state"] == "FRESH"
    assert out["produced_at"] == NOW + 8.0


def test_routing_without_routes_file_is_unknown_not_green(tmp_path: Path):
    payload = bp.build_budget(None, tap(), NOW)
    routing = bp.build_routing(tmp_path, payload, NOW)
    assert routing["state"] == "UNKNOWN"
    assert "routes.json" in routing["reason"]


def test_routing_view_names_the_same_ranked_candidate_as_the_signal(tmp_path: Path):
    from mishe_tauftauf import engine_router as er

    engine = tmp_path / "engine"
    engine.mkdir()
    first = {"engine": "omp", "model": "roster-first", "account": None, "free": True, "tier": 0}
    second = {"engine": "omp", "model": "rate-winner", "account": None, "free": True, "tier": 0}
    (engine / er.ROUTES).write_text(json.dumps({"version": 1, "roles": {"health": [first, second]}}))
    at = er._iso(NOW * 1000 - 3600_000)
    rows = ([{"at": at, "event": "outcome", "key": er.candidate_key(first), "outcome": "no yield (exit 1)"}] * 5
            + [{"at": at, "event": "outcome", "key": er.candidate_key(second), "outcome": "yielded"}] * 5)
    (engine / er.LEDGER).write_text("".join(json.dumps(row) + "\n" for row in rows))
    payload = bp.build_budget(None, tap(), NOW)
    view = bp.build_routing(tmp_path, payload, NOW)
    tally = er.measured_tally(tmp_path, now_ms=NOW * 1000)
    signal = er.assess(er.load_routes(tmp_path), er.status_from_payload(payload, NOW), {}, NOW * 1000, tally=tally)
    assert signal["roles"]["health"]["selected"] == er.candidate_key(second)
    assert view["roles"]["health"]["selected"] == signal["roles"]["health"]["selected"]


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


def test_without_history_burn_falls_back_to_elapsed_window_and_is_tagged():
    # No rate history: the window-elapsed estimate stands in, tagged so it is never read as a measured burn.
    accounts = bp.build_accounts(None, tap(), NOW)
    five = accounts["claude"]["windows"]["5h"]
    assert five["state"] == "FRESH" and five["burn_source"] == "window-elapsed"
    assert five["burn_h"] is not None and five["burn_h"] > 0 and five["eta_s"] is not None
    stale = dict(tap(), five_hour=dict(tap()["five_hour"], observed_at=NOW - 5000))
    assert bp.build_accounts(None, stale, NOW)["claude"]["windows"]["5h"]["burn_h"] is None


def test_elapsed_window_burn_matches_legacy_when_history_is_missing():
    # DM 300211 case: fresh 5h window at 60% used, reset in 2h, no history -> 0.2/h and ETA 2h, tagged as estimate.
    reset = NOW + 2 * 3600
    assert bp.elapsed_window_burn(0.6, reset, "5h", NOW) == pytest.approx(0.2)
    w = bp.build_accounts(None, {"state": "FRESH", "source": "claude-native-usage",
                                 "five_hour": {"observed_at": NOW - 10, "resets_at": reset, "used_percentage": 60}},
                          NOW, None)["claude"]["windows"]["5h"]
    assert w["burn_h"] == pytest.approx(0.2)
    assert w["burn_source"] == "window-elapsed"
    assert w["eta_s"] == pytest.approx(2 * 3600)


def test_elapsed_window_burn_is_not_used_when_history_or_window_length_is_missing():
    reset = NOW + 2 * 3600
    assert bp.elapsed_window_burn(0.0, reset, "5h", NOW) is None          # nothing used yet
    assert bp.elapsed_window_burn(0.6, reset, "9h", NOW) is None          # unknown window length
    assert bp.elapsed_window_burn(0.6, NOW + 4.9 * 3600, "5h", NOW) is None  # under 0.25 h elapsed
    assert bp.elapsed_window_burn(0.6, None, "5h", NOW) is None


def test_retired_routerai_candidates_are_listed_retired_not_unclassified(tmp_path):
    # Ilya DM 300461: the two RouterAI candidates were removed from every role; the budget
    # must list them as retired, never as unclassified (UNKNOWN).
    import json
    from pathlib import Path
    from mishe_tauftauf import budget_producer as bp

    home = tmp_path / "site"
    (home / "engine").mkdir(parents=True)
    (home / "engine" / "routes.json").write_text(json.dumps({
        "version": 1, "roles": {"genome": [{"engine": "claude", "model": "claude-haiku-5-5", "account": None}]},
        "retired": [{"engine": "omp", "model": "routerai/deepseek/deepseek-v4.1-flash", "account": None}],
    }))
    ledger = bp.build_ledger(home, 1794521756.0)
    assert "omp:routerai/deepseek/deepseek-v4.1-flash:-" in ledger["retired_models"]
    assert ledger["retired_models"]["omp:routerai/deepseek/deepseek-v4.1-flash:-"]["verdict"] == "RETIRED"
    assert ledger["unclassified_models"] == {}


def test_routerai_lines_read_balance_and_count_turns(tmp_path):
    import json as _json
    import datetime as _dt
    now = _dt.datetime(2026, 11, 14, 12, 0, tzinfo=_dt.timezone.utc).timestamp()
    engine = tmp_path / "engine"
    engine.mkdir()
    rows = [
        {"at": "2026-11-14T10:00:00Z", "event": "outcome", "key": "omp:routerai/deepseek/deepseek-v4.1-flash:-", "outcome": "yielded"},
        {"at": "2026-11-13T09:00:00Z", "event": "outcome", "key": "omp:routerai/deepseek/deepseek-v4.1-flash:-", "outcome": "yielded"},
        {"at": "2026-11-10T09:00:00Z", "event": "outcome", "key": "omp:routerai/deepseek/deepseek-v4.1-flash:-", "outcome": "yielded"},
        {"at": "2026-11-14T10:00:00Z", "event": "outcome", "key": "claude:claude-haiku-5-5:-", "outcome": "yielded"},
    ]
    (engine / "routing-ledger.jsonl").write_text("\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ok = bp.build_routerai(tmp_path, now, fetch=lambda: 325.5)
    assert ok["llm"]["state"] == "FRESH" and ok["llm"]["balance"] == 325.5
    # only the 2026-11-14 10:00Z deepseek row is within 24 h of 12:00Z; the 11-13 09:00Z row is 27 h old
    assert ok["llm"]["turns_24h"] == 1
    assert ok["decision"]["state"] == "UNKNOWN"
    assert ok["decision"]["reason"] == "no decision_provider config at engine/decision-providers.json"
    assert ok["decision"]["kind"].startswith("System One decision models") and ok["decision"]["daily_cap"] is None
    # LLM spend limit from operator DM 301051; spend is not readable, so it is UNKNOWN, never zero
    assert ok["llm"]["spend_limit"] == 200
    assert ok["llm"]["spent"] is None and ok["llm"]["spend_state"] == "UNKNOWN"
    assert "rubles" in ok["llm"]["spend_limit_unit"]


def test_routerai_balance_failure_is_unknown_not_zero(tmp_path):
    def boom():
        raise OSError("unreachable")
    out = bp.build_routerai(tmp_path, 1794700000.0, fetch=boom)
    for line in out.values():
        assert line["state"] == "UNKNOWN"
        assert line["balance"] is None
    assert out["llm"]["reason"] == "balance unreadable: OSError"


def _decision_home(tmp_path, cap=10, ledger=None, config=None):
    import datetime as _dt
    import json as _json
    engine = tmp_path / "engine"
    engine.mkdir(exist_ok=True)
    budget = tmp_path / "decision-budget.json"
    if ledger is not None:
        budget.write_text(_json.dumps(ledger), encoding="utf-8")
        budget.chmod(0o600)
    if config is None:
        key = tmp_path / "routerai.key"
        key.write_text("ROUTERAI_API_KEY=unused\n", encoding="utf-8")
        key.chmod(0o600)
        config = {"providers": {"routerai": {
            "protocol": "systemone", "base_url": "https://routerai.ru", "key_file": str(key),
            "model": "typesafe/jev-1.14", "daily_cap": cap, "spend_limit": 50,
            "price_per_token": 4.48547736e-06, "timeout": 30, "budget_file": str(budget)}}}
    (engine / "decision-providers.json").write_text(_json.dumps(config), encoding="utf-8")
    now = _dt.datetime(2026, 11, 14, 12, 0, tzinfo=_dt.timezone.utc).timestamp()
    return now


def test_decision_line_reports_cap_and_today_calls(tmp_path):
    now = _decision_home(tmp_path, cap=10, ledger={"date": "2026-11-14", "count": 3, "spent": 0.25})
    line = bp.decision_line(tmp_path, now, {"balance": 1.0})
    assert line["state"] == "FRESH" and line["reason"] is None
    assert line["daily_cap"] == 10 and line["calls_today"] == 3 and line["calls_remaining"] == 7
    assert line["spend_limit"] == 50 and line["spent"] == 0.25 and line["spend_remaining"] == 49.75
    assert line["model"] == "typesafe/jev-1.14" and abs(line["price_per_1m_input"] - 4.48547736) < 1e-9


def test_decision_line_yesterday_ledger_counts_zero_but_keeps_total(tmp_path):
    now = _decision_home(tmp_path, cap=10, ledger={"date": "2026-11-13", "count": 9, "spent": 12.5})
    line = bp.decision_line(tmp_path, now, {"balance": 1.0})
    assert line["state"] == "FRESH" and line["calls_today"] == 0 and line["calls_remaining"] == 10
    assert line["spent"] == 12.5 and line["spend_remaining"] == 37.5


def test_decision_line_corrupt_ledger_is_unknown_not_zero(tmp_path):
    now = _decision_home(tmp_path, cap=10, ledger={"date": "2026-11-14", "count": -1, "spent": 0})
    line = bp.decision_line(tmp_path, now, {"balance": 1.0})
    assert line["state"] == "UNKNOWN" and line["calls_today"] is None
    assert line["reason"].startswith("decision ledger unreadable")


def test_decision_line_bad_config_is_unknown_not_default(tmp_path):
    now = _decision_home(tmp_path, config={"providers": {"routerai": {"protocol": "other"}}})
    line = bp.decision_line(tmp_path, now, {"balance": 1.0})
    assert line["state"] == "UNKNOWN" and line["daily_cap"] is None
    assert line["reason"].startswith("decision config unreadable")


def test_non_fresh_tap_publishes_no_window_but_keeps_its_cause():
    # The fail-closed rule stays: a non-FRESH native sample publishes no claude window. What it must
    # also publish is why (2026-11-17: an idle five_hour window made the account UNREADABLE with no
    # cause, so every consumer could only repeat "account state UNREADABLE").
    accounts = bp.build_accounts(None, tap(state="UNKNOWN", error="five_hour reset not yet reported; sample not trusted"), NOW)
    assert accounts["claude"]["state"] == "UNREADABLE"
    assert accounts["claude"]["windows"] == {}
    assert accounts["claude"]["reason"] == "five_hour reset not yet reported; sample not trusted"


def _routing_site(tmp_path):
    from mishe_tauftauf import engine_router as er

    engine = tmp_path / "engine"
    engine.mkdir()
    cand = {"engine": "omp", "model": "free-first", "account": None, "free": True, "tier": 0}
    (engine / er.ROUTES).write_text(json.dumps({"version": 1, "roles": {"health": [cand]}}))


def test_producer_write_reobserves_routing_signal(tmp_path):
    from mishe_tauftauf import engine_router as er, signal_store

    _routing_site(tmp_path)
    out = tmp_path / "budget.json"
    assert bp.main(tmp_path, out=out) == 0
    row = signal_store.recorded(tmp_path, er.SIGNAL)
    assert row is not None
    produced = json.loads(out.read_text())
    assert row["value"] == produced["routing"]["state"]
    assert row["observed_at"] >= produced["produced_at"]


def test_dry_run_does_not_observe_routing_signal(tmp_path, capsys):
    from mishe_tauftauf import engine_router as er, signal_store

    _routing_site(tmp_path)
    assert bp.main(tmp_path) == 0
    capsys.readouterr()
    assert signal_store.recorded(tmp_path, er.SIGNAL) is None


def test_producer_stamps_at_write_clock_not_preprobe_now(tmp_path, monkeypatch):
    # A wake outcome observed later must not be overwritten by the producer's older captured `now`.
    from mishe_tauftauf import engine_router as er, signal_store

    er.observe(tmp_path, {"state": "RED", "reason": "no candidate"}, now=NOW + 100)
    assert signal_store.recorded(tmp_path, er.SIGNAL)["value"] == "RED"
    _routing_site(tmp_path)
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        return NOW if calls["n"] == 1 else NOW + 200

    monkeypatch.setattr(bp.time, "time", clock)
    assert bp.main(tmp_path, out=tmp_path / "budget.json") == 0
    row = signal_store.recorded(tmp_path, er.SIGNAL)
    assert row["observed_at"] == NOW + 200


def test_free_row_freshness_is_the_producer_read_not_a_quiet_ledger(tmp_path):
    # Replay: 3900 s after the last routing event, the free row inherited that event as observed_at,
    # aged past the 3600 s budget window and held an open-slot free route. A free route has no window to
    # age, so its row is observed when the producer read the roster.
    import json
    from mishe_tauftauf.engine_router import _iso
    engine = tmp_path / "engine"
    engine.mkdir()
    routes = {"version": 1, "cool_off_s": 600, "env": {}, "roles": {"idle": [
        {"engine": "omp", "model": "free/m", "free": True}]}}
    (engine / "routes.json").write_text(json.dumps(routes))
    now = 1794464000.0
    rows = [_ev("outcome", key="omp:free/m:-", wake="a", outcome="yielded", at=_iso((now - 3900) * 1000))]
    (engine / "routing-ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = bp.build_ledger(tmp_path, now)
    row = out["free_models"]["omp:free/m:-"]
    assert row["observed_at"] == now
    assert row["turns_24h"] == 1


def test_producer_run_with_unknown_routing_beats_engine_routing_cadence(tmp_path: Path, monkeypatch):
    # 2026-11-22 incident: minimax-a's stale window made routing UNKNOWN; observe_unknown leaves .seen
    # untouched, so engine-routing aged to a "producer missed cadence" RED although the producer ran on
    # time. The run must leave its cadence beat for engine-routing, whatever the routing value is.
    from mishe_tauftauf import codex_usage
    monkeypatch.setattr(codex_usage, "refresh", lambda now, **kwargs: False)
    out = tmp_path / "engine" / "budget.json"
    out.parent.mkdir()
    assert bp.main(tmp_path, out) == 0
    assert json.loads(out.read_text())["routing"]["state"] == "UNKNOWN"
    assert (tmp_path / "signals" / "engine-routing.ran").exists()


def test_codex_fresh_reading_is_recorded_and_flat_use_measures_zero_burn(tmp_path):
    # A FRESH codex-a reading is written to quota-history; two flat readings 6 h apart give a measured
    # burn of 0 (no runs-out ETA), not the window-elapsed projection that drove a false RED.
    reset = NOW + 4 * 86400
    def reading(observed):
        return {"state": "FRESH", "windows": {"7d": {"remaining": 0.19, "reset_at": reset, "state": "FRESH", "observed_at": observed}}}
    bp.append_codex_history(tmp_path, NOW - 6 * 3600, reading(NOW - 6 * 3600))
    bp.append_codex_history(tmp_path, NOW, reading(NOW))
    rows = [json.loads(line) for line in (tmp_path / "operator" / "budget" / "quota-history.jsonl").read_text().splitlines()]
    assert [r["q"] for r in rows] == [{"codex-a/7d": [0.19, reset]}] * 2
    points = bp.load_burn_history(tmp_path, NOW)[("codex-a", "7d")]
    assert bp.recent_burn(points, reset) == 0.0


def test_codex_stale_reading_writes_nothing(tmp_path):
    codex = {"state": "STALE", "windows": {"7d": {"remaining": 0.19, "reset_at": NOW + 1, "state": "STALE"}}}
    bp.append_codex_history(tmp_path, NOW, codex)
    assert not (tmp_path / "operator" / "budget" / "quota-history.jsonl").exists()


def test_codex_row_is_stamped_with_observation_time_and_unchanged_reading_is_not_rerecorded(tmp_path):
    # The row carries the engine's observed_at, not the producer clock; a re-written FRESH reading whose
    # observation has not advanced adds no row (it would otherwise repeat every tick).
    reset = NOW + 4 * 86400
    path = tmp_path / "operator" / "budget" / "quota-history.jsonl"
    old_obs = NOW - 900
    codex = {"state": "FRESH", "windows": {"7d": {"remaining": 0.19, "reset_at": reset, "state": "FRESH", "observed_at": old_obs}}}
    bp.append_codex_history(tmp_path, NOW, codex)
    bp.append_codex_history(tmp_path, NOW + 600, codex)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["at"] for r in rows] == [old_obs]
    newer = {"state": "FRESH", "windows": {"7d": {"remaining": 0.18, "reset_at": reset, "state": "FRESH", "observed_at": NOW + 300}}}
    bp.append_codex_history(tmp_path, NOW + 600, newer)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["at"] for r in rows] == [old_obs, NOW + 300]
