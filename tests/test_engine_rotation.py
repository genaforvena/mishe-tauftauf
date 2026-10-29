"""Engine rotation: controlled generation-scoped rotation."""
import json
import time
from pathlib import Path

import pytest

from mishe_tauftauf import cli, engine_policy as policy
from mishe_tauftauf import model_boundary


def make_home(tmp_path, name="site"):
    home = tmp_path / name
    cli.initialize(home)
    # minimal minds dir already made, but ensure charters
    for role in ("code", "witness"):
        (home / "charters").mkdir(exist_ok=True)
        (home / "charters" / (role + ".md")).write_text(role)
    return home


def write_launcher(home, role, model, store=None, effort="high"):
    p = home / "minds" / role
    env = f"export PI_CODING_AGENT_DIR={store}\n" if store else ""
    text = f"#!/bin/sh\n{env}exec /usr/bin/omp --model={model} --thinking {effort} --cwd /tmp\n"
    p.write_text(text)
    p.chmod(0o755)
    return p


def write_registry(home, accounts):
    # accounts is dict alias -> {provider, state, credential_id, store?}
    (home / "engine").mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "store": "omp-recovery/agent", "accounts": accounts}
    (home / "engine" / "accounts.json").write_text(json.dumps(data, sort_keys=True, indent=2))
    # ensure store dirs exist (no DB needed for rotation)
    for alias, row in accounts.items():
        store = row.get("store", "omp-recovery/agent")
        (home / store).mkdir(parents=True, exist_ok=True)


def stub_idle(monkeypatch, value=True):
    monkeypatch.setattr(model_boundary, "idle", lambda *a, **kw: value)


def stub_preflight(monkeypatch, thinking=None):
    if thinking is None:
        thinking = ["low", "medium", "high"]
    monkeypatch.setattr(model_boundary, "preflight", lambda cfg, target: {"thinking": thinking})


def route(alias, provider, model, effort="high"):
    return {"provider": provider, "alias": alias, "model": model, "effort": effort, "runner": "omp"}


# -- helpers for policy fallback/ reset ----------------------------

NOW = 1_792_712_432_436
RESET_FUTURE = NOW + 3_600_000


def iso(ms):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(remaining=1.0, reset_ms=RESET_FUTURE, limit="rolling-5h", window_id="5h", provider_status=None):
    return {
        "limit": limit,
        "window": window_id,
        "label": limit,
        "remaining_fraction": remaining,
        "reset_at": iso(reset_ms),
        "duration_ms": 3600_000,
        "provider_status": provider_status,
        "state": "FRESH",
        "reason": None,
    }


def account(alias, provider, state="FRESH", windows=None):
    if windows is None:
        windows = [window()]
    return {"alias": alias, "provider": provider, "state": state, "windows": windows, "fetched_at": iso(NOW), "age_seconds": 1}


def status_doc(d):
    return {"state": "FRESH", "observed_at": iso(NOW), "accounts": d}


# -------------------------------------------------------------------
def test_go_a_to_go_b_same_model_succeeds_and_observable(tmp_path, monkeypatch):
    from mishe_tauftauf import engine_rotation as rot

    home = make_home(tmp_path)
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "go-b": {"provider": "opencode-go", "state": "configured", "credential_id": 9, "store": "engine/go-b-store"},
            "minimax-a": {"provider": "minimax-code", "state": "configured", "credential_id": 12},
        },
    )
    model = "opencode-go/muse-spark-1.2-contributor"
    write_launcher(home, "code", model, store="omp-recovery/agent")
    # seed no session -> idle path treated as idle
    stub_idle(monkeypatch, True)
    stub_preflight(monkeypatch)
    # stub seed recorded_session to None so idle check is skipped
    import mishe_tauftauf.seed as seed

    monkeypatch.setattr(seed, "recorded_session", lambda h: None)

    r = route("go-a", "opencode-go", model)
    prep = rot.prepare(home, "code", r, generation=1, now_ms=NOW)
    assert prep["receipt_id"] == "00000001-code"

    res = rot.apply(home, "code", route("go-b", "opencode-go", model), generation=1, now_ms=NOW)
    assert res["generation"] == 1
    assert res["state"] == "ok"
    # launcher now points at go-b store
    text = (home / "minds/code").read_text()
    assert "engine/go-b-store" in text
    assert model in text
    # observable as new generation
    obs = rot.observe(home, "code", generation=1, target=model)
    assert obs["state"] == "verified"
    assert obs["actual_model"] == model
    assert obs["actual_account_hint"] == "go-b"


