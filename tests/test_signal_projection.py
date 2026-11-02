"""The live dashboard projection: publish on change, at most once per interval.

Pure: the caller supplies the reading, the clock and the renderer. These tests use
an injected clock so every boundary is exact.
"""
from mishe_tauftauf import live_signal as ls
from mishe_tauftauf import signal_projection as sp


def render(value):
    return f"VALUE {value}"


def test_first_reading_publishes_and_is_remembered():
    decision, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), sp.Projection(), now=100.0, render=render)
    assert decision.publish and decision.reason == "first publication"
    assert decision.frame == "VALUE 1"
    assert projection.last_value == 1 and projection.published_once
    assert projection.last_emitted_at == 100.0


def test_unchanged_reading_does_not_republish_inside_the_interval():
    first, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), sp.Projection(), now=100.0, render=render)
    assert first.publish
    decision, projection = sp.decide_publish(
        ls.observe(1, at=105.0, source="x"), projection, now=105.0, render=render,
        min_interval=30.0)
    assert not decision.publish
    assert decision.reason == "unchanged and inside the minimum interval"
    assert projection is not None and projection.last_value == 1


def test_changed_reading_publishes_immediately_inside_the_interval():
    first, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), sp.Projection(), now=100.0, render=render)
    decision, projection = sp.decide_publish(
        ls.observe(2, at=101.0, source="x"), projection, now=101.0, render=render,
        min_interval=30.0)
    assert decision.publish and decision.reason == "reading changed"
    assert decision.frame == "VALUE 2"
    assert projection.last_value == 2


def test_unchanged_reading_refreshes_once_the_interval_has_passed():
    first, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), sp.Projection(), now=100.0, render=render)
    decision, projection = sp.decide_publish(
        ls.observe(1, at=130.0, source="x"), projection, now=130.0, render=render,
        min_interval=30.0)
    assert decision.publish and decision.reason == "periodic refresh"
    assert decision.frame == "VALUE 1"
    assert projection.last_emitted_at == 130.0


def test_a_missing_reading_is_never_published_as_a_value():
    decision, projection = sp.decide_publish(
        ls.fail("x", at=None, reason="gone"), sp.Projection(), now=100.0, render=render)
    assert not decision.publish
    assert "UNKNOWN" in decision.reason
    assert decision.frame is None
    assert not projection.published_once


def test_a_stale_reading_is_not_published_even_though_it_was_observed():
    reading = ls.observe(1, at=100.0, source="x")
    decision, projection = sp.decide_publish(
        reading, sp.Projection(), now=200.0, render=render, max_age=60.0)
    assert not decision.publish
    assert "STALE" in decision.reason
    assert not projection.published_once


def test_a_future_reading_is_unknown_and_not_published():
    reading = ls.observe(1, at=100.0, source="x")
    decision, projection = sp.decide_publish(
        reading, sp.Projection(), now=90.0, render=render)
    assert not decision.publish
    assert "UNKNOWN" in decision.reason


def test_a_failing_renderer_does_not_advance_the_projection():
    def boom(value):
        raise ValueError("render blew up")

    decision, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), sp.Projection(), now=100.0, render=boom)
    assert not decision.publish
    assert "render failed" in decision.reason
    assert not projection.published_once


def test_an_empty_frame_is_refused():
    decision, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), sp.Projection(), now=100.0, render=lambda v: "")
    assert not decision.publish
    assert "no frame" in decision.reason


def test_the_renderer_is_not_called_for_a_reading_that_will_not_publish():
    calls = []

    def counting(value):
        calls.append(value)
        return f"VALUE {value}"

    first, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), sp.Projection(), now=100.0, render=counting)
    assert calls == [1]
    sp.decide_publish(ls.observe(1, at=101.0, source="x"), projection, now=101.0,
                      render=counting, min_interval=30.0)
    assert calls == [1]


def test_a_stale_reading_after_a_published_one_does_not_erase_the_last_frame():
    first, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), sp.Projection(), now=100.0, render=render)
    decision, projection = sp.decide_publish(
        ls.observe(1, at=100.0, source="x"), projection, now=200.0, render=render,
        max_age=60.0)
    assert not decision.publish
    assert projection.last_frame == "VALUE 1"
    assert projection.published_once
