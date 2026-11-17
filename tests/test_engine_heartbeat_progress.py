"""Wait-tool heartbeats are not progress (docs wake 2026-11-13, cut at the 3600 s hard cap).

A mind stuck in a wait on a subagent that never returns keeps writing ``tool_execution_update``
heartbeat lines while a live child is alive. Those lines must not extend the run past the soft
deadline, and a silent child-wait is cut once the bounded grace after the soft deadline ends.

The exception is a heartbeat whose async job advanced its own counters: that wait is on a
subagent that is working. Genome wake 2026-11-17 03:47Z was cut at 600 s of silence while its
review subagent was on its 74th tool call, and the review was discarded.
"""
import json
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

# An engine that first says something real, then only reports its async job. With advancing
# counters it is working and must survive; with frozen ones it must read as silence.
JOB_SCRIPT = (
    "import json, time\n"
    "print(json.dumps({{'type': 'tool_execution_start', 'toolName': 'task'}}, separators=(',', ':')))\n"
    "for i in range({rounds}):\n"
    "    count = {step}\n"
    "    row = {{'type': 'tool_execution_update', 'toolName': 'task', 'partialResult':\n"
    "           {{'details': {{'progress': [{{'id': 'DutyReview', 'status': 'running',\n"
    "            'toolCount': count, 'requests': count, 'tokens': count * 1000}}]}}}}}}\n"
    "    print(json.dumps(row, separators=(',', ':')), flush=True)\n"
    "    time.sleep(0.2)\n"
)
ADVANCING_JOB = JOB_SCRIPT.format(rounds=15, step="i + 1")
FROZEN_JOB = JOB_SCRIPT.format(rounds=300, step="1")


def job_heartbeat(tool_count: int, *, job: str = "DutyReview") -> str:
    row = {"type": "tool_execution_update", "toolName": "task", "partialResult": {"details": {
        "progress": [{"id": job, "status": "running", "toolCount": tool_count,
                      "requests": tool_count, "tokens": tool_count * 1000}]}}}
    return json.dumps(row, separators=(",", ":")) + "\n"


def wait_heartbeat(duration_ms: int) -> str:
    row = {"type": "tool_execution_update", "toolName": "wait", "partialResult": {"details": {
        "op": "wait", "jobs": [{"id": "DutyReview", "status": "running",
                                "durationMs": duration_ms}]}}}
    return json.dumps(row, separators=(",", ":")) + "\n"


def test_the_reader_counts_real_output_and_not_heartbeats(tmp_path: Path):
    out = tmp_path / "stream"
    reader = proc._ProgressReader(out)
    assert reader.poll() is None  # file not created yet: no output to watch
    out.write_bytes((HEARTBEAT + "\n").encode() * 3)
    assert reader.poll() == 0
    out.write_bytes((HEARTBEAT + "\n").encode() * 3 + b"real output\n")
    assert reader.poll() == len(b"real output\n")


def test_a_repeated_job_heartbeat_is_silence_but_an_advancing_one_is_progress(tmp_path: Path):
    out = tmp_path / "stream"
    reader = proc._ProgressReader(out)
    first = job_heartbeat(11)
    out.write_bytes(first.encode())
    assert reader.poll() == 0, "the first sighting of a job only records its counters"
    out.write_bytes((first * 2).encode())
    assert reader.poll() == 0, "a repeated running job advanced nothing"
    advancing = job_heartbeat(12)
    out.write_bytes((first * 2 + advancing).encode())
    assert reader.poll() == len(advancing), "a job on its next tool call is progress"


def test_a_wait_ping_whose_durations_advance_is_still_silence(tmp_path: Path):
    out = tmp_path / "stream"
    reader = proc._ProgressReader(out)
    first = wait_heartbeat(5000)
    out.write_bytes((first + wait_heartbeat(5000)).encode())
    assert reader.poll() == 0
    out.write_bytes((first + wait_heartbeat(5000) + wait_heartbeat(500000)).encode())
    assert reader.poll() == 0, "wall-clock durations of a frozen wait are not work"


def test_a_working_subagent_heartbeat_holds_the_engine(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    code = proc.run_bounded([sys.executable, "-c", ADVANCING_JOB], env=dict(os.environ),
                            cwd=tmp_path, timeout=2, stdout_to=out, hard_timeout=30,
                            idle_gap=1.0, stall_gap=1.0)
    elapsed = time.monotonic() - started
    assert code == 0, "advancing job counters must not be cut as a stall"
    assert elapsed >= 2.0, "the run must outlive the soft deadline on its job's progress"


def test_a_frozen_subagent_heartbeat_is_cut_as_silence(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", FROZEN_JOB], env=dict(os.environ),
                         cwd=tmp_path, timeout=30, stdout_to=out, hard_timeout=60,
                         idle_gap=1.0, stall_gap=1.0)
    elapsed = time.monotonic() - started
    assert "silent 1s" in str(caught.value)
    assert elapsed < 12, f"a frozen job was tolerated {elapsed:.1f}s"


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


# A table that cannot be read is UNKNOWN, not "no children": the stall cut that acts on it
# discarded a live wake at 600 s of silence (genome 2026-11-17 19:04Z, its bash job still
# reported running). Only a confirmed empty tree may end a silent run early.
OUTPUT_THEN_SILENT = (
    "import time\n"
    "print('real output', flush=True)\n"
    "time.sleep(60)\n"
)


def test_an_unreadable_process_table_does_not_cut_a_silent_engine_early(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(proc.proc_table, "children_by_parent", lambda: None)
    monkeypatch.setattr(proc, "TOOL_WAIT_GRACE_S", 1.0)
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", OUTPUT_THEN_SILENT], env=dict(os.environ),
                         cwd=tmp_path, timeout=2, stdout_to=out, hard_timeout=60,
                         idle_gap=1.0, stall_gap=1.0)
    elapsed = time.monotonic() - started
    assert "silent" not in str(caught.value), "an unknown child table was read as childless"
    assert "exceeded 2s" in str(caught.value)
    assert elapsed >= 2.5, f"the run was cut {elapsed:.1f}s in, before its soft deadline"


def test_a_confirmed_childless_engine_is_still_cut_at_the_stall_gap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(proc.proc_table, "children_by_parent", lambda: {})
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", OUTPUT_THEN_SILENT], env=dict(os.environ),
                         cwd=tmp_path, timeout=30, stdout_to=out, hard_timeout=60,
                         idle_gap=1.0, stall_gap=1.0)
    elapsed = time.monotonic() - started
    assert str(caught.value) == "silent 1s before the 30s deadline (no live child)"
    assert elapsed < 12, f"a childless silent engine was tolerated {elapsed:.1f}s"
