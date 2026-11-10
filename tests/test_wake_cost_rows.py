"""Tests for the per-wake cost rows tool: decoding joins, measurement honesty."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from wake_cost_rows import wake_rows  # noqa: E402

TS0 = 1_800_000_000.0


def _stream(wake_dir, wake_key, events):
    path = wake_dir / f"wake-{wake_key}.prompt.out.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def _assistant(provider, model, ts_s, usage, cost):
    # the message_end shape the decoder reads: role assistant, usage block with
    # a numeric cost total; ts is seconds, matching decode_line output.
    return {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
            "provider": provider,
            "model": model,
            "usage": {
                "input": usage[0],
                "output": usage[1],
                "cacheRead": usage[2],
                "cacheWrite": usage[3],
                "totalTokens": sum(usage),
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": cost},
            },
            "responseId": f"r-{int(ts_s * 1000)}",
            "stopReason": "stop",
            "timestamp": int(ts_s * 1000),
        },
    }


def _yield(wake_dir, wake_key, role, result):
    path = wake_dir / f"wake-{wake_key}.yield.json"
    path.write_text(json.dumps({"key": wake_key, "role": role, "result": result, "settled_at": "2026-11-10T21:00:00Z"}))
    return path


@pytest.fixture
def site(tmp_path):
    d = tmp_path / "wakes"
    d.mkdir()
    return d


def test_one_wake_one_model_costs_its_turns_and_joins_the_yield(site):
    _stream(site, "k1", [_assistant("routerai", "inclusionai/ling-3.1-flash", TS0, (100000, 2000, 3000000, 0), 0.1234)])
    _yield(site, "k1", "health", "changed")
    rows = wake_rows(site, since=0.0)
    assert len(rows) == 1
    r = rows[0]
    assert r["wake"] == "k1" and r["role"] == "health" and r["result"] == "changed"
    assert r["model"] == "inclusionai/ling-3.1-flash"
    assert r["turns"] == 1
    assert r["input"] == 100000 and r["output"] == 2000 and r["cache_read"] == 3000000 and r["cache_write"] == 0
    assert r["cost"] == 0.1234 and r["cost_currency"] == "RUB"
    assert r["measured"] is True
    assert r["unpriced_turns"] == 0
    assert abs(r["cache_share"] - 3000000 / 3102000) < 1e-9


def test_a_fall_through_wake_keeps_one_row_per_model(site):
    # ling tried first, claude-engine haiku took the wake: both rows survive
    _stream(
        site,
        "k2",
        [
            _assistant("routerai", "inclusionai/ling-3.1-flash", TS0, (1000, 100, 2000, 0), 0.01),
            _assistant("claude", "claude-haiku-5-5", TS0 + 30.0, (5000, 500, 1000, 0), 0.5),
            _assistant("routerai", "inclusionai/ling-3.1-flash", TS0 + 60.0, (2000, 200, 4000, 0), 0.02),
        ],
    )
    rows = [r for r in wake_rows(site, since=0.0) if r["wake"] == "k2"]
    assert len(rows) == 2
    ling, haiku = rows
    assert ling["provider"] == "routerai" and ling["turns"] == 2 and ling["cost"] == 0.03
    assert haiku["provider"] == "claude" and haiku["cost_currency"] == "USD-sub" and haiku["cost"] == 0.5
    assert ling["result"] is None  # no yield record: the wake never settled
    assert ling["wake_seconds"] == 60.0  # seconds span the routerai lane only
    assert ling["measured"] is None  # shared stream: per-model honesty flags are n/a


def test_a_wake_with_an_unpriced_turn_is_flagged_not_whole(site):
    # the boundary decoder raises Uncountable when the cost total is null:
    # the turn is skipped, the wake is counted and marked unmeasured
    good = _assistant("routerai", "z-ai/glm-5.3-flash", TS0, (1000, 100, 2000, 0), 0.05)
    bad = _assistant("routerai", "z-ai/glm-5.3-flash", TS0 + 5.0, (2000, 200, 4000, 0), None)
    _stream(site, "k3", [good, bad])
    rows = wake_rows(site, since=0.0)
    assert rows[0]["turns"] == 1
    assert rows[0]["cost"] == 0.05
    assert rows[0]["unpriced_turns"] == 1
    assert rows[0]["measured"] is False


def test_an_aborted_turn_before_the_provider_answered_costs_zero(site):
    # no responseId: the decoder counts it as zero usage, zero cost — countable, not uncountable
    event = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "provider": "routerai",
            "model": "inclusionai/ling-3.1-flash",
            "usage": {
                "input": 0,
                "output": 0,
                "cacheRead": 0,
                "cacheWrite": 0,
                "totalTokens": 0,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0.0},
            },
            "stopReason": "aborted",
            "timestamp": int(TS0 * 1000),
        },
    }
    _stream(site, "k4", [event])
    rows = wake_rows(site, since=0.0)
    assert rows[0]["turns"] == 1
    assert rows[0]["cost"] == 0.0
    assert rows[0]["measured"] is True  # the provider reported a cost total: zero


def test_only_wakes_inside_the_window_are_returned(site, monkeypatch):
    import os
    import time

    now = time.time()
    recent = _stream(site, "k-new", [_assistant("routerai", "inclusionai/ling-3.1-flash", now, (1, 1, 1, 0), 1.0)])
    old = _stream(site, "k-old", [_assistant("routerai", "inclusionai/ling-3.1-flash", now - 7200.0, (1, 1, 1, 0), 1.0)])
    os.utime(old, (now - 7200, now - 7200))
    os.utime(recent, (now, now))
    rows = wake_rows(site, since=now - 3600.0)
    assert [r["wake"] for r in rows] == ["k-new"]