def test_provider_fallback_via_policy_chain(tmp_path, monkeypatch):
    # Unit: policy prefers cross-provider healthy when same-provider depleted
    allow = {"code": [route("go-a", "opencode-go", "opencode-go/muse-spark-1.2-contributor"), route("minimax-a", "minimax-code", "minimax-code/MiniMax-M3.1")]}
    depleted = account("go-a", "opencode-go", windows=[window(remaining=0.0, reset_ms=RESET_FUTURE)])
    healthy = account("minimax-a", "minimax-code", windows=[window(remaining=0.9, reset_ms=RESET_FUTURE)])
    st = status_doc({"go-a": depleted, "minimax-a": healthy})
    decision = policy.decide(role="code", status=st, allowlist=allow, current_route=route("go-a", "opencode-go", "opencode-go/muse-spark-1.2-contributor"), now_ms=NOW)
    assert decision["decision"] == "select"
    assert decision["selected"]["alias"] == "minimax-a"

    # Rotation applies the fallback model
    from mishe_tauftauf import engine_rotation as rot

    home = make_home(tmp_path, name="site2")
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "minimax-a": {"provider": "minimax-code", "state": "configured", "credential_id": 12},
        },
    )
    write_launcher(home, "code", "opencode-go/muse-spark-1.2-contributor")
    import mishe_tauftauf.seed as seed

    monkeypatch.setattr(seed, "recorded_session", lambda h: None)
    stub_idle(monkeypatch, True)
    stub_preflight(monkeypatch)
    res = rot.apply(home, "code", route("minimax-a", "minimax-code", "minimax-code/MiniMax-M3.1"), generation=1, now_ms=NOW)
    assert res["state"] == "ok"
    assert "minimax-code/MiniMax-M3.1" in (home / "minds/code").read_text()


def test_reset_return_expired_becomes_healthy(tmp_path):
    # depleted window whose reset is in the past is ignored -> healthy
    allow = {"code": [route("go-a", "opencode-go", "opencode-go/muse-spark-1.2-contributor"), route("go-b", "opencode-go", "opencode-go/muse-spark-1.2-contributor")]}
    expired_reset = NOW - 5000
    # go-a depleted but reset already passed
    depleted_expired = account("go-a", "opencode-go", windows=[window(remaining=0.0, reset_ms=expired_reset)])
    go_b_healthy = account("go-b", "opencode-go", windows=[window(remaining=0.9, reset_ms=RESET_FUTURE)])
    st = status_doc({"go-a": depleted_expired, "go-b": go_b_healthy})
    # current is go-b (fallback). After reset, go-a should be eligible again and stay keeps go-b, but a fresh decide with no current or depleted current should re-select primary.
    # Simulate: now policy re-evaluates with go-a as current (was depleted but now healthy after reset). It should stay on go-a.
    decision = policy.decide(
        role="code",
        status=st,
        allowlist=allow,
        current_route=route("go-a", "opencode-go", "opencode-go/muse-spark-1.2-contributor"),
        now_ms=NOW,
    )
    assert decision["decision"] == "stay"
    assert decision["selected"]["alias"] == "go-a"

    # With a different status where go-a window still validly depleted, queue/select would pick go-b
    still_depleted = account("go-a", "opencode-go", windows=[window(remaining=0.0, reset_ms=RESET_FUTURE)])
    st2 = status_doc({"go-a": still_depleted, "go-b": go_b_healthy})
    d2 = policy.decide(role="code", status=st2, allowlist=allow, current_route=route("go-a", "opencode-go", "opencode-go/muse-spark-1.2-contributor"), now_ms=NOW)
    assert d2["selected"]["alias"] == "go-b"


def test_repeated_cycles_new_generation(tmp_path, monkeypatch):
    from mishe_tauftauf import engine_rotation as rot
    import mishe_tauftauf.seed as seed

    home = make_home(tmp_path, name="site3")
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "go-b": {"provider": "opencode-go", "state": "configured", "credential_id": 9, "store": "engine/go-b-store"},
        },
    )
    model = "opencode-go/muse-spark-1.2-contributor"
    write_launcher(home, "code", model, store="omp-recovery/agent")
    monkeypatch.setattr(seed, "recorded_session", lambda h: None)
    stub_idle(monkeypatch, True)
    stub_preflight(monkeypatch)

    receipts = []
    for i, alias in enumerate(["go-b", "go-a", "go-b", "go-a"], start=1):
        res = rot.apply(home, "code", route(alias, "opencode-go", model), generation=i, now_ms=NOW + i * 1000)
        assert res["state"] == "ok"
        assert res["generation"] == i
        receipts.append(res["receipt_id"])
        obs = rot.observe(home, "code", generation=i, target=model)
        assert obs["state"] == "verified"

    # receipt ids are distinct
    assert len(set(receipts)) == 4
    # old generation is superseded but not re-interpreted as success for old route?
    # Generation 1's stored route was go-b; current launcher is go-a, so observe gen1 should be activated (not verified for go-b store)
    old_obs = rot.observe(home, "code", generation=1, target=model)
    assert old_obs["state"] == "activated"


