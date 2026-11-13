"""The readings scan leaves a per-stage trace: a clean scan removes it, a killed scan keeps the last stage reached."""
from __future__ import annotations

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

    def slow_sample(home):
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
    events = [line.split(" ", 2)[2] for line in lines]
    assert events == ["enter", "sample:start", "failed OSError at acquisition"]


def test_killed_scan_keeps_the_last_stage_it_reached(tmp_path: Path) -> None:
    src = Path(discovery.__file__).resolve().parents[1]
    child = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from unittest.mock import patch\n"
        "from mishe_tauftauf import discovery\n"
        "with patch('mishe_tauftauf.discovery.sample', side_effect=lambda home: time.sleep(600)):\n"
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
