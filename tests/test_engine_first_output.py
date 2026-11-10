"""A wake that never produces the model's first event is cut at the first-output bound.

The idle watchdog only starts after the soft deadline, so a model that emits nothing used to
wait the whole soft timeout (~1800 s). The first-output bound cuts it at ~120 s, and a stalled
provider that writes a few bytes and then goes quiet is caught by the event predicate rather
than by byte growth (operator DM 299288).
"""
import os
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import engine_omp, engine_process as proc
from mishe_tauftauf.wake import EngineTimeout

SILENT = "import time\ntime.sleep(60)\n"
ONE_WRITE = "import time,sys\nprint('x'*100, flush=True)\ntime.sleep(60)\n"


def test_a_silent_engine_is_cut_at_the_first_output_bound(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", SILENT], env=dict(os.environ), cwd=tmp_path,
                         timeout=30, stdout_to=out, hard_timeout=60, idle_gap=5.0,
                         first_output_s=1.0, first_event=lambda: False)
    assert "no first output within 1s" in str(caught.value)
    assert time.monotonic() - started < 10, "the silent engine was cut at the soft deadline, not the bound"


def test_a_first_event_before_the_bound_is_enough(tmp_path: Path):
    out = tmp_path / "stream"
    code = proc.run_bounded([sys.executable, "-c", "import time; time.sleep(0.2)"],
                            env=dict(os.environ), cwd=tmp_path, timeout=30, stdout_to=out,
                            hard_timeout=60, idle_gap=5.0, first_output_s=1.0,
                            first_event=lambda: True)
    assert code == 0


def test_byte_growth_alone_does_not_satisfy_the_event_predicate(tmp_path: Path):
    """The xhigh stall: a few bytes, then nothing. Growth is not an assistant event."""
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", ONE_WRITE], env=dict(os.environ), cwd=tmp_path,
                         timeout=30, stdout_to=out, hard_timeout=60, idle_gap=5.0,
                         first_output_s=1.0, first_event=lambda: False)
    assert "no first output within 1s" in str(caught.value)
    assert time.monotonic() - started < 10


def test_without_a_predicate_any_growth_satisfies_the_bound(tmp_path: Path):
    """The bound is a fallback: without an event predicate, output growth counts."""
    out = tmp_path / "stream"
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", ONE_WRITE], env=dict(os.environ), cwd=tmp_path,
                         timeout=5, stdout_to=out, hard_timeout=60, idle_gap=1.0,
                         first_output_s=1.0)
    assert "no first output" not in str(caught.value), "growth was not accepted as the first output"
    assert "no output" in str(caught.value)


def test_the_event_predicate_is_checked_once(tmp_path: Path):
    calls: list = []

    def event() -> bool:
        calls.append(1)
        return False

    with pytest.raises(EngineTimeout):
        proc.run_bounded([sys.executable, "-c", SILENT], env=dict(os.environ), cwd=tmp_path,
                         timeout=30, stdout_to=tmp_path / "stream", hard_timeout=60, idle_gap=5.0,
                         first_output_s=1.0, first_event=event)
    assert len(calls) == 1, "the predicate was consulted more than once"


def test_the_real_stall_signature_is_cut_at_the_bound(tmp_path: Path):
    """A run that writes omp's optimistic turn and then goes quiet is cut at the bound.

    This is the shape of the two stalled wake streams on record (2026-11-08, 2026-11-09):
    ``message_start``/``message_end``/``turn_end`` for the assistant with empty content and
    nothing after it.
    """
    stream = tmp_path / "stream.jsonl"
    stream.write_text('{"type":"session"}\n'
                      '{"type":"message_start","message":{"role":"user"}}\n'
                      '{"type":"message_end","message":{"role":"user"}}\n'
                      '{"type":"message_start","message":{"role":"assistant","content":[]}}\n'
                      '{"type":"message_end","message":{"role":"assistant","content":[]}}\n',
                      encoding="utf-8")
    assert engine_omp.saw_provider_output(stream) is False


def test_the_optimistic_assistant_start_is_not_a_provider_event(tmp_path: Path):
    """``omp`` opens the assistant turn before the provider answers; that is not output.

    A stalled run writes exactly this stream and then goes quiet, so treating the optimistic
    ``message_start`` as the model's first response would make the bound a no-op exactly where
    it is needed.
    """
    stream = tmp_path / "stream.jsonl"
    stream.write_text('{"type":"message_start","message":{"role":"user"}}\n'
                      '{"type":"message_end","message":{"role":"user"}}\n'
                      '{"type":"message_start","message":{"role":"assistant","content":[]}}\n',
                      encoding="utf-8")
    assert engine_omp.saw_provider_output(stream) is False


def test_a_provider_event_marks_the_models_first_response(tmp_path: Path):
    stream = tmp_path / "stream.jsonl"
    stream.write_text('{"type":"message_start","message":{"role":"user"}}\n'
                      '{"type":"message_start","message":{"role":"assistant","content":[]}}\n',
                      encoding="utf-8")
    assert engine_omp.saw_provider_output(stream) is False
    stream.write_text('{"type":"message_update","assistantMessageEvent":{"type":"text_start"}}\n',
                      encoding="utf-8")
    assert engine_omp.saw_provider_output(stream) is True


def test_a_thinking_or_tool_call_event_also_counts_as_output(tmp_path: Path):
    for kind in ("thinking_start", "toolcall_start"):
        stream = tmp_path / f"{kind}.jsonl"
        stream.write_text('{"type":"message_update","assistantMessageEvent":{"type":"%s"}}\n' % kind,
                          encoding="utf-8")
        assert engine_omp.saw_provider_output(stream) is True, kind