def test_concurrent_cas_fails_generation_mismatch(tmp_path, monkeypatch):
    from mishe_tauftauf import engine_rotation as rot
    import mishe_tauftauf.seed as seed

    home = make_home(tmp_path, name="site4")
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "go-b": {"provider": "opencode-go", "state": "configured", "credential_id": 9, "store": "engine/go-b-store"},
        },
    )
    model = "opencode-go/muse-spark-1.2-contributor"
    write_launcher(home, "code", model)
    monkeypatch.setattr(seed, "recorded_session", lambda h: None)
    stub_idle(monkeypatch, True)
    stub_preflight(monkeypatch)

    r1 = rot.apply(home, "code", route("go-b", "opencode-go", model), generation=1, now_ms=NOW)
    assert r1["state"] == "ok"
    # Simulate peer advancing generation to 2 via direct state edit
    import json as _json

    state_path = home / "engine" / "rotation.json"
    st = _json.loads(state_path.read_text())
    st["generation"] = 2
    st.setdefault("history", []).append({"generation": 2, "role": "code", "route": route("go-a", "opencode-go", model), "reason": "peer", "requested_ms": NOW})
    state_path.write_text(_json.dumps(st, sort_keys=True, indent=2))
    # Now try to apply generation 2 again via CAS: should mismatch or be idempotent?
    # generation 2 already exists; applying same generation with same route is idempotent, with different route also idempotent (no mismatch)
    # To simulate true concurrent stale write, attempt generation 1 again -> mismatch because current is 2
    with pytest.raises(ValueError, match="generation-mismatch"):
        rot.apply(home, "code", route("go-a", "opencode-go", model), generation=1, now_ms=NOW)
    # Attempt skipped generation 4 (should be 3) -> mismatch
    with pytest.raises(ValueError, match="generation-mismatch"):
        rot.apply(home, "code", route("go-a", "opencode-go", model), generation=4, now_ms=NOW)


def test_failure_before_effects_leaves_launcher_no_duplicate(tmp_path, monkeypatch):
    from mishe_tauftauf import engine_rotation as rot
    import mishe_tauftauf.seed as seed

    home = make_home(tmp_path, name="site5")
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "go-b": {"provider": "opencode-go", "state": "configured", "credential_id": 9, "store": "engine/go-b-store"},
        },
    )
    model = "opencode-go/muse-spark-1.2-contributor"
    write_launcher(home, "code", model, store="omp-recovery/agent")
    monkeypatch.setattr(seed, "recorded_session", lambda h: None)
    stub_idle(monkeypatch, True)

    # preflight fails
    def fail_preflight(cfg, target):
        raise ValueError("exact model/catalog unavailable in this runtime")

    monkeypatch.setattr(model_boundary, "preflight", fail_preflight)
    before = (home / "minds/code").read_bytes()
    # use a different model to trigger preflight
    with pytest.raises(ValueError, match="catalog unavailable"):
        rot.apply(home, "code", route("go-b", "opencode-go", "minimax-code/MiniMax-M3.1"), generation=1, now_ms=NOW)
    assert (home / "minds/code").read_bytes() == before
    # generation still 0, no history written
    state_path = home / "engine" / "rotation.json"
    if state_path.exists():
        st = json.loads(state_path.read_text())
        assert st.get("generation", 0) == 0

    # Now make preflight succeed and apply should work
    stub_preflight(monkeypatch)
    res = rot.apply(home, "code", route("go-b", "opencode-go", model), generation=1, now_ms=NOW)
    assert res["state"] == "ok"


def test_failure_after_tool_action_unresolved_no_replay(tmp_path, monkeypatch):
    from mishe_tauftauf import engine_rotation as rot
    import mishe_tauftauf.seed as seed

    home = make_home(tmp_path, name="site6")
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "go-b": {"provider": "opencode-go", "state": "configured", "credential_id": 9, "store": "engine/go-b-store"},
        },
    )
    model = "opencode-go/muse-spark-1.2-contributor"
    write_launcher(home, "code", model, store="omp-recovery/agent")
    monkeypatch.setattr(seed, "recorded_session", lambda h: None)
    stub_idle(monkeypatch, True)
    stub_preflight(monkeypatch)

    # route with sentinel that causes post-save failure
    r = route("go-b", "opencode-go", model)
    r["_post_fail"] = True
    with pytest.raises(RuntimeError, match="simulated post-condition"):
        rot.apply(home, "code", r, generation=1, now_ms=NOW)
    # launcher was changed despite failure (after tool action)
    assert "engine/go-b-store" in (home / "minds/code").read_text()
    # but generation was persisted (after-save failure). Retry with same generation should be idempotent no replay
    before_mtime = (home / "minds/code").stat().st_mtime_ns
    res = rot.apply(home, "code", r, generation=1, now_ms=NOW)
    assert res.get("idempotent") is True
    assert (home / "minds/code").stat().st_mtime_ns == before_mtime


