"""wake-wait-stall: a hard-cap wake cut with a still-running subagent wait goes RED; quiet windows stay GREEN."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "wake_wait_stall", Path(__file__).parent.parent / "checks" / "wake-wait-stall.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

NOW = 10_000_000.0
WAKE = "wake-senses-1795103362156906856"
MODEL = "minimax-code/MiniMax-M3.1-Flash:minimal"


def ev(at=NOW - 60, wake=WAKE, role="senses"):
    return {"event": "outcome", "reason": "exceeded 2100s (hard cap)", "role": role,
            "wake": wake, "at": at}


def job(status="running", duration=2023892):
    return {"id": "MapBudgetProducer", "type": "task", "status": status,
            "label": "MapBudgetProducer", "durationMs": duration, "resolvedModel": MODEL}


def update(cid, jobs):
    return {"type": "tool_execution_update", "toolCallId": cid, "toolName": "wait",
            "args": {}, "partialResult": {"details": {"op": "wait", "jobs": jobs}}}


def end(cid, text="Operation aborted", error=True):
    return {"type": "tool_execution_end", "toolCallId": cid, "toolName": "wait",
            "result": {"content": [{"type": "text", "text": text}]}, "isError": error}


def stream(tmp_path, events, name=WAKE):
    folder = tmp_path / "wakes"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.prompt.out.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n")
    return folder


def test_empty_window_is_green(tmp_path):
    assert mod.evaluate([], tmp_path, NOW)[0] == 0


def test_hard_cap_without_a_stream_is_unknown(tmp_path):
    code, msg = mod.evaluate([ev()], tmp_path, NOW)
    assert code == 3
    assert "UNKNOWN" in msg and WAKE in msg


@pytest.mark.parametrize("data", ["", '{"type":'])
def test_hard_cap_without_readable_stream_events_is_unknown(tmp_path, data):
    folder = stream(tmp_path, [])
    (folder / f"{WAKE}.prompt.out.jsonl").write_text(data)
    code, msg = mod.evaluate([ev()], tmp_path, NOW)
    assert code == 3
    assert "UNKNOWN" in msg and WAKE in msg


def test_confirmed_wait_stays_red_when_another_hard_cap_has_no_stream(tmp_path):
    missing = "wake-health-missing"
    stream(tmp_path, [update("wait-1", [job()])])
    code, msg = mod.evaluate([ev(wake=missing, role="health"), ev()], tmp_path, NOW)
    assert code == 2
    assert WAKE in msg and missing in msg


def test_hard_cap_without_a_wait_is_green(tmp_path):
    stream(tmp_path, [{"type": "turn_start"}])
    assert mod.evaluate([ev()], tmp_path, NOW)[0] == 0


def test_aborted_wait_with_running_job_is_red_and_names_the_job(tmp_path):
    stream(tmp_path, [update("wait-1", [job()]), end("wait-1")])
    code, msg = mod.evaluate([ev()], tmp_path, NOW)
    assert code == 2
    assert "senses" in msg and WAKE in msg
    assert "MapBudgetProducer" in msg and MODEL in msg
    assert "hard cap" in msg and "still running" in msg


def test_wait_still_in_progress_at_the_cap_is_red(tmp_path):
    stream(tmp_path, [update("wait-1", [job()])])
    assert mod.evaluate([ev()], tmp_path, NOW)[0] == 2


def test_normally_completed_wait_is_green(tmp_path):
    stream(tmp_path, [update("wait-1", [job(status="completed")]),
                      end("wait-1", text="jobs completed", error=False)])
    assert mod.evaluate([ev()], tmp_path, NOW)[0] == 0


def test_last_completed_wait_masks_an_earlier_running_wait(tmp_path):
    stream(tmp_path, [update("wait-1", [job()]), end("wait-1", "jobs completed", False),
                      update("wait-2", [job(status="completed")]),
                      end("wait-2", "jobs completed", False)])
    assert mod.evaluate([ev()], tmp_path, NOW)[0] == 0


def test_stale_hard_cap_is_ignored(tmp_path):
    stream(tmp_path, [update("wait-1", [job()])])
    assert mod.evaluate([ev(at=NOW - mod.WINDOW_S - 1)], tmp_path, NOW)[0] == 0


def test_tail_bound_ignores_a_wait_outside_the_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "TAIL_BYTES", 200)
    stream(tmp_path, [update("old", [job()]),
                      {"type": "message_update", "text": "x" * 400},
                      update("last", [job(status="completed")]),
                      end("last", "jobs completed", False)])
    assert mod.evaluate([ev()], tmp_path, NOW)[0] == 0


def test_unreadable_ledger_is_unknown(tmp_path):
    assert mod.main(["--home", str(tmp_path)]) == 3
