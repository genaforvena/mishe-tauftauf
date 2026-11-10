"""A wake that keeps producing output is not cut at the soft deadline (operator DM 298902).

An engine that is carrying on may run past the soft deadline up to a hard cap; an idle engine
and the hard cap both end the run as a timeout, exactly as the plain deadline does.
"""
import os
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import engine_process as proc
from mishe_tauftauf.wake import EngineTimeout

GROWING = "import time,sys\nfor i in range(40):\n    print('x'*100, flush=True)\n    time.sleep(0.2)\n"
SILENT = "import time\ntime.sleep(60)\n"


def test_a_growing_engine_outlives_the_soft_deadline(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    code = proc.run_bounded([sys.executable, "-c", GROWING], env=dict(os.environ), cwd=tmp_path,
                            timeout=1, stdout_to=out, hard_timeout=20, idle_gap=1.0)
    assert code == 0
    assert time.monotonic() - started >= 1.0, "the run was cut at the soft deadline"


def test_an_idle_engine_is_cut_at_the_soft_deadline(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout):
        proc.run_bounded([sys.executable, "-c", SILENT], env=dict(os.environ), cwd=tmp_path,
                         timeout=1, stdout_to=out, hard_timeout=60, idle_gap=1.0)
    assert time.monotonic() - started < 30, "an idle engine kept the wake running"


def test_the_hard_cap_ends_a_growing_engine(tmp_path: Path):
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", GROWING], env=dict(os.environ), cwd=tmp_path,
                         timeout=1, stdout_to=out, hard_timeout=3, idle_gap=2.0)
    assert "hard cap" in str(caught.value)
    assert time.monotonic() - started < 30


def test_without_a_hard_cap_the_soft_deadline_is_unchanged(tmp_path: Path):
    out = tmp_path / "stream"
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", GROWING], env=dict(os.environ), cwd=tmp_path,
                         timeout=1, stdout_to=out)
    assert str(caught.value) == "exceeded 1s"


def test_a_cap_below_the_soft_deadline_changes_nothing(tmp_path: Path):
    out = tmp_path / "stream"
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", GROWING], env=dict(os.environ), cwd=tmp_path,
                         timeout=5, stdout_to=out, hard_timeout=2)
    assert str(caught.value) == "exceeded 5s"
