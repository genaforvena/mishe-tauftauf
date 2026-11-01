from mishe_tauftauf import live_signal as ls


def test_observe_fresh_value_is_ok_with_time_and_source():
    s = ls.observe(3, at=100.0, source="psi")
    assert (s.value, s.observed_at, s.source, s.state) == (3, 100.0, "psi", ls.OK)


def test_missing_or_non_finite_input_is_never_ok():
    assert ls.observe(None, at=1.0, source="x").state == ls.UNKNOWN
    assert ls.observe(float("nan"), at=1.0, source="x").state == ls.UNKNOWN
    assert ls.observe(float("inf"), at=1.0, source="x").state == ls.UNKNOWN
    assert ls.fail("x", at=None, reason="gone").state == ls.UNKNOWN


def test_non_ok_state_drops_value():
    s = ls.Signal(42, 1.0, "x", ls.STALE)
    assert s.value is None


def test_hold_until_expiry_keeps_then_expires():
    s = ls.observe(7, at=100.0, source="x")
    assert ls.hold_until_expiry(s, now=119.0, max_age=20).state == ls.OK
    expired = ls.hold_until_expiry(s, now=121.0, max_age=20)
    assert expired.state == ls.STALE and expired.value is None
    assert expired.observed_at == 100.0  # keeps when it was seen, for the reader


def test_future_observation_is_unknown_not_ok():
    s = ls.observe(7, at=100.0, source="x")
    assert ls.hold_until_expiry(s, now=90.0, max_age=20).state == ls.UNKNOWN


def test_hold_passes_non_ok_through():
    s = ls.fail("x", at=5.0, reason="bad")
    assert ls.hold_until_expiry(s, now=6.0, max_age=20) is s


def test_map_transforms_ok_and_propagates_states():
    s = ls.observe(4, at=1.0, source="x")
    assert ls.map_(s, lambda v: v * 2).value == 8
    stale = ls.Signal(None, 1.0, "x", ls.STALE)
    assert ls.map_(stale, lambda v: v * 2).state == ls.STALE


def test_map_exception_becomes_unknown():
    s = ls.observe(0, at=1.0, source="x")
    assert ls.map_(s, lambda v: 1 / v).state == ls.UNKNOWN


def test_join_worst_state_wins_unknown_over_stale_over_ok():
    ok = ls.observe(1, at=10.0, source="a")
    stale = ls.Signal(None, 9.0, "b", ls.STALE, "old")
    unk = ls.fail("c", at=8.0, reason="missing")
    assert ls.join([ok, ok], lambda a, b: a + b, source="j").value == 2
    assert ls.join([ok, stale], lambda a, b: a + b, source="j").state == ls.STALE
    assert ls.join([ok, stale, unk], lambda a, b, c: 0, source="j").state == ls.UNKNOWN


def test_join_observed_at_is_the_oldest_input():
    a = ls.observe(1, at=10.0, source="a")
    b = ls.observe(2, at=4.0, source="b")
    assert ls.join([a, b], lambda x, y: x + y, source="j").observed_at == 4.0


def test_join_without_inputs_or_time_is_unknown():
    assert ls.join([], lambda: 0, source="j").state == ls.UNKNOWN
    untimed = ls.Signal(1, None, "a", ls.OK)
    assert ls.join([untimed], lambda x: x, source="j").state == ls.UNKNOWN


def test_join_fn_failure_is_unknown():
    a = ls.observe(1, at=1.0, source="a")
    assert ls.join([a], lambda x: x / 0, source="j").state == ls.UNKNOWN


def test_changed_is_an_edge_on_fresh_values_only():
    assert ls.changed(1, ls.observe(2, at=1.0, source="x"))
    assert not ls.changed(2, ls.observe(2, at=1.0, source="x"))
    assert not ls.changed(1, ls.fail("x", at=1.0, reason="r"))


def test_rate_limit_deterministic_clock():
    assert ls.rate_limit(None, now=5.0, min_interval=10.0)
    assert not ls.rate_limit(0.0, now=9.9, min_interval=10.0)
    assert ls.rate_limit(0.0, now=10.0, min_interval=10.0)
