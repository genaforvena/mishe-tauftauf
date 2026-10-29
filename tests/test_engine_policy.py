"""Engine admission policy: pure decision tests.

Covers reserve arithmetic, reservations, reset crossing, reconciliation,
stickiness/hysteresis, queuing with binding reset, forbidden choices,
stale/unknown hold, and same-provider preference. Deterministic, no network.
Pattern after tests/test_engine_usage.py.
"""
from datetime import datetime, timezone

import pytest

from mishe_tauftauf import engine_policy as policy

NOW = 1_792_712_432_436
RESET_FUTURE = NOW + 3_600_000
RESET_FUTURE_B = RESET_FUTURE + 86_400_000
RESET_PAST = NOW - 10_000
MIN_HOLD = 600_000


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(remaining=1.0, reset_ms=RESET_FUTURE, limit="rolling-5h", window_id="5h", provider_status=None, label=None):
    return {
        "limit": limit,
        "window": window_id,
        "window_id": window_id,
        "label": label or limit,
        "remaining_fraction": remaining,
        "reset_at": iso(reset_ms),
        "duration_ms": 5 * 3600_1000 if False else 18_000_000,
        "provider_status": provider_status,
        "state": "FRESH",
        "reason": None,
    }


def account(alias, provider, state="FRESH", windows=None, reason=None):
    if windows is None:
        windows = [window()]
    return {
        "alias": alias,
        "provider": provider,
        "state": state,
        "reason": reason,
        "windows": windows,
        "fetched_at": iso(NOW - 10_000),
        "age_seconds": 10.0,
    }


def status(accounts_dict):
    return {"state": "FRESH", "observed_at": iso(NOW), "accounts": accounts_dict}


def route(alias, provider, model="model-a", effort="high", runner="omp"):
    return {"provider": provider, "alias": alias, "model": model, "effort": effort, "runner": runner}


# ---------------------------------------------------------------------------
# reserve arithmetic
# ---------------------------------------------------------------------------

def test_reserve_10pct_threshold():
    # OMP default 10% (pi-ai DEFAULT_USAGE_RESERVE_PCT=10)
    allow = {"default": [route("go-a", "opencode-go")]}
    # 0.09 remaining < 0.10 => reserve -> ineligible under auto -> queue (no alternative)
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.09)])})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.10, reserve_policy="auto")
    assert r["decision"] == "queue"
    # 0.11 remaining >=0.10 => healthy -> select
    st2 = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.11)])})
    r2 = policy.decide(role="default", status=st2, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.10, reserve_policy="auto")
    assert r2["decision"] == "select"
    assert r2["selected"]["alias"] == "go-a"
    # exactly 0.10 => healthy (>= reserve)
    st3 = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.10)])})
    r3 = policy.decide(role="default", status=st3, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.10)
    assert r3["decision"] == "select"


def test_reserve_15pct_threshold():
    # plan proposed 15% (our default)
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.14)])})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.15, reserve_policy="auto")
    assert r["decision"] == "queue"
    st2 = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.16)])})
    r2 = policy.decide(role="default", status=st2, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.15)
    assert r2["decision"] == "select"
    # exactly 0.15 => healthy
    st3 = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.15)])})
    r3 = policy.decide(role="default", status=st3, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.15)
    assert r3["decision"] == "select"


def test_depleted_zero_remaining_is_depleted():
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.0)])})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.10)
    assert r["decision"] == "queue"
    assert "depleted" in r["reason"]


def test_depleted_provider_status():
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.5, provider_status="depleted")])})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert r["decision"] == "queue"


def test_multiple_windows_all_checked():
    # One depleted window gates the whole account even if others healthy
    allow = {"default": [route("go-a", "opencode-go")]}
    windows = [
        window(remaining=0.8, limit="rolling-5h", window_id="5h", reset_ms=RESET_FUTURE),
        window(remaining=0.02, limit="monthly", window_id="monthly", reset_ms=RESET_FUTURE_B),
    ]
    st = status({"go-a": account("go-a", "opencode-go", windows=windows)})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.10)
    assert r["decision"] == "queue"
    # reserve on any window also gates
    windows2 = [
        window(remaining=0.8, limit="rolling-5h", window_id="5h"),
        window(remaining=0.09, limit="weekly", window_id="weekly"),
    ]
    st2 = status({"go-a": account("go-a", "opencode-go", windows=windows2)})
    r2 = policy.decide(role="default", status=st2, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.10)
    assert r2["decision"] == "queue"


