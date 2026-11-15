"""wake-waste: discarded wakes go RED, rolled up per candidate; a quiet window is GREEN, an unreadable ledger UNKNOWN."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("wake_waste", Path(__file__).parent.parent / "checks" / "wake-waste.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

NOW = 10_000_000.0
HARD = "omp:minimax-code/MiniMax-M3.1-Flash:minimax-a"
DEAD = "omp:nvidia/nvidia/nemotron-3-ultra-550b-a55b:-"


def ev(key=HARD, role="pub", outcome="timeout", reason="exceeded 2100s (hard cap)", at=NOW - 60, wake="w1"):
    return {"event": "outcome", "outcome": outcome, "reason": reason, "role": role, "key": key, "wake": wake, "at": at}


def test_empty_window_is_green(tmp_path):
    assert mod.evaluate([], tmp_path, NOW)[0] == 0


def test_yielded_wake_is_ignored(tmp_path):
    assert mod.evaluate([ev(outcome="yielded", reason="")], tmp_path, NOW)[0] == 0


def test_old_loss_is_ignored(tmp_path):
    assert mod.evaluate([ev(at=NOW - mod.WINDOW_S - 1)], tmp_path, NOW)[0] == 0


def test_hard_cap_timeout_is_red_and_names_candidate(tmp_path):
    code, msg = mod.evaluate([ev()], tmp_path, NOW)
    assert code == 2 and HARD in msg and "hard cap" in msg and "1 wake(s) lost" in msg


def test_losses_are_rolled_up_per_candidate_and_ranked(tmp_path):
    events = [ev(), ev(wake="w2"), ev(key=DEAD, role="docs", reason="exceeded 1800s (no output for 180s)")]
    code, msg = mod.evaluate(events, tmp_path, NOW)
    assert code == 2
    assert "2 wake(s) lost" in msg and msg.index(HARD) < msg.index(DEAD)
    assert "no output" in msg


def test_large_stream_without_yield_counts_even_without_timeout(tmp_path):
    (tmp_path / "wakes").mkdir()
    (tmp_path / "wakes" / "big.prompt.out.jsonl").write_bytes(b"x" * (mod.MIN_STREAM_BYTES + 1))
    code, msg = mod.evaluate([ev(outcome="no yield (exit 0)", reason="", wake="big")], tmp_path, NOW)
    assert code == 2 and "no yield" in msg


def test_small_stream_without_yield_is_ignored(tmp_path):
    assert mod.evaluate([ev(outcome="no yield (exit 0)", reason="", wake="tiny")], tmp_path, NOW)[0] == 0


def test_unreadable_ledger_is_unknown(tmp_path):
    assert mod.main(["--home", str(tmp_path)]) == 3
