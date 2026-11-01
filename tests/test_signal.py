import pytest

from mishe_tauftauf import signal_algebra as sig


def test_fresh_observation_is_ok_until_max_age_then_stale():
    s = sig.observed(5, observed_at=100.0, source="psi")
    assert sig.expire(s, now=110.0, max_age=10.0).state == sig.OK
    assert sig.expire(s, now=110.5, max_age=10.0).state == sig.STALE


def test_future_observation_is_not_trusted():
    s = sig.observed(5, observed_at=100.0, source="psi")
    assert sig.expire(s, now=99.0, max_age=10.0).state == sig.STALE


def test_unknown_and_stale_pass_through_expire_unchanged():
    u = sig.unknown("psi", "missing file")
    assert sig.expire(u, now=1e9, max_age=1.0) is u


def test_ok_signal_requires_a_time_and_bad_state_is_rejected():
    with pytest.raises(ValueError):
        sig.Signal(1, None, "x", sig.OK)
    with pytest.raises(ValueError):
        sig.Signal(1, 1.0, "x", "MAYBE")


def test_map_keeps_time_source_and_state():
    s = sig.observed(4, observed_at=7.0, source="memory")
    m = sig.map_value(s, lambda v: v * 2)
    assert (m.value, m.observed_at, m.source, m.state) == (8, 7.0, "memory", sig.OK)


def test_map_failure_is_unknown_not_ok():
    s = sig.observed("abc", observed_at=7.0, source="memory")
    m = sig.map_value(s, lambda v: int(v))
    assert m.state == sig.UNKNOWN
    assert "map failed" in m.value


def test_map_does_not_call_fn_on_unknown():
    calls = []
    u = sig.unknown("memory", "gone")
    assert sig.map_value(u, lambda v: calls.append(v)) is u
    assert calls == []


def test_join_worst_state_wins_unknown_over_stale_over_ok():
    ok = sig.observed(1, 10.0, "a")
    stale = sig.observed(2, 1.0, "b")
    stale = sig.expire(stale, now=100.0, max_age=5.0)
    unk = sig.unknown("c", "no read")
    assert sig.join([ok, ok]).state == sig.OK
    assert sig.join([ok, stale]).state == sig.STALE
    assert sig.join([ok, stale, unk]).state == sig.UNKNOWN


def test_join_observed_at_is_the_oldest_input():
    j = sig.join([sig.observed(1, 10.0, "a"), sig.observed(2, 4.0, "b")])
    assert j.observed_at == 4.0
    assert j.value == [1, 2]


def test_join_with_an_unknown_has_no_fake_time_and_keeps_reasons():
    j = sig.join([sig.observed(1, 10.0, "a"), sig.unknown("b", "missing")])
    assert j.state == sig.UNKNOWN
    assert j.observed_at is None
    assert "missing" in j.value


def test_join_of_nothing_is_unknown():
    assert sig.join([]).state == sig.UNKNOWN


def test_join_combine_is_applied_to_ok_values():
    j = sig.join([sig.observed(1, 1.0, "a"), sig.observed(2, 1.0, "b")], combine=sum)
    assert j.value == 3 and j.state == sig.OK


def test_hold_keeps_last_ok_across_a_short_gap_then_expires():
    prev = sig.observed("LIMITED", observed_at=100.0, source="host")
    gap = sig.unknown("host", "read failed")
    held = sig.hold(prev, gap, now=105.0, max_age=10.0)
    assert held.state == sig.OK and held.value == "LIMITED"
    expired = sig.hold(prev, gap, now=120.0, max_age=10.0)
    assert expired.state == sig.UNKNOWN


def test_hold_lets_a_real_reading_win():
    prev = sig.observed("LIMITED", 100.0, "host")
    now_reading = sig.observed("NORMAL", 105.0, "host")
    assert sig.hold(prev, now_reading, now=105.0, max_age=10.0) is now_reading


def test_hold_without_previous_returns_current():
    gap = sig.unknown("host", "first read failed")
    assert sig.hold(None, gap, now=0.0, max_age=10.0) is gap


def test_edge_fires_on_change_and_first_reading_only():
    assert sig.edge(None, "OK")
    assert not sig.edge("OK", "OK")
    assert sig.edge("OK", "LIMITED")


def test_rate_limit_spaces_emissions_with_injected_clock():
    assert sig.rate_limit(None, now=0.0, min_interval=5.0)
    assert not sig.rate_limit(0.0, now=4.9, min_interval=5.0)
    assert sig.rate_limit(0.0, now=5.0, min_interval=5.0)


def test_hold_with_previous_unknown_or_stale_returns_current():
    gap = sig.unknown("host", "read failed")
    prev_unknown = sig.unknown("host", "earlier failure")
    assert sig.hold(prev_unknown, gap, now=5.0, max_age=10.0) is gap
    prev_stale = sig.expire(sig.observed("LIMITED", 0.0, "host"), now=50.0, max_age=10.0)
    assert prev_stale.state == sig.STALE
    assert sig.hold(prev_stale, gap, now=50.0, max_age=10.0) is gap


def test_join_unknown_with_timestamp_keeps_time_but_stays_unknown():
    j = sig.join([sig.observed(1, 10.0, "a"), sig.unknown("b", "missing", observed_at=4.0)])
    assert j.state == sig.UNKNOWN
    assert j.observed_at == 4.0
    assert "missing" in j.value


def test_edge_none_is_the_no_previous_sentinel():
    # None as previous means "no reading yet", so the first reading is an edge
    # even when that reading is None. Pinned so the rule is not silently changed.
    assert sig.edge(None, None) is True
    assert sig.edge(None, "OK") is True


def test_rate_limit_clock_stepped_back_emits_and_does_not_stall():
    # now < last_emit: the interval cannot be trusted, so emit rather than
    # suppress for the length of the backward step.
    assert sig.rate_limit(100.0, now=40.0, min_interval=5.0) is True
    # Once the caller records the new time, normal spacing resumes.
    assert sig.rate_limit(40.0, now=41.0, min_interval=5.0) is False
