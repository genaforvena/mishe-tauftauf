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


def test_a_stalled_engine_is_cut_before_the_soft_deadline(tmp_path: Path):
    """An engine that wrote and then went silent is cut at the stall gap, not at the deadline.

    rust-plan 2026-11-16: the wake finished and verified its work at 19:00:15Z, produced nothing
    after, and was cut at the 19:26:57Z soft deadline. Silence before the deadline ends the run
    too, so the dispatch slot is released instead of being held for the whole soft timeout.
    """
    out = tmp_path / "stream"
    started = time.monotonic()
    with pytest.raises(EngineTimeout) as caught:
        proc.run_bounded([sys.executable, "-c", ONE_WRITE], env=dict(os.environ), cwd=tmp_path,
                         timeout=60, stdout_to=out, hard_timeout=120, idle_gap=30.0, stall_gap=1.0)
    elapsed = time.monotonic() - started
    assert "silent" in str(caught.value), f"cut by another path, not the stall gap: {caught.value}"
    assert elapsed < 20, f"the stalled engine held the run for {elapsed:.2f}s"


def test_a_child_wait_defers_the_stall_cut_before_the_soft_deadline(tmp_path: Path):
    """A silent wait on a live child is not a stall, before the deadline as well as after it.

    The child exists before the engine's one line, so the silence that follows it is a wait on a
    child already in the table; the cut must not fire, and the engine exits cleanly.
    """
    out = tmp_path / "stream"
    writes_then_waits = ("import subprocess,sys,time\n"
                         "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(8)'])\n"
                         "print('x'*100, flush=True)\n"
                         "child.wait()\n")
    started = time.monotonic()
    code = proc.run_bounded([sys.executable, "-c", writes_then_waits], env=dict(os.environ), cwd=tmp_path,
                            timeout=60, stdout_to=out, hard_timeout=120, idle_gap=30.0, stall_gap=1.0)
    assert code == 0
    assert time.monotonic() - started >= 8.0, "the engine waiting on its child was cut as idle"


def _waits_on_child(seconds: float) -> str:
    """Engine code that forks a child, announces it, then waits on it in silence."""
    return ("import subprocess,sys\n"
            f"child=subprocess.Popen([sys.executable,'-c','import time; time.sleep({seconds})'])\n"
            "print('waiting on child', child.pid, flush=True)\n"
            "child.wait()\n")


def test_an_engine_waiting_on_a_live_child_is_not_idle(tmp_path: Path):
    """Pub wake 19:02Z: the mind waited silently on a background job and was cut as idle.

    The idle window is measured from the engine's own line, not from ``Popen``: a full-suite
    delivery check on a loaded host took longer than ``idle_gap`` just to start the interpreter,
    so measuring from ``Popen`` cut a good run before its child existed (8c568a59 gate,
    2026-11-18). The callback returns once that line, and so the child, is in the stream; the
    2.5 s window then has real teeth, because a run that ignored the child would be cut here.
    """
    out = tmp_path / "stream"

    def child_is_running(pid: int) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if "waiting on child" in out.read_text():
                    return
            except FileNotFoundError:
                pass
            time.sleep(0.02)
        raise AssertionError("the engine never reported its child")

    started = time.monotonic()
    code = proc.run_bounded([sys.executable, "-c", _waits_on_child(8)], env=dict(os.environ), cwd=tmp_path,
                            timeout=1, stdout_to=out, hard_timeout=20, idle_gap=2.5,
                            on_start=child_is_running)
    assert code == 0
    assert time.monotonic() - started >= 8.0, "the engine waiting on its child was cut as idle"


def test_a_child_does_not_extend_the_hard_cap(tmp_path: Path):
    out = tmp_path / "stream"
    with pytest.raises(EngineTimeout, match="hard cap"):
        proc.run_bounded([sys.executable, "-c", _waits_on_child(60)],
                         env=dict(os.environ), cwd=tmp_path, timeout=1, stdout_to=out,
                         hard_timeout=3, idle_gap=1.0)
