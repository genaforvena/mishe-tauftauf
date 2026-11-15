"""Reasoning deltas are not wake progress (health wake 1794674035779401832, cut at the 3600 s hard cap).

A model that streams only ``thinking_delta`` events past the soft deadline must be cut at the
idle gap, not carried to the hard cap. Tool and message events remain progress.
"""
import os
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import engine_boundary as boundary
from mishe_tauftauf import engine_process as proc
from mishe_tauftauf.wake import EngineTimeout

THINKING = '{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta","contentIndex":0,"delta":"hmm"}}'
TOOL = '{"type":"tool_execution_start","toolName":"bash"}'

THINKS_FOREVER = (
    "import time\n"
    f"for i in range(300):\n    print({THINKING!r}, flush=True)\n    time.sleep(0.2)\n"
)
TOOLS_WHILE_RUNNING = (
    "import time\n"
    f"for i in range(40):\n    print({TOOL!r}, flush=True)\n    time.sleep(0.2)\n"
)


def test_thinking_deltas_count_as_no_progress():
    line = THINKING.encode() + b"\n"
    assert boundary.progress_bytes(line * 5) == (0, len(line) * 5)


def test_thinking_block_markers_count_as_no_progress():
    start = b'{"type":"message_update","assistantMessageEvent":{"type":"thinking_start","contentIndex":0}}\n'
    end = b'{"type":"message_update","assistantMessageEvent":{"type":"thinking_end","contentIndex":0}}\n'
    assert boundary.progress_bytes(start + end) == (0, len(start) + len(end))


def test_tool_and_text_events_still_count_as_progress():
    tool = TOOL.encode() + b"\n"
    text = b'{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"hi"}}\n'
    assert boundary.progress_bytes(tool)[0] == len(tool)
    assert boundary.progress_bytes(text)[0] == len(text)


def test_a_thinking_only_engine_is_cut_at_the_soft_deadline(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", THINKS_FOREVER], env=dict(os.environ), cwd=tmp_path,
                         timeout=1, stdout_to=out, hard_timeout=30, idle_gap=1.0)
    assert "hard cap" not in str(caught.value)
    assert time.monotonic() - started < 10, "reasoning deltas kept the engine running"


def test_a_tool_driven_engine_outlives_the_soft_deadline(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    code = proc.run_bounded([sys.executable, "-c", TOOLS_WHILE_RUNNING], env=dict(os.environ), cwd=tmp_path,
                            timeout=1, stdout_to=out, hard_timeout=20, idle_gap=1.0)
    assert code == 0
    assert time.monotonic() - started >= 1.0, "tool progress was cut at the soft deadline"
