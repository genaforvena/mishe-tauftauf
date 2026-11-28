"""The readings scan leaves a per-stage trace: a clean scan removes it, a killed scan keeps the last stage reached."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mishe_tauftauf import discovery
from mishe_tauftauf.discovery import scan
from tests.tape_binary import pin_tape_binary  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("pin_tape_binary")


def _traces(home: Path) -> list[Path]:
    return sorted((home / "signals").glob("readings-scan-trace-*.txt"))


def test_clean_scan_records_each_stage_in_order_and_removes_its_trace(tmp_path: Path) -> None:
    value = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": []}

    def slow_sample(home, trace=None):
        time.sleep(0.05)  # a forced slow stage: the trace must show its elapsed time
        return value

    with patch("mishe_tauftauf.discovery.sample", side_effect=slow_sample):
        scan(tmp_path)
    # The trace is removed on success, so a clean run leaves no trace behind.
    assert _traces(tmp_path) == []


def test_failed_scan_keeps_its_trace_with_the_failing_stage(tmp_path: Path) -> None:
    value = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": []}
    with patch("mishe_tauftauf.discovery.sample", return_value=value):
        scan(tmp_path)
    with patch("mishe_tauftauf.discovery.sample", side_effect=OSError("controlled acquisition failure")):
        with pytest.raises(OSError, match="controlled acquisition failure"):
            scan(tmp_path)
    traces = _traces(tmp_path)
    assert len(traces) == 1
    lines = traces[0].read_text(encoding="utf-8").splitlines()
    events = [line.split(" ", 2)[2].split(" | ")[0] for line in lines]
    assert events == ["enter", "sample:start", "failed OSError at acquisition"]
    # Each stage line carries this scan's write total and the host pressure, so a stall can be attributed.
    assert all(" | wbytes=" in line and " psi_mem=" in line and " psi_io=" in line for line in lines)


def test_killed_scan_keeps_the_last_stage_it_reached(tmp_path: Path) -> None:
    src = Path(discovery.__file__).resolve().parents[1]
    child = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from unittest.mock import patch\n"
        "from mishe_tauftauf import discovery\n"
        "with patch('mishe_tauftauf.discovery.sample', side_effect=lambda home, trace=None: time.sleep(600)):\n"
        "    discovery.scan(Path(sys.argv[1]))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(src)}
    process = subprocess.Popen([sys.executable, "-c", child, str(tmp_path)], env=env)
    try:
        # Wait until the child is inside the blocked sample stage, then kill it the way the producer does.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            traces = _traces(tmp_path)
            if traces and "sample:start" in traces[0].read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        else:
            pytest.fail("the scan never reached the sample stage")
        process.kill()
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
    traces = _traces(tmp_path)
    assert len(traces) == 1
    text = traces[0].read_text(encoding="utf-8")
    assert "sample:start" in text
    assert "sample:done" not in text and "succeeded" not in text


def test_slow_clean_scan_keeps_trace_when_its_stack_dump_fired(tmp_path: Path) -> None:
    # A scan that crosses its dump threshold and then finishes must keep its stage trace:
    # the trace (with the stack dump) is the only evidence of where a slow run spent its time.
    value = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": []}

    def slow_sample(home, trace=None):
        time.sleep(0.5)
        return value

    with patch("mishe_tauftauf.discovery.SCAN_STACK_DUMP_S", 0.1), \
            patch("mishe_tauftauf.discovery.sample", side_effect=slow_sample):
        scan(tmp_path)
    traces = _traces(tmp_path)
    assert len(traces) == 1
    text = traces[0].read_text(encoding="utf-8")
    assert "sample:start" in text and "sample:done" in text and "publication" in text
    stacks = sorted((tmp_path / "signals").glob("readings-scan-stack-*.txt"))
    assert len(stacks) == 1 and "Timeout" in stacks[0].read_text(encoding="utf-8")


def test_clean_scan_keeps_one_snapshot_timing_line(tmp_path: Path) -> None:
    value = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": []}

    def slow_sample(home, trace=None):
        trace("stage:snapshot:start")  # sample() reports its snapshot stage through the trace callback
        time.sleep(0.05)
        trace("stage:snapshot:done")
        return value

    with patch("mishe_tauftauf.discovery.sample", side_effect=slow_sample):
        scan(tmp_path)
    lines = (tmp_path / "signals" / "readings-scan-timing.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    # The forced slow sample is inside the snapshot stage, so its seconds cover the sleep.
    assert record["snapshot_s"] is not None and record["snapshot_s"] >= 0.04
    assert record["total_s"] >= record["snapshot_s"]
    assert " psi_io=" in record["snapshot_done_host"]


def test_slow_clean_scan_keeps_its_stage_trace_and_fast_one_removes_it(tmp_path: Path) -> None:
    value = {"created": "2026-01-01T00:00:00Z", "node": "node", "observations": []}

    def slow_sample(home, trace=None):
        trace("stage:snapshot:start")
        time.sleep(0.05)
        trace("stage:snapshot:done")
        return value

    with patch.object(discovery, "SLOW_SNAPSHOT_TRACE_S", 0.01):
        with patch("mishe_tauftauf.discovery.sample", side_effect=slow_sample):
            scan(tmp_path)
    # Over the threshold: the clean run keeps the trace, so the slow snapshot's stages are on disk.
    assert len(_traces(tmp_path)) == 1
    assert "stage:snapshot:done" in _traces(tmp_path)[0].read_text(encoding="utf-8")

    with patch("mishe_tauftauf.discovery.sample", return_value=value):
        scan(tmp_path)
    # Under the threshold: no new trace; the earlier slow one is still there.
    assert len(_traces(tmp_path)) == 1