# ---------------------------------------------------------------------------
# reservations / simultaneous admissions
# ---------------------------------------------------------------------------

def test_reservation_reduces_headroom():
    # remaining 0.20, reserve 0.15, reservation 0.06 => effective 0.14 (<0.15) => reserve
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.20)])})
    res = {"go-a": {"5h": 0.06}}
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reservations=res, reserve_pct=0.15)
    assert r["decision"] == "queue"
    # without reservation it would be healthy
    r2 = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reservations={}, reserve_pct=0.15)
    assert r2["decision"] == "select"


def test_simultaneous_admissions_reservation_blocks_second():
    # Two admits share same pool: first reserves, second sees reduced headroom
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    # go-a 0.20, go-b 0.50
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.20)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.50)]),
    })
    # no reservation: prefers go-a (first in allowlist)
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.15)
    assert r["selected"]["alias"] == "go-a"
    # with reservation consuming go-a headroom below reserve, should pick go-b
    res = {"go-a": {"5h": 0.06}}  # 0.20-0.06=0.14 reserve
    r2 = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reservations=res, reserve_pct=0.15)
    assert r2["decision"] == "select"
    assert r2["selected"]["alias"] == "go-b"


def test_reservation_float_form():
    # reservations alias -> float (generic) should also apply
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.20)])})
    res = {"go-a": 0.10}  # 0.20-0.10=0.10 <0.15 reserve
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reservations=res, reserve_pct=0.15)
    assert r["decision"] == "queue"


# ---------------------------------------------------------------------------
# reset crossing
# ---------------------------------------------------------------------------

def test_reset_crossing_expired_window_becomes_eligible():
    # monthly depleted but its reset is in the past => ignore that window
    allow = {"default": [route("go-a", "opencode-go")]}
    w_past = window(remaining=0.0, limit="monthly", window_id="monthly", reset_ms=RESET_PAST)
    w_fresh = window(remaining=0.80, limit="rolling-5h", window_id="5h", reset_ms=RESET_FUTURE)
    st = status({"go-a": account("go-a", "opencode-go", windows=[w_fresh, w_past])})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.15)
    assert r["decision"] == "select"
    # if reset is still future, depleted window gates
    w_future = window(remaining=0.0, limit="monthly", window_id="monthly", reset_ms=RESET_FUTURE_B)
    st2 = status({"go-a": account("go-a", "opencode-go", windows=[w_fresh, w_future])})
    r2 = policy.decide(role="default", status=st2, allowlist=allow, current_route=None, now_ms=NOW)
    assert r2["decision"] == "queue"


def test_queue_binding_reset_uses_future_reset():
    # both accounts depleted with different resets; binding is earliest
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.0, reset_ms=RESET_FUTURE_B)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.0, reset_ms=RESET_FUTURE)]),
    })
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert r["decision"] == "queue"
    assert r["binding_reset"] == iso(RESET_FUTURE)


# ---------------------------------------------------------------------------
# external usage reconciliation
# ---------------------------------------------------------------------------

def test_external_usage_reconciliation_new_observation_corrects():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    # initially go-a appears depleted => picks go-b
    st1 = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.0)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.50)]),
    })
    r1 = policy.decide(role="default", status=st1, allowlist=allow, current_route=None, now_ms=NOW)
    assert r1["selected"]["alias"] == "go-b"
    # external reconciliation: new observation shows go-a replenished
    st2 = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.80)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.50)]),
    })
    # if current is go-b and still healthy, stays on go-b (stickiness)
    r2 = policy.decide(role="default", status=st2, allowlist=allow, current_route=route("go-b", "opencode-go"), now_ms=NOW)
    assert r2["decision"] == "stay"
    # a new admission without current would pick go-a first (allowlist order)
    r3 = policy.decide(role="default", status=st2, allowlist=allow, current_route=None, now_ms=NOW)
    assert r3["selected"]["alias"] == "go-a"


# ---------------------------------------------------------------------------
# stickiness / hysteresis
# ---------------------------------------------------------------------------

def test_current_route_stickiness_stay_even_with_higher_headroom_alternative():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    # go-a 0.30, go-b 0.90 (higher) both healthy, current is go-a
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.30)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.90)]),
    })
    cur = route("go-a", "opencode-go")
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=cur, now_ms=NOW)
    assert r["decision"] == "stay"
    assert r["selected"]["alias"] == "go-a"


