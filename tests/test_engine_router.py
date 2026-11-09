import json
from pathlib import Path

import pytest

from mishe_tauftauf import engine_router as router
from mishe_tauftauf import signal_store, yielding
from mishe_tauftauf.wake import EngineNotStarted, EngineTimeout

NOW_MS = 1_792_712_432_000
RESET = router._iso(NOW_MS + 3_600_000)


def account(state="FRESH", provider="opencode-go", remaining=0.8, reason=None):
    windows = [{"limit": "rolling-5h", "window": "5h", "remaining_fraction": remaining,
                "reset_at": RESET, "provider_status": None, "state": state, "reason": None}]
    return {"state": state, "provider": provider, "reason": reason, "windows": windows}


def status(**accounts):
    return {"state": "FRESH", "accounts": accounts}


GO = {"engine": "omp", "model": "opencode-go/beam-free", "account": "go-a"}
MINI = {"engine": "omp", "model": "minimax-code/MiniMax-M3.1-Flash", "account": "minimax-a"}
HAIKU = {"engine": "claude", "model": "claude-haiku-5-5", "account": None}


def routes_file(home, roles, cool_off_s=1800):
    d = Path(home) / "engine"
    d.mkdir(parents=True, exist_ok=True)
    (d / "routes.json").write_text(json.dumps({"version": 1, "cool_off_s": cool_off_s, "roles": roles}))


def test_first_fresh_admitted_candidate_is_selected():
    st = status(**{"go-a": account(), "minimax-a": account(provider="minimax-code")})
    c = router.choose("health", [GO, MINI, HAIKU], st, {}, NOW_MS)
    assert c["decision"] == "select" and c["key"] == router.candidate_key(GO)


def test_stale_or_unknown_account_is_skipped_for_next_candidate():
    st = status(**{"go-a": account(state="STALE", reason="provider reading older than report TTL"),
                   "minimax-a": account(provider="minimax-code")})
    c = router.choose("health", [GO, MINI, HAIKU], st, {}, NOW_MS)
    assert c["key"] == router.candidate_key(MINI)
    assert c["trail"][0]["verdict"] == "skipped"


def test_depleted_account_is_not_selected():
    st = status(**{"go-a": account(remaining=0.0)})
    c = router.choose("health", [GO, HAIKU], st, {}, NOW_MS)
    assert c["key"] == router.candidate_key(HAIKU)


def test_claude_safety_net_needs_no_account_status():
    c = router.choose("genome", [HAIKU], None, {}, NOW_MS)
    assert c["decision"] == "select"


def test_cooling_candidate_is_skipped_then_reprobed_after_cool_off():
    st = status(**{"go-a": account(), "minimax-a": account(provider="minimax-code")})
    cooling = {router.candidate_key(GO): NOW_MS + 60_000}
    assert router.choose("health", [GO, MINI], st, cooling, NOW_MS)["key"] == router.candidate_key(MINI)
    assert router.choose("health", [GO, MINI], st, cooling, NOW_MS + 61_000)["key"] == router.candidate_key(GO)


def test_no_working_candidate_is_none():
    st = status(**{"go-a": account(state="UNKNOWN", reason="usage read failed")})
    c = router.choose("health", [GO], st, {}, NOW_MS)
    assert c["decision"] == "none"


def test_assess_green_when_every_role_has_admitted_candidate():
    routes = {"roles": {"health": [GO, HAIKU]}, "cool_off_s": 1800}
    st = status(**{"go-a": account()})
    assert router.assess(routes, st, {}, NOW_MS)["state"] == "GREEN"


def test_assess_unknown_when_windows_stale_but_role_still_routed():
    routes = {"roles": {"health": [GO, HAIKU]}, "cool_off_s": 1800}
    st = status(**{"go-a": account(state="STALE")})
    result = router.assess(routes, st, {}, NOW_MS)
    assert result["state"] == "UNKNOWN" and "go-a" in result["reason"]


def test_assess_red_when_a_role_has_no_working_candidate():
    routes = {"roles": {"genome": [HAIKU], "health": [GO]}, "cool_off_s": 1800}
    cooling = {router.candidate_key(HAIKU): NOW_MS + 60_000}
    result = router.assess(routes, status(**{"go-a": account()}), cooling, NOW_MS)
    assert result["state"] == "RED" and "genome" in result["reason"]


def test_assess_unknown_without_status_or_routes():
    assert router.assess({"roles": {}, "cool_off_s": 1}, None, {}, NOW_MS)["state"] == "UNKNOWN"
    assert router.assess(None, status(), {}, NOW_MS)["state"] == "UNKNOWN"


def test_load_routes_rejects_bad_engine_and_missing_file(tmp_path):
    assert router.load_routes(tmp_path) is None
    routes_file(tmp_path, {"health": [{"engine": "codex", "model": "x", "account": None}]})
    with pytest.raises(ValueError):
        router.load_routes(tmp_path)


