"""The bounded worker: its own process group, and an honest timeout."""
import os
import subprocess
import sys
import time

from mishe_tauftauf.core import Dispatch, SubprocessWorker, WorkerResult


def test_a_finished_command_reports_its_output(tmp_path):
    worker = SubprocessWorker(timeout=10, cwd=tmp_path)
    result = worker.run(Dispatch(role="genome", key="wake-1",
                                  payload={"command": [sys.executable, "-c",
                                                      "print('useful result')"]}))
    assert result.outcome == "completed"
    assert "useful result" in result.detail


def test_a_failing_command_is_unknown_because_its_effect_is_not_established(tmp_path):
    """A non-zero exit does not prove the command had no effect."""
    worker = SubprocessWorker(timeout=10, cwd=tmp_path)
    result = worker.run(Dispatch(role="genome", key="wake-1",
                                  payload={"command": [sys.executable, "-c",
                                                      "raise SystemExit(3)"]}))
    assert result.outcome == "unknown"


def test_a_command_that_cannot_start_is_a_failure(tmp_path):
    worker = SubprocessWorker(timeout=10, cwd=tmp_path)
    result = worker.run(Dispatch(role="genome", key="wake-1",
                                  payload={"command": ["/nonexistent/binary"]}))
    assert result.outcome == "failed"
    assert "could not start" in result.detail


def test_a_dispatch_without_a_command_is_refused(tmp_path):
    worker = SubprocessWorker(timeout=10, cwd=tmp_path)
    try:
        worker.run(Dispatch(role="genome", key="wake-1", payload={}))
    except ValueError as exc:
        assert "command" in str(exc)
    else:
        raise AssertionError("a dispatch without a command must be refused")


def test_a_worker_that_overruns_its_bound_reports_an_unknown_effect(tmp_path):
    worker = SubprocessWorker(timeout=0.4, cwd=tmp_path)
    result = worker.run(Dispatch(role="genome", key="wake-1",
                                  payload={"command": [sys.executable, "-c",
                                                      "import time; time.sleep(30)"]}))
    # The worker was stopped, so it may already have had its effect.
    assert result.outcome == "unknown"
    assert "unknown" in result.detail


def test_a_timed_out_worker_leaves_no_descendant_behind(tmp_path):
    marker = tmp_path / "child-pid"
    # The child ignores SIGTERM, so it survives the pre-fix behaviour that
    # stopped the group and returned as soon as the leader exited.  Only a
    # child that outlives SIGTERM shows whether the whole group was dealt
    # with.
    child_code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(120)\n"
    )
    script = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    worker = SubprocessWorker(timeout=0.6, cwd=tmp_path, stop_grace_seconds=1.0)
    result = worker.run(Dispatch(role="genome", key="wake-1",
                                  payload={"command": [sys.executable, "-c", script]}))
    assert result.outcome == "unknown"
    assert marker.is_file()
    child = int(marker.read_text().strip())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not _alive(child):
            break
        time.sleep(0.05)
    assert not _alive(child), "the timed-out worker left a descendant running"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie is not running work; the parent is gone, so it is reaped.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
    except OSError:
        return False
    return fields[0] != "Z"
