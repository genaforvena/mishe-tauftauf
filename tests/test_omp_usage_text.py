"""The OMP wake usage boundary: what a turn's usage record says, and what it cannot say.

The budget collector folds these records into per-account token totals. A
record it cannot read makes the whole account UNKNOWN, so the difference
between "this turn spent nothing" and "this record is unreadable" decides
whether a budget verdict is real.
"""
import json

import pytest

from mishe_tauftauf.omp_usage_text import Uncountable, decode_line, usage_record


def _usage(**over):
    base = {"input": 10, "output": 20, "cacheRead": 30, "cacheWrite": 40,
            "cost": {"total": 0.5}}
    base.update(over)
    return base


def _event(**over):
    message = {"role": "assistant", "provider": "minimax-code", "model": "MiniMax-M3.1-Flash",
               "responseId": "resp-1", "stopReason": "stop", "completedAt": 1_700_000_000_000,
               "usage": _usage()}
    message.update(over)
    return {"type": "message_end", "message": message}


def test_a_normal_turn_decodes_its_turn_and_tokens():
    ts, name, key, values = usage_record(_event())
    assert ts == 1_700_000_000.0
    assert name == "minimax-code|MiniMax-M3.1-Flash"
    assert key == "resp-1"
    assert values == [1, 20, 10, 30, 40, 0.5]


def test_an_aborted_turn_without_a_response_id_counts_as_zero_not_uncountable():
    """The regression: an aborted turn spent nothing, so it is countable zero.

    Treating it as unreadable made every non-Claude account UNKNOWN in the
    budget view, because one aborted turn poisoned the whole account.
    """
    event = _event(stopReason="aborted", responseId=None, errorId=134221824,
                   usage=_usage(input=0, output=0, cacheRead=0, cacheWrite=0, cost={"total": 0}))
    ts, name, key, values = usage_record(event)
    assert values == [1, 0, 0, 0, 0, 0.0]
    assert name == "minimax-code|MiniMax-M3.1-Flash"
    assert "aborted:" in key
    assert ts == 1_700_000_000.0


def test_two_aborted_turns_with_one_error_id_share_a_key():
    """A re-read of the same aborted turn must replace, not double-count."""
    first = _event(stopReason="aborted", responseId=None, errorId=99,
                   usage=_usage(input=0, output=0, cacheRead=0, cacheWrite=0, cost={"total": 0}))
    second = dict(first, message=dict(first["message"], completedAt=1_700_000_005_000))
    assert usage_record(first)[2] == usage_record(second)[2]


def test_a_failed_call_with_zero_usage_is_not_a_usage_record():
    """The provider answered and then failed: no tokens spent, nothing to count."""
    event = _event(stopReason="error", responseId=None,
                   usage=_usage(input=0, output=0, cacheRead=0, cacheWrite=0, cost={"total": 0}))
    assert usage_record(event) is None


def test_a_failed_call_that_carries_usage_is_counted():
    event = _event(stopReason="error")
    ts, name, key, values = usage_record(event)
    assert values == [1, 20, 10, 30, 40, 0.5]
    assert key == "resp-1"


def test_records_that_are_not_assistant_usage_turns_are_skipped():
    assert usage_record({"type": "message_end", "message": {"role": "user"}}) is None
    assert usage_record({"type": "message_end", "message": {"role": "assistant"}}) is None
    assert usage_record({"type": "message_start", "message": {"role": "assistant"}}) is None
    assert usage_record({"type": "message_end"}) is None


def test_a_record_without_a_response_id_or_numeric_output_is_uncountable():
    event = _event(responseId=None)
    with pytest.raises(Uncountable):
        usage_record(event)
    event = _event(usage=_usage(output="many"))
    with pytest.raises(Uncountable):
        usage_record(event)


def test_a_record_without_a_completion_time_is_uncountable():
    event = _event(completedAt=None)
    with pytest.raises(Uncountable):
        usage_record(event)


def test_a_record_without_a_cost_total_is_uncountable():
    event = _event(usage=_usage(cost={"total": "free"}))
    with pytest.raises(Uncountable):
        usage_record(event)


def test_a_boolean_is_not_a_number():
    event = _event(usage=_usage(output=True))
    with pytest.raises(Uncountable):
        usage_record(event)


def test_decode_line_raises_on_a_line_that_is_not_json():
    # A usage-claiming line that does not parse is corrupt, not skippable.
    with pytest.raises(Uncountable):
        decode_line("not json at all")
    with pytest.raises(Uncountable):
        decode_line("")


def test_decode_line_reads_a_record_from_a_json_line():
    ts, name, key, values = decode_line(json.dumps(_event()))
    assert key == "resp-1"
    assert values == [1, 20, 10, 30, 40, 0.5]
    assert ts == 1_700_000_000.0
