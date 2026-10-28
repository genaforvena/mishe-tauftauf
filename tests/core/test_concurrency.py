"""The core's refusal rules under real concurrency and real effects.

These tests use real processes, not threads: the lock that keeps one mind
at a time has to hold across processes, because the second supervisor is a
separate unit.
"""
import os
import subprocess
import sys
import textwrap
import time

import pytest

from mishe_tauftauf.core import (Dispatch, DispatchStore, StaticRoleRegistry,
                                 Supervisor, SupervisorError, WorkerResult)
from mishe_tauftauf.core.supervisor import DispatchBusy


class SlowWorker:
    """A worker that takes long enough for another process to collide."""

    def __init__(self, seconds=1.0):
        self.seconds = seconds
        self.calls = 0

    def run(self, dispatch):
        self.calls += 1
        time.sleep(self.seconds)
        return WorkerResult(outcome="completed", detail="done")


def make_supervisor(tmp_path, worker, roles=("genome", "health")):
    return Supervisor(store=DispatchStore(tmp_path / "dispatch"),
                      registry=StaticRoleRegistry(roles), worker=worker)


def test_missing_observation_reads_unknown_not_healthy(tmp_path):
    """No detector is no evidence, and no evidence is not a pass."""
    supervisor = make_supervisor(tmp_path, SlowWorker(0))
    reading = supervisor.observe()
    assert reading.fresh is False
    assert reading.healthy is False
    assert "no observation source" in reading.detail


def test_a_broken_observer_reads_unknown_not_healthy(tmp_path):
    def broken():
        raise OSError("the observer cannot read its source")

    supervisor = Supervisor(store=DispatchStore(tmp_path / "dispatch"),
                            registry=StaticRoleRegistry(["genome"]),
                            worker=SlowWorker(0), observer=broken)
    with pytest.raises(OSError):
        supervisor.observe()


def test_a_second_process_cannot_dispatch_while_one_is_running(tmp_path):
    """Two supervisors over one store: only one may hold the turn."""
    store = tmp_path / "dispatch"
    marker = tmp_path / "holding"
    # A worker that announces it holds the turn, waits for the second
    # process to try, then releases it.
    script = f"""
import sys, time
sys.path.insert(0, {str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")!r})
from pathlib import Path
from mishe_tauftauf.core import DispatchStore, StaticRoleRegistry, Supervisor, WorkerResult

class Holder:
    def run(self, dispatch):
        Path({str(marker)!r}).write_text("held")
        time.sleep(3)
        return WorkerResult(outcome="completed", detail="done")

supervisor = Supervisor(store=DispatchStore(Path({str(store)!r})),
                        registry=StaticRoleRegistry(["genome", "health"]),
                        worker=Holder())
supervisor.dispatch("genome", "first")
"""
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(script)])
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "the first process never took the turn"
        # The second process must be refused, not merely queued behind the
        # first one's record.
        second = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(f"""
import sys
sys.path.insert(0, {str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")!r})
from pathlib import Path
from mishe_tauftauf.core import DispatchStore, StaticRoleRegistry, Supervisor, WorkerResult

class Never:
    def run(self, dispatch):
        raise AssertionError("the second process must not run a worker")

supervisor = Supervisor(store=DispatchStore(Path({str(store)!r})),
                        registry=StaticRoleRegistry(["genome", "health"]),
                        worker=Never())
try:
    supervisor.dispatch("health", "second")
except Exception as exc:
    print(type(exc).__name__)
    print(exc)
""")], capture_output=True, text=True, timeout=30)
        assert second.returncode == 0, second.stderr
        assert "DispatchBusy" in second.stdout or "is started" in second.stdout, \
            f"the second process was not refused: {second.stdout!r}"
        assert "Never" not in second.stderr
    finally:
        holder.wait(timeout=30)


def test_the_lock_is_released_when_a_dispatch_finishes(tmp_path):
    """A finished turn does not leave the lock held."""
    supervisor = make_supervisor(tmp_path, SlowWorker(0))
    supervisor.dispatch("genome", "first")
    # A second dispatch must be possible.
    record = supervisor.dispatch("genome", "second")
    assert record.state.name == "completed"


def test_the_lock_is_released_when_a_dispatch_raises(tmp_path):
    """A failed turn does not leave the lock held."""
    class Raising:
        def run(self, dispatch):
            raise RuntimeError("worker died")

    supervisor = make_supervisor(tmp_path, Raising())
    with pytest.raises(RuntimeError):
        supervisor.dispatch("genome", "first")
    # The record is open, so the next dispatch is refused — by the record,
    # not by a stuck lock.
    with pytest.raises(SupervisorError) as caught:
        supervisor.dispatch("genome", "second")
    assert "reconcile" in str(caught.value)


def test_a_worker_that_exits_non_zero_is_unknown_not_failed(tmp_path):
    """A non-zero exit does not prove the effect did not happen."""
    from mishe_tauftauf.core import SubprocessWorker
    worker = SubprocessWorker(timeout=30)
    result = worker.run(Dispatch(role="genome", key="k", payload={
        "command": [sys.executable, "-c", "raise SystemExit(3)"]}))
    assert result.outcome == "unknown"


def test_a_worker_that_never_started_is_failed(tmp_path):
    """No process means no effect, so it is a plain failure."""
    from mishe_tauftauf.core import SubprocessWorker
    worker = SubprocessWorker(timeout=30)
    result = worker.run(Dispatch(role="genome", key="k", payload={
        "command": ["/nonexistent/binary"]}))
    assert result.outcome == "failed"


def test_a_non_zero_exit_blocks_the_next_dispatch(tmp_path):
    """The unknown effect is what blocks the replay, not the exit code."""
    from mishe_tauftauf.core import SubprocessWorker
    supervisor = Supervisor(store=DispatchStore(tmp_path / "dispatch"),
                            registry=StaticRoleRegistry(["genome"]),
                            worker=SubprocessWorker(timeout=30))
    record = supervisor.dispatch("genome", "first", payload={
        "command": [sys.executable, "-c", "raise SystemExit(3)"]})
    assert record.state.name == "unknown"
    with pytest.raises(SupervisorError) as caught:
        supervisor.dispatch("genome", "second", payload={
            "command": [sys.executable, "-c", "print('replay')"]})
    assert "reconcile" in str(caught.value)


def test_a_stopped_worker_leaves_no_live_descendant(tmp_path):
    """The whole group is stopped, not just the leader."""
    from mishe_tauftauf.core import SubprocessWorker
    marker = tmp_path / "child-pid"
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    worker = SubprocessWorker(timeout=0.8, cwd=tmp_path)
    result = worker.run(Dispatch(role="genome", key="k", payload={
        "command": [sys.executable, "-c", script]}))
    assert result.outcome == "unknown"
    assert marker.is_file()
    child = int(marker.read_text().strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _alive(child):
            break
        time.sleep(0.05)
    assert not _alive(child), "the stopped worker left a live descendant"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
    except OSError:
        return False
    return fields[0] != "Z"