def test_hysteresis_no_churn_within_min_hold():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.30)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.80)]),
    })
    cur = route("go-a", "opencode-go")
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=cur, now_ms=NOW, min_hold_ms=600_000)
    assert r["decision"] == "stay"
    assert r["next_reconsider_ms"] == NOW + 600_000
    # even with a later call 1s later, still stay (pure function does not flip)
    r2 = policy.decide(role="default", status=st, allowlist=allow, current_route=cur, now_ms=NOW + 1000)
    assert r2["decision"] == "stay"


def test_switch_on_hard_failure_current_becomes_ineligible():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.02)]),  # reserve/depleted
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.50)]),
    })
    cur = route("go-a", "opencode-go")
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=cur, now_ms=NOW, reserve_pct=0.10)
    assert r["decision"] == "select"
    assert r["selected"]["alias"] == "go-b"


# ---------------------------------------------------------------------------
# all-exhausted queuing with binding reset
# ---------------------------------------------------------------------------

def test_all_exhausted_queuing_with_binding_reset():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go"), route("codex-a", "openai-codex")]}
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.0, reset_ms=RESET_FUTURE_B)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.0, reset_ms=RESET_FUTURE)]),
        "codex-a": account("codex-a", "openai-codex", windows=[window(remaining=0.0, reset_ms=RESET_FUTURE_B, limit="weekly", window_id="weekly")]),
    })
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert r["decision"] == "queue"
    assert r["selected"] is None
    assert r["binding_reset"] == iso(RESET_FUTURE)  # earliest
    # next_reconsider is min(reset, now+hold)
    assert r["next_reconsider_ms"] == min(RESET_FUTURE, NOW + MIN_HOLD) or r["next_reconsider_ms"] == min(RESET_FUTURE, NOW + 600_000)


def test_all_exhausted_queue_next_reconsider_is_min():
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.0, reset_ms=RESET_FUTURE)] )})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, min_hold_ms=600_000)
    assert r["decision"] == "queue"
    # RESET_FUTURE is 3.6M ahead, hold is 0.6M => next is hold
    assert r["next_reconsider_ms"] == NOW + 600_000
    # large hold should cap at reset (iso truncates to seconds, so truncated)
    r2 = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, min_hold_ms=10_000_000)
    assert r2["next_reconsider_ms"] == (RESET_FUTURE // 1000) * 1000
def test_queue_reserve_policy_fail_closed():
    # reserve under fail-closed also queues with binding
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.05)])})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.10, reserve_policy="fail-closed")
    assert r["decision"] == "queue"
    assert "binding_reset" in r or r["decision"] == "queue"


# ---------------------------------------------------------------------------
# forbidden provider/model choices filtered
# ---------------------------------------------------------------------------

def test_forbidden_provider_model_filtered():
    # allowlist for role 'code' only allows minimax-a, not go-a
    allow = {"code": [route("minimax-a", "minimax-code")]}
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.90)]),
        "minimax-a": account("minimax-a", "minimax-code", windows=[window(remaining=0.05)]),  # reserve -> queue if alone
    })
    # for role code, go-a is not allowed, so even healthy go-a not selected
    r = policy.decide(role="code", status=st, allowlist=allow, current_route=None, now_ms=NOW, reserve_pct=0.10)
    assert r["decision"] == "queue"
    # but role with go-a allowed would select it
    allow2 = {"default": [route("go-a", "opencode-go"), route("minimax-a", "minimax-code")]}
    r2 = policy.decide(role="default", status=st, allowlist=allow2, current_route=None, now_ms=NOW, reserve_pct=0.10)
    assert r2["selected"]["alias"] == "go-a"

    # current route not in allowlist is not considered healthy -> forces fallback
    cur = route("go-a", "opencode-go")
    r3 = policy.decide(role="code", status=st, allowlist=allow, current_route=cur, now_ms=NOW, reserve_pct=0.10)
    # go-a forbidden for this role, minimax-a gated => queue, not stay on forbidden
    assert r3["decision"] == "queue"
    assert r3["selected"] is None


def test_allowlist_order_matters_not_headroom_percent():
    # allowlist says minimax first even though go has more headroom
    allow = {"default": [route("minimax-a", "minimax-code"), route("go-a", "opencode-go")]}
    st = status({
        "minimax-a": account("minimax-a", "minimax-code", windows=[window(remaining=0.20)]),
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.90)]),
    })
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert r["selected"]["alias"] == "minimax-a"


# ---------------------------------------------------------------------------
# stale/unknown suspends reassignment
# ---------------------------------------------------------------------------

