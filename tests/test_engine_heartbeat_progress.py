"""Wait-tool heartbeats are not progress (docs wake 2026-11-13, cut at the 3600 s hard cap).

A mind stuck in a wait on a subagent that never returns keeps writing ``tool_execution_update``
heartbeat lines while a live child is alive. Those lines must not extend the run past the soft
deadline, and a silent child-wait is cut once the bounded grace after the soft deadline ends.
"""
import os
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import engine_process as proc
from mishe_tauftauf.wake import EngineTimeout

HEARTBEAT = '{"type":"tool_execution_update","toolName":"wait"}'
HEARTBEATS_FOREVER = (
    "import time\n"
    f"for i in range(300):\n    print({HEARTBEAT!r}, flush=True)\n    time.sleep(0.2)\n"
)
HEARTBEATS_WHILE_CHILD_WAITS = (
    "import subprocess, sys, time\n"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    f"for i in range(300):\n    print({HEARTBEAT!r}, flush=True)\n    time.sleep(0.2)\n"
)


def test_the_reader_counts_real_output_and_not_heartbeats(tmp_path: Path):
    out = tmp_path / "stream"
    reader = proc._ProgressReader(out)
    assert reader.poll() is None  # file not created yet: no output to watch
    out.write_bytes((HEARTBEAT + "\n").encode() * 3)
    assert reader.poll() == 0
    out.write_bytes((HEARTBEAT + "\n").encode() * 3 + b"real output\n")
    assert reader.poll() == len(b"real output\n")


def test_the_reader_waits_for_a_complete_line(tmp_path: Path):
    out = tmp_path / "stream"
    reader = proc._ProgressReader(out)
    out.write_bytes(b"partial")
    assert reader.poll() == 0
    out.write_bytes(b"partial line\n")
    assert reader.poll() == len(b"partial line\n")


def test_heartbeats_alone_do_not_hold_an_engine_past_the_soft_deadline(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", HEARTBEATS_FOREVER], env=dict(os.environ), cwd=tmp_path,
                         timeout=1, stdout_to=out, hard_timeout=30, idle_gap=1.0)
    assert "hard cap" not in str(caught.value)
    assert time.monotonic() - started < 10, "heartbeats kept the engine running"


def test_a_silent_child_wait_is_cut_after_the_bounded_grace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(proc, "TOOL_WAIT_GRACE_S", 2.0)
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", HEARTBEATS_WHILE_CHILD_WAITS], env=dict(os.environ),
                         cwd=tmp_path, timeout=1, stdout_to=out, hard_timeout=60, idle_gap=1.0)
    elapsed = time.monotonic() - started
    assert "hard cap" not in str(caught.value), "the stuck wait ran to the hard cap"
    assert 2.5 <= elapsed < 12, f"the stuck wait was cut {elapsed:.1f}s in, not at soft + grace"