def test_idempotent_retry_same_generation_no_churn(tmp_path, monkeypatch):
    from mishe_tauftauf import engine_rotation as rot
    import mishe_tauftauf.seed as seed

    home = make_home(tmp_path, name="site7")
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "go-b": {"provider": "opencode-go", "state": "configured", "credential_id": 9, "store": "engine/go-b-store"},
        },
    )
    model = "opencode-go/muse-spark-1.2-contributor"
    write_launcher(home, "code", model)
    monkeypatch.setattr(seed, "recorded_session", lambda h: None)
    stub_idle(monkeypatch, True)
    stub_preflight(monkeypatch)

    r = route("go-b", "opencode-go", model)
    res1 = rot.apply(home, "code", r, generation=1, now_ms=NOW)
    mtime1 = (home / "minds/code").stat().st_mtime_ns
    digest1 = model_boundary.digest((home / "minds/code").read_bytes())
    res2 = rot.apply(home, "code", r, generation=1, now_ms=NOW)
    assert res2.get("idempotent") is True
    assert res2["generation"] == 1
    assert (home / "minds/code").stat().st_mtime_ns == mtime1
    assert model_boundary.digest((home / "minds/code").read_bytes()) == digest1
    # also prepare+apply idempotency via prepare helper
    prep = rot.prepare(home, "code", r, generation=1, now_ms=NOW)
    assert prep["receipt_id"] == "00000001-code"


def test_revert_exact_restore(tmp_path, monkeypatch):
    from mishe_tauftauf import engine_rotation as rot
    import mishe_tauftauf.seed as seed

    home = make_home(tmp_path, name="site8")
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "go-b": {"provider": "opencode-go", "state": "configured", "credential_id": 9, "store": "engine/go-b-store"},
        },
    )
    model = "opencode-go/muse-spark-1.2-contributor"
    p = write_launcher(home, "code", model, store="omp-recovery/agent")
    original = p.read_bytes()
    monkeypatch.setattr(seed, "recorded_session", lambda h: None)
    stub_idle(monkeypatch, True)
    stub_preflight(monkeypatch)

    rot.apply(home, "code", route("go-b", "opencode-go", model), generation=1, now_ms=NOW)
    assert (home / "minds/code").read_bytes() != original
    result = rot.revert(home, generation=1)
    assert result["state"] == "reverted"
    assert (home / "minds/code").read_bytes() == original
    # generation monotonic: still 1, not reused as new. Idempotent retry of same generation does not churn.
    res_same = rot.apply(home, "code", route("go-b", "opencode-go", model), generation=1, now_ms=NOW)
    assert res_same.get("idempotent") is True
    assert (home / "minds/code").read_bytes() == original
    # skipping generation would be mismatch
    with pytest.raises(ValueError, match="generation-mismatch"):
        rot.apply(home, "code", route("go-b", "opencode-go", model), generation=4, now_ms=NOW)
    # generation 2 succeeds
    res = rot.apply(home, "code", route("go-b", "opencode-go", model), generation=2, now_ms=NOW)
    assert res["generation"] == 2


def test_account_store_helper_and_instrument_alias(tmp_path, monkeypatch):
    home = make_home(tmp_path, name="site9")
    write_registry(
        home,
        {
            "go-a": {"provider": "opencode-go", "state": "configured", "credential_id": 8},
            "go-b": {"provider": "opencode-go", "state": "configured", "credential_id": 9, "store": "engine/go-b-store"},
        },
    )
    write_launcher(home, "code", "opencode-go/muse-spark-1.2-contributor", store="engine/go-b-store")
    assert model_boundary.account_store_for_role(home, "code") == "engine/go-b-store"

    # instrument carries alias
    text = (home / "minds/code").read_text()
    cfg = model_boundary.launcher(text)
    out = model_boundary.instrument(cfg, "gen1-code", home / "engine" / "rotation" / "1" / "code", alias="go-b")
    assert "MISHE_ENGINE_ALIAS=go-b" in out or "MISHE_ENGINE_ALIAS='go-b'" in out
    assert "MISHE_MODEL_CHANGE=gen1-code" in out
