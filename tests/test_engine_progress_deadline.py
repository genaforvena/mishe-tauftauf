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
ONE_WRITE = "import time,sys\nprint('x'*100, flush=True)\ntime.sleep(60)\n"


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


def test_the_hard_cap_holds_within_a_tenth_of_a_second(tmp_path: Path):
    """The cap is the total runtime: the run ends at the cap, not at the next poll."""
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", GROWING], env=dict(os.environ), cwd=tmp_path,
                         timeout=1, stdout_to=out, hard_timeout=3, idle_gap=2.0)
    elapsed = time.monotonic() - started
    assert "hard cap" in str(caught.value)
    assert 3.0 <= elapsed < 3.5, f"the run ended {elapsed:.2f}s after the start, not at the 3s cap"


def test_an_idle_engine_is_cut_one_gap_after_its_last_output(tmp_path: Path):
    """The idle cut lands at the gap after the last output, not one poll later.

    The engine writes once and then goes quiet: it was carrying on at the soft deadline,
    so it is cut one idle gap after that output rather than at the deadline itself.
    """
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", ONE_WRITE], env=dict(os.environ), cwd=tmp_path,
                         timeout=1, stdout_to=out, hard_timeout=60, idle_gap=1.0)
    elapsed = time.monotonic() - started
    assert "no output" in str(caught.value)
    assert 2.0 <= elapsed < 2.6, f"the idle engine ran {elapsed:.2f}s, not one gap after its output"


def test_a_cap_below_the_idle_gap_still_ends_the_run(tmp_path: Path):
    """A cap shorter than the idle gap is still the end of the run.

    The two bounds are independent: an engine that keeps writing is cut at the cap even
    though its output never went idle for the full gap.
    """
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", GROWING], env=dict(os.environ), cwd=tmp_path,
                         timeout=0.5, stdout_to=out, hard_timeout=2, idle_gap=30.0)
    elapsed = time.monotonic() - started
    assert "hard cap" in str(caught.value)
    assert 2.0 <= elapsed < 2.5, f"the run ended {elapsed:.2f}s after the start, not at the 2s cap"