def test_for_role_is_none_for_unlisted_role(tmp_path):
    routes_file(tmp_path, {"health": [HAIKU]})
    assert router.for_role(tmp_path, "docs") is None
    assert isinstance(router.for_role(tmp_path, "health"), router.RoutedEngine)


class FakeEngine:
    """Records calls; behaviour per candidate model is scripted."""
    script = {}

    def __init__(self, model):
        self.model = model

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None):
        action = FakeEngine.script[self.model]
        if action == "not-started":
            raise EngineNotStarted("missing binary")
        if action == "timeout":
            raise EngineTimeout("exceeded")
        if action == "yield":
            path = yielding.receipt_path(env["MISHE_SEED_HOME"], env["MISHE_WAKE_KEY"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
        return 0


@pytest.fixture
def routed(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "live_status", lambda home: status(**{"go-a": account(), "minimax-a": account(provider="minimax-code")}))
    monkeypatch.setattr(router.time, "time", lambda: NOW_MS / 1000)
    monkeypatch.setattr(router.RoutedEngine, "_build", lambda self, cand: FakeEngine(cand["model"]))
    FakeEngine.script = {}
    routes_file(tmp_path, {"health": [GO, MINI, HAIKU]})
    return tmp_path


def _env(home, key):
    return {"MISHE_SEED_HOME": str(home), "MISHE_WAKE_KEY": key}


def test_yielded_run_clears_cooling_and_records_outcome(routed):
    FakeEngine.script = {GO["model"]: "yield"}
    eng = router.for_role(routed, "health")
    eng.run(routed / "p.md", env=_env(routed, "k1"), cwd=routed, timeout=5)
    ledger = [json.loads(x) for x in (routed / "engine" / router.LEDGER).read_text().splitlines()]
    assert [e["event"] for e in ledger] == ["select", "outcome"]
    assert ledger[-1]["outcome"] == "yielded"
    assert (routed / "engine" / router.STATE).exists() is False or not json.loads(
        (routed / "engine" / router.STATE).read_text())["cooling"]


def test_no_yield_cools_candidate_so_next_wake_switches(routed):
    FakeEngine.script = {GO["model"]: "exit-no-yield", MINI["model"]: "yield"}
    eng = router.for_role(routed, "health")
    eng.run(routed / "p.md", env=_env(routed, "k2"), cwd=routed, timeout=5)
    cooling = json.loads((routed / "engine" / router.STATE).read_text())["cooling"]
    assert router.candidate_key(GO) in cooling
    nxt = router.for_role(routed, "health")
    assert nxt.run(routed / "p.md", env=_env(routed, "k3"), cwd=routed, timeout=5) == 0
    selects = [json.loads(x) for x in (routed / "engine" / router.LEDGER).read_text().splitlines()
               if json.loads(x)["event"] == "select"]
    assert selects[-1]["key"] == router.candidate_key(MINI)


def test_not_started_falls_through_within_same_wake(routed):
    FakeEngine.script = {GO["model"]: "not-started", MINI["model"]: "yield"}
    eng = router.for_role(routed, "health")
    assert eng.run(routed / "p.md", env=_env(routed, "k4"), cwd=routed, timeout=5) == 0
    events = [json.loads(x)["event"] for x in (routed / "engine" / router.LEDGER).read_text().splitlines()]
    assert events == ["select", "fallback", "select", "outcome"]


def test_timeout_cools_and_reraises(routed):
    FakeEngine.script = {GO["model"]: "timeout"}
    eng = router.for_role(routed, "health")
    with pytest.raises(EngineTimeout):
        eng.run(routed / "p.md", env=_env(routed, "k5"), cwd=routed, timeout=5)
    assert router.candidate_key(GO) in json.loads((routed / "engine" / router.STATE).read_text())["cooling"]


def test_publish_observes_engine_routing_signal(routed):
    result = router.publish(routed, now=NOW_MS / 1000)
    assert result["state"] == "GREEN"
    assert router.SIGNAL in signal_store.registry(routed)


def test_live_status_refetches_only_stale_configured_accounts(tmp_path, monkeypatch):
    import subprocess
    from mishe_tauftauf import engine_usage
    stale = {"state": "STALE", "registry_state": "configured", "provider": "opencode-go", "store": "omp/agent"}
    fresh = {"state": "FRESH", "registry_state": "configured", "provider": "minimax-code", "store": "omp/agent"}
    readings = iter([{"accounts": {"go-a": stale, "minimax-a": fresh}}, {"accounts": {"go-a": fresh, "minimax-a": fresh}}])
    monkeypatch.setattr(engine_usage, "status", lambda home: next(readings))
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw["env"]["PI_CODING_AGENT_DIR"])))
    result = router.live_status(tmp_path)
    assert result["accounts"]["go-a"]["state"] == "FRESH"
    assert calls == [(["omp", "usage", "invalidate", "--provider", "opencode-go"], str(tmp_path / "omp/agent"))]
