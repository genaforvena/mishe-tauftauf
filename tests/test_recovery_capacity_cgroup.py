"""The recovery state's capacity must see the cgroup memory high-water mark.

2026-11-01 evidence: recovery/host-samples.jsonl last row had memory.current == memory.high
(ratio 1.000) with PSI memory_full 0.05 and io_full 5.48. host_capacity.decide classified it
CRITICAL, while recovery_policy.capacity (which writes state.json capacity) said NORMAL and
restoration proceeded on that reading. The policy must carry the same cgroup dimension.
"""
import math

from mishe_tauftauf import host_capacity
from mishe_tauftauf import recovery_policy as policy

HIGH = 17179869184
NOW = 1793577297.0


def pressure(**overrides):
    row = {"memory_full": .05, "io_full": 5.48, "available_ratio": .539}
    row.update(overrides)
    return row


def test_cgroup_at_high_water_is_not_normal_even_with_quiet_pressure():
    row = policy.capacity(None, now=NOW, recover_seconds=120, max_gap=60,
                          current=HIGH, high=HIGH, **pressure())
    assert row["state"] != "NORMAL"
    assert row["limited"] is True


def test_policy_agrees_with_host_capacity_on_the_live_sample():
    observation = dict(pressure(), current=HIGH, high=HIGH,
                       max=18253611008, events={"oom_kill": 0})
    controller = host_capacity.decide(None, observation, now=NOW)
    row = policy.capacity(None, now=NOW, recover_seconds=120, max_gap=60,
                          current=observation["current"], high=observation["high"],
                          **pressure())
    assert controller["limited"] is True
    assert row["limited"] is True


def test_cgroup_clear_needs_sustained_band_below_085_high():
    below = int(HIGH * .8)
    row = policy.capacity(None, now=0, recover_seconds=60, max_gap=60,
                          current=HIGH, high=HIGH, **pressure())
    assert row["limited"] is True
    row = policy.capacity(row, now=5, recover_seconds=60, max_gap=60,
                          current=below, high=HIGH, **pressure(memory_full=0, io_full=0))
    assert row["limited"] is True
    row = policy.capacity(row, now=65, recover_seconds=60, max_gap=60,
                          current=below, high=HIGH, **pressure(memory_full=0, io_full=0))
    assert row["limited"] is False
    assert row["state"] == "NORMAL"


def test_in_band_plateau_holds_clear_timer_instead_of_resetting_it():
    # 0.85..0.90 x high is in the band: neither overloaded nor clear. A running clear timer must
    # survive it, or a plateau straddling the cut latches LIMITED (the 2026-11-01 flap).
    row = policy.capacity(None, now=0, recover_seconds=60, max_gap=60,
                          current=int(HIGH * .8), high=HIGH, **pressure(memory_full=0, io_full=0))
    row = policy.capacity(row, now=10, recover_seconds=60, max_gap=60,
                          current=int(HIGH * .87), high=HIGH, **pressure(memory_full=0, io_full=0))
    assert row["clear_since"] is not None
    row = policy.capacity(row, now=70, recover_seconds=60, max_gap=60,
                          current=int(HIGH * .8), high=HIGH, **pressure(memory_full=0, io_full=0))
    assert row["limited"] is False


def test_missing_cgroup_reading_is_unknown_not_normal():
    row = policy.capacity(None, now=0, recover_seconds=60, max_gap=60,
                          current=math.nan, high=math.nan, **pressure())
    assert row["state"] == "UNKNOWN"
    assert row["limited"] is True


def test_callers_without_cgroup_keep_the_pressure_only_behaviour():
    row = policy.capacity(None, now=0, recover_seconds=60, **pressure(memory_full=0, io_full=0))
    assert row["state"] == "NORMAL"