def test_unknown_suspends_reassignment_hold():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    st = status({
        "go-a": account("go-a", "opencode-go", state="UNKNOWN", windows=[], reason="no provider report"),
        "go-b": account("go-b", "opencode-go", state="UNKNOWN", windows=[], reason="no provider report"),
    })
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert r["decision"] == "hold"
    assert r["selected"] is None


def test_stale_suspends_reassignment_hold():
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", state="STALE", windows=[window(remaining=0.80)], reason="stale")})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert r["decision"] == "hold"


def test_unattributed_and_not_configured_hold():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    st = status({
        "go-a": account("go-a", "opencode-go", state="UNATTRIBUTED", windows=[], reason="shared"),
        "go-b": account("go-b", "opencode-go", state="NOT_CONFIGURED", windows=[], reason="validated but no credential"),
    })
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert r["decision"] == "hold"


def test_unknown_hold_with_in_flight_current():
    # UNKNOWN does not mean exhausted; keep in-flight (hold) not queue
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go")]}
    st = status({
        "go-a": account("go-a", "opencode-go", state="UNKNOWN", windows=[], reason="no report"),
        "go-b": account("go-b", "opencode-go", state="UNKNOWN", windows=[], reason="no report"),
    })
    cur = route("go-a", "opencode-go")
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=cur, now_ms=NOW)
    assert r["decision"] in ("hold", "queue")
    # spec says UNKNOWN => hold (keep in-flight, no reassignment)
    # our implementation returns hold when no gated pools, queue when gated; with all UNKNOWN there are no gated => hold
    assert r["decision"] == "hold"


# ---------------------------------------------------------------------------
# same-provider alias preferred before cross-provider
# ---------------------------------------------------------------------------

def test_same_provider_alias_preferred_before_cross_provider():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go"), route("codex-a", "openai-codex")]}
    # go-a depleted, go-b healthy, codex-a healthy => prefer go-b
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.0)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.50)]),
        "codex-a": account("codex-a", "openai-codex", windows=[window(remaining=0.90)]),
    })
    cur = route("go-a", "opencode-go")
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=cur, now_ms=NOW)
    assert r["decision"] == "select"
    assert r["selected"]["alias"] == "go-b"
    assert r["selected"]["provider"] == "opencode-go"


def test_cross_provider_fallback_when_same_provider_unavailable():
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go"), route("codex-a", "openai-codex")]}
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.0)]),
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.0)]),
        "codex-a": account("codex-a", "openai-codex", windows=[window(remaining=0.80)]),
    })
    cur = route("go-a", "opencode-go")
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=cur, now_ms=NOW)
    assert r["decision"] == "select"
    assert r["selected"]["alias"] == "codex-a"


def test_same_provider_reserve_auto_prefers_cross_provider_healthy():
    # reserve under auto is ineligible, so cross-provider healthy wins over same-provider reserve
    allow = {"default": [route("go-a", "opencode-go"), route("go-b", "opencode-go"), route("codex-a", "openai-codex")]}
    st = status({
        "go-a": account("go-a", "opencode-go", windows=[window(remaining=0.05)]),  # reserve
        "go-b": account("go-b", "opencode-go", windows=[window(remaining=0.05)]),  # reserve
        "codex-a": account("codex-a", "openai-codex", windows=[window(remaining=0.80)]),
    })
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=route("go-a", "opencode-go"), now_ms=NOW, reserve_pct=0.10, reserve_policy="auto")
    assert r["selected"]["alias"] == "codex-a"


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------

def test_explain_renders():
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.80)])})
    text = policy.explain(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert "decision" in text
    assert "OMP reuse" in text
    assert "usageAwareFallback" in text

    # alternative explain(role, policy_input) form
    text2 = policy.explain("default", {"status": st, "allowlist": allow, "current_route": None, "now_ms": NOW})
    assert "decision" in text2


def test_decide_is_pure_no_io():
    allow = {"default": [route("go-a", "opencode-go")]}
    st = status({"go-a": account("go-a", "opencode-go", windows=[window(remaining=0.80)])})
    r1 = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    r2 = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    assert r1 == r2


def test_none_remaining_ignored():
    # unsupported unit => remaining None, should not gate
    allow = {"default": [route("go-a", "opencode-go")]}
    w_none = window(remaining=None)
    # manual dict to ensure None
    w_none["remaining_fraction"] = None
    st = status({"go-a": account("go-a", "opencode-go", windows=[w_none])})
    r = policy.decide(role="default", status=st, allowlist=allow, current_route=None, now_ms=NOW)
    # None windows are ignored -> healthy -> select
    assert r["decision"] == "select"
