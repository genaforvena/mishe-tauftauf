"""The Claude CLI's refusal document: what it says, and whether anything ran.

The fixture is the recorded refusal of 2026-11-17: a first-party weekly limit spent,
which ended every wake that picked the safety net without falling through.
"""
import json

import pytest

from mishe_tauftauf import claude_error_text as text

REFUSED = {
    "duration_api_ms": 0,
    "stop_reason": "stop_sequence",
    "total_cost_usd": 0,
    "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0},
    "modelUsage": {},
    "terminal_reason": "api_error",
    "is_error": True,
    "num_turns": 1,
    "subtype": "success",
    "api_error_status": 429,
    "api_error_origin": "first_party",
    "api_error": "usage_limit_reached",
    "result": "You've hit your weekly limit · resets Nov 18, 3am (UTC)",
    "type": "result",
    "duration_ms": 898,
}

ANSWERED = {
    "duration_api_ms": 12148,
    "is_error": False,
    "num_turns": 3,
    "usage": {"input_tokens": 6, "output_tokens": 2070, "cache_read_input_tokens": 66213,
              "cache_creation_input_tokens": 17622},
    "result": "done",
    "type": "result",
}


def write(tmp_path, document, name="prompt.out.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_spent_weekly_limit_is_a_quota_error(tmp_path):
    document = text.load(write(tmp_path, REFUSED))
    refusal = text.refusal(document)
    assert text.is_quota_error(refusal)
    assert not text.is_rate_limit_error(refusal)
    assert "usage_limit_reached" in refusal


def test_a_refusal_that_ran_nothing_is_not_an_effect(tmp_path):
    document = text.load(write(tmp_path, REFUSED))
    assert text.inferred(document) is False
    assert text.refusal(document) is not None


def test_an_answered_run_is_an_effect_even_when_it_carries_a_refusal(tmp_path):
    answered = dict(ANSWERED, is_error=True, api_error="usage_limit_reached",
                    result="You've hit your weekly limit")
    assert text.inferred(text.load(write(tmp_path, answered))) is True


def test_a_successful_run_is_not_a_refusal(tmp_path):
    document = text.load(write(tmp_path, ANSWERED))
    assert text.refusal(document) is None
    assert text.inferred(document) is True


def test_the_stated_reset_is_read_as_utc(tmp_path):
    # 2026-11-17T00:00:00Z, and the provider named "Nov 18, 3am (UTC)".
    when = text.reset_at(text.load(write(tmp_path, REFUSED)), now=1794873600.0)
    assert when == 1794970800.0   # 2026-11-18T03:00:00Z


def test_a_reset_already_past_moves_to_the_next_year(tmp_path):
    when = text.reset_at(text.load(write(tmp_path, REFUSED)), now=1794960000.0)
    assert when is not None and when > 1794960000.0


def test_a_reset_in_the_past_without_a_stamp_is_unknown(tmp_path):
    assert text.reset_at(text.load(write(tmp_path, ANSWERED)), now=1794873600.0) is None


def test_a_malformed_document_yields_no_refusal(tmp_path):
    path = tmp_path / "prompt.out.json"
    path.write_text("{not json", encoding="utf-8")
    assert text.load(path) is None
    assert text.refusal(text.load(path)) is None
    assert text.load(tmp_path / "missing.out.json") is None


def test_a_rate_limit_is_not_a_quota_exhaustion():
    assert text.is_rate_limit_error("rate_limit_error: too many requests")
    assert not text.is_quota_error("rate_limit_error: too many requests")
    assert not text.is_quota_error(None)
    assert not text.is_rate_limit_error(None)
