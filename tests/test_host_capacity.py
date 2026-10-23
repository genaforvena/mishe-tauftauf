from pathlib import Path
import fcntl
import json
import sys

import pytest

from mishe_tauftauf import host_capacity as host
from mishe_tauftauf import recovery_io as recovery


def sample(current=1, oom=0):
    return {"memory_full": 0., "io_full": 0., "available_ratio": .5,
            "current": current, "high": 1000, "max": 1200,
            "events": {"oom_kill": oom, "high": 0}}


def test_pressure_closes_admission_and_requires_continuous_recovery():
    row = host.decide(None, sample(current=1150), now=100, stable_seconds=20)
    assert row["state"] == "CRITICAL" and row["slots"] == 0
    row = host.decide(row, sample(), now=105, stable_seconds=20)
    assert row["limited"]
    row = host.decide(row, sample(), now=200, stable_seconds=20)
    assert row["limited"], "missing intervals break stability"
    for now in (205, 210, 215, 220):
        row = host.decide(row, sample(), now=now, stable_seconds=20)
    assert row["state"] == "NORMAL"


def test_old_oom_counter_is_retained_without_permanent_overload():
    row = host.decide(None, sample(oom=9), now=100, stable_seconds=20)
    row = host.decide(row, sample(oom=10), now=105, stable_seconds=20)
    assert row["state"] == "CRITICAL" and row["oom_delta"] == 1
    for now in (110, 115, 120, 125, 130):
        row = host.decide(row, sample(oom=10), now=now, stable_seconds=20)
    assert row["state"] == "NORMAL" and row["observation"]["events"]["oom_kill"] == 10


def test_missing_and_stale_host_observation_is_conservative(tmp_path):
    assert host.status(tmp_path, now=100)["state"] == "UNKNOWN"
    row = host.decide(None, None, now=100)
    assert row["state"] == "UNKNOWN" and row["slots"] <= 1
    recovery.save(tmp_path / "host-capacity.json", host.decide(None, sample(), now=100))
    assert host.status(tmp_path, now=200)["state"] == "UNKNOWN"


def test_shared_admission_cannot_use_local_normal_when_host_is_critical(tmp_path):
    from mishe_tauftauf import render_admission
    shared = tmp_path / "shared"
    shared.mkdir()
    recovery.save(shared / "host-capacity.json", host.decide(None, sample(current=1150), now=100))
    for role in ("health", "genome"):
        site = tmp_path / role
        (site / "recovery").mkdir(parents=True)
        recovery.save(site / "recovery/config.json", {"host_root": str(shared)})
        row = render_admission.render(site, role, [sys.executable, "-c", "print('must not run')"], now=100)
        assert row["code"] != 0 and "must not run" not in row["stdout"]


def test_host_reader_rejects_bad_slots_in_otherwise_fresh_state(tmp_path):
    row = host.decide(None, sample(), now=100)
    row["slots"] = 800
    recovery.save(tmp_path / "host-capacity.json", row)
    assert host.status(tmp_path, now=100)["state"] == "UNKNOWN"


def test_reduction_restores_only_units_it_actually_stopped(tmp_path, monkeypatch):
    active = {"owned-optional.service"}
    actions = []
    monkeypatch.setattr(host, "unit_states", lambda names: {n: "active" if n in active else "inactive" for n in names})
    def act(verb, units):
        actions.append((verb, list(units)))
        for unit in units:
            if verb == "stop": active.discard(unit)
            else: active.add(unit)
        return {"ok": True, "stdout": "", "stderr": ""}
    monkeypatch.setattr(host, "unit_action", act)
    cfg = {"optional_units": ["owned-optional.service", "already-down.service"]}
    row = host.decide(None, sample(current=1150), now=100)
    reduced = host.reconcile(tmp_path, row, cfg)
    assert reduced["managed_units"] == ["owned-optional.service"]
    normal = host.decide(reduced, sample(), now=105, stable_seconds=0)
    restored = host.reconcile(tmp_path, normal, cfg)
    assert not restored["managed_units"]
    assert actions == [("stop", ["owned-optional.service"]), ("start", ["owned-optional.service"])]


def test_failed_stop_does_not_claim_restore_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "unit_states", lambda names: {n: "active" for n in names})
    monkeypatch.setattr(host, "unit_action", lambda *a: {"ok": False, "stderr": "rejected"})
    row = host.reconcile(tmp_path, host.decide(None, sample(current=1150), now=100),
                         {"optional_units": ["optional.service"]})
    assert not row["managed_units"]
    assert row.get("action_error")


def test_async_start_keeps_ownership_until_observed_active(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "unit_states", lambda names: {n: "inactive" for n in names})
    monkeypatch.setattr(host, "unit_action", lambda *a: {"ok": True, "stderr": ""})
    row = host.decide(None, sample(), now=100)
    row["managed_units"] = ["optional.service"]
    result = host.reconcile(tmp_path, row, {"optional_units": ["optional.service"]})
    assert result["managed_units"] == ["optional.service"]


def test_interrupted_stop_does_not_authorize_blind_restore(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "unit_states", lambda names: {n: "inactive" for n in names})
    monkeypatch.setattr(host, "unit_action", lambda *a: pytest.fail("unknown stop must not authorize start"))
    row = host.decide(None, sample(), now=100)
    row["pending_stops"] = {"optional.service": {"phase": "intent", "at": 90}}
    row = host.reconcile(tmp_path, row, {"optional_units": ["optional.service"]})
    assert row.get("action_error")
    assert not row["managed_units"]


def test_critical_admission_stays_closed_when_action_or_guard_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "observe", lambda _: sample(current=1150))
    monkeypatch.setattr(host, "reconcile", lambda *a: (_ for _ in ()).throw(ValueError("controlled failure")))
    row = host.tick(tmp_path, {"slice_path": "unused", "optional_units": []}, now=100)
    assert row["state"] == "UNKNOWN" and row["slots"] == 0


def test_accepted_but_failed_restoration_has_bounded_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "unit_states", lambda names: {n: "inactive" for n in names})
    monkeypatch.setattr(host, "unit_action", lambda *a: {"ok": True, "stderr": ""})
    row = host.decide(None, sample(), now=100)
    row["managed_units"] = ["optional.service"]
    row = host.reconcile(tmp_path, row, {"optional_units": ["optional.service"]})
    row = host.decide(row, sample(), now=135)
    row = host.reconcile(tmp_path, row, {"optional_units": ["optional.service"]})
    assert row.get("action_error") and "30s" in row["action_error"]
    assert row["managed_units"] == ["optional.service"]


@pytest.mark.parametrize("state", ["", "invented"])
def test_invalid_unit_state_cannot_disappear_from_reconciliation(monkeypatch, state):
    monkeypatch.setattr(recovery, "run_probe", lambda *a, **k: {"ok": True, "stdout": "Id=optional.service\nActiveState=" + state, "stderr": ""})
    with pytest.raises(ValueError, match="state"):
        host.unit_states(["optional.service"])


def test_incomplete_guard_is_not_coverage(tmp_path, monkeypatch):
    recovery.save(tmp_path / "host-capacity-guard.json", {"checked_at": 100})
    monkeypatch.setattr(host, "observe", lambda _: sample())
    row = host.tick(tmp_path, {"slice_path": "unused", "optional_units": []}, now=100)
    assert row["state"] == "UNKNOWN" and not row["guard_fresh"]
