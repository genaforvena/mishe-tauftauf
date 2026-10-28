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
    """Two supervisors over one store: only one may hold the turn.

    The second process must be refused by the *lock*, not by the record the
    first process left behind.  The refusal has to name DispatchBusy
    specifically: a refusal caused by the open record would also happen
    without the lock, and would prove nothing about the lock.
    """
    store = tmp_path / "dispatch"
    marker = tmp_path / "holding"
    source = str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")
    # A worker that announces it holds the turn, waits for the second
    # process to try, then releases it.
    script = f"""
import sys, time
sys.path.insert(0, {source!r})
from pathlib import Path
from mishe_tauftauf.core import DispatchStore, StaticRoleRegistry, Supervisor, WorkerResult

class Holder:
    def run(self, dispatch):
        Path({str(marker)!r}).write_text("held")
        time.sleep(6)
        return WorkerResult(outcome="completed", detail="done")

supervisor = Supervisor(store=DispatchStore(Path({str(store)!r})),
                        registry=StaticRoleRegistry(["genome", "health"]),
                        worker=Holder())
supervisor.dispatch("genome", "first")
"""
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(script)])
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "the first process never took the turn"
        # The second process must be refused by the lock, while the first
        # process is still inside its own claim.
        second = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(f"""
import sys
sys.path.insert(0, {source!r})
from pathlib import Path
from mishe_tauftauf.core import DispatchStore, StaticRoleRegistry, Supervisor, WorkerResult

class Never:
    def run(self, dispatch):
        raise AssertionError("the second process must not run a worker")

supervisor = Supervisor(store=DispatchStore(Path({str(store)!r})),
                        registry=StaticRoleRegistry(["genome", "health"]),
                        worker=Never(), lock_timeout=0.5)
try:
    supervisor.dispatch("health", "second")
except Exception as exc:
    print(type(exc).__name__)
    print(exc)
""")], capture_output=True, text=True, timeout=30)
        assert second.returncode == 0, second.stderr
        assert "DispatchBusy" in second.stdout, \
            f"the second process was not refused by the lock: {second.stdout!r}"
        assert "Never" not in second.stderr
    finally:
        holder.wait(timeout=30)


def test_two_processes_racing_inside_the_claim_window_run_one_turn(tmp_path):
    """Two different turns must not run at once, even with different keys.

    The race window is the gap between reading the records and writing the
    STARTED record.  The test injects a pause inside that window, in both
    processes, and holds them at a barrier first, so both really are inside
    the window together.  Without a lock that spans the check and the
    claim, both would see "nothing open" and both would start a worker.

    The two processes use *different* keys on purpose: the rule is one mind
    at a time across the plant, not one key at a time.  A repeated key is
    refused by the key history, which would mask the race this test is
    about.

    The pause is injected rather than raced for, so the test is
    deterministic: it fails every time the lock is absent, not about half
    the time.
    """
    store = tmp_path / "dispatch"
    source = str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")
    barrier = tmp_path / "barrier"
    script = f"""
import os, sys, time
sys.path.insert(0, {source!r})
from pathlib import Path
from mishe_tauftauf.core import DispatchStore, StaticRoleRegistry, Supervisor, WorkerResult

class Marker:
    def __init__(self, path):
        self.path = path
    def run(self, dispatch):
        with open(self.path, "a") as handle:
            handle.write("ran\\n")
        return WorkerResult(outcome="completed", detail="done")

class SlowStore(DispatchStore):
    \"\"\"A store whose record read pauses inside the claim window.\"\"\"
    def records(self):
        time.sleep(1.5)
        return super().records()

barrier = Path({str(barrier)!r})
ready = barrier.with_suffix(".ready")
barrier.parent.mkdir(parents=True, exist_ok=True)
with open(ready, "a") as handle:
    handle.write(str(os.getpid()) + " ")
    handle.flush()
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        if len(ready.read_text().split()) >= 2:
            break
    except OSError:
        pass
    time.sleep(0.01)

marker = Path({str(tmp_path / "ran")!r})
supervisor = Supervisor(store=SlowStore(Path({str(store)!r})),
                        registry=StaticRoleRegistry(["genome"]),
                        worker=Marker(marker), lock_timeout=0.4)
try:
    supervisor.dispatch("genome", sys.argv[1])
    print("DISPATCHED")
except Exception as exc:
    print(type(exc).__name__)
"""
    first = subprocess.Popen([sys.executable, "-c", textwrap.dedent(script), "race-a"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen([sys.executable, "-c", textwrap.dedent(script), "race-b"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first_out = first.communicate(timeout=90)
    second_out = second.communicate(timeout=90)
    ran = (tmp_path / "ran").read_text().splitlines() if (tmp_path / "ran").is_file() else []
    dispatched = ("DISPATCHED" in first_out[0]) + ("DISPATCHED" in second_out[0])
    assert dispatched == 1, \
        f"both processes dispatched a turn: {first_out[0]!r} {second_out[0]!r}"
    assert len(ran) == 1, f"the worker ran {len(ran)} times"


def test_the_lock_is_released_when_a_dispatch_finishes(tmp_path):
    """A finished turn does not leave the lock held."""
    supervisor = make_supervisor(tmp_path, SlowWorker(0))
    supervisor.dispatch("genome", "first")
    # A second dispatch must be possible.
    record = supervisor.dispatch("genome", "second")
    assert record.state.name == "completed"


def test_an_in_flight_dispatch_cannot_be_reconciled_away(tmp_path):
    """Reconciling a running turn must not let a second role start.

    The reconciliation is under the dispatch lock, so a running worker's
    record cannot be closed while the worker is still working.  The
    reconciliation waits for the lock, and when it gets it the turn has
    already finished — so it is refused as "not an open dispatch" rather
    than being applied to a running worker.
    """
    class Holding:
        def __init__(self):
            self.holding = False
            self.released = False

        def run(self, dispatch):
            self.holding = True
            time.sleep(2)
            self.holding = False
            self.released = True
            return WorkerResult(outcome="completed", detail="done")

    worker = Holding()
    supervisor = make_supervisor(tmp_path, worker, )
    supervisor.lock_timeout = 0.3
    import threading

    def run_turn():
        supervisor.dispatch("genome", "first")

    thread = threading.Thread(target=run_turn)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not worker.holding and time.monotonic() < deadline:
            time.sleep(0.02)
        assert worker.holding, "the first turn never started"
        # The reconciliation cannot take the lock while the worker runs, so
        # it is refused rather than applied to a running record.
        refused = False
        try:
            supervisor.reconcile("genome", detail="checked while running")
        except (SupervisorError, DispatchBusy):
            refused = True
        assert refused, "a running turn was reconciled away"
        # While the worker runs, the record is open and no second role may
        # start beside it.
        assert supervisor.open_dispatch("genome") is not None
        with pytest.raises((SupervisorError, DispatchBusy)):
            supervisor.dispatch("health", "second")
    finally:
        thread.join(timeout=30)


def test_the_same_durable_key_cannot_be_dispatched_twice(tmp_path):
    """A key names the work; dispatching it again would run it again."""
    supervisor = make_supervisor(tmp_path, SlowWorker(0))
    supervisor.dispatch("genome", "wake-41")
    with pytest.raises(SupervisorError) as caught:
        supervisor.dispatch("genome", "wake-41")
    assert "already been dispatched" in str(caught.value)
    # A different key is a different piece of work, and is allowed.
    record = supervisor.dispatch("genome", "wake-42")
    assert record.state.name == "completed"


def test_an_older_key_cannot_be_dispatched_again(tmp_path):
    """A key stays used for the whole history of the role.

    The role's record holds only its most recent key, so the earlier ones
    are kept beside it.  Without that, dispatching an older key would run
    that work a second time.
    """
    worker = SlowWorker(0)
    supervisor = make_supervisor(tmp_path, worker)
    supervisor.dispatch("genome", "wake-1")
    supervisor.dispatch("genome", "wake-2")
    with pytest.raises(SupervisorError) as caught:
        supervisor.dispatch("genome", "wake-1")
    assert "already been dispatched" in str(caught.value)
    assert worker.calls == 2


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
    """The whole group is stopped, not just the leader.

    The descendant ignores SIGTERM on purpose.  A descendant that dies on
    the default SIGTERM would also die under the pre-fix code, which stopped
    the group and then returned as soon as the leader exited; only a
    descendant that survives SIGTERM shows whether the whole group was
    actually dealt with.
    """
    from mishe_tauftauf.core import SubprocessWorker
    marker = tmp_path / "child-pid"
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
    worker = SubprocessWorker(timeout=0.8, cwd=tmp_path, stop_grace_seconds=1.0)
    result = worker.run(Dispatch(role="genome", key="k", payload={
        "command": [sys.executable, "-c", script]}))
    assert result.outcome == "unknown"
    assert marker.is_file()
    child = int(marker.read_text().strip())
    deadline = time.monotonic() + 15
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


def test_the_lock_timeout_is_honoured(tmp_path):
    """A caller that cannot get the lock waits for its own bound."""
    import threading
    supervisor = make_supervisor(tmp_path, SlowWorker(0))
    supervisor.lock_timeout = 0.4
    holding = tmp_path / "holding"
    holder = threading.Thread(target=lambda: holding.write_text("held"))
    holder.start()
    holder.join()
    # Take the lock in another thread and hold it, so the second attempt
    # must wait for its own bound and then be refused.
    lock_path = supervisor.store.lock_path
    import fcntl
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(DispatchBusy):
            supervisor.dispatch("genome", "first")
        waited = time.monotonic() - started
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    assert waited >= 0.35, f"the refusal came after {waited:.2f}s, not the configured bound"


def test_the_stop_grace_is_honoured(tmp_path):
    """A stopped worker waits for the grace it was given.

    The descendant ignores SIGTERM, so the group does not empty on the
    first signal and the grace is the interval the worker actually waits
    before escalating to SIGKILL.
    """
    from mishe_tauftauf.core import SubprocessWorker
    child_code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(120)\n"
    )
    script = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(120)\n"
    )
    quick = SubprocessWorker(timeout=0.3, cwd=tmp_path, stop_grace_seconds=0.3)
    started = time.monotonic()
    quick.run(Dispatch(role="genome", key="k", payload={
        "command": [sys.executable, "-c", script]}))
    quick_elapsed = time.monotonic() - started
    patient = SubprocessWorker(timeout=0.3, cwd=tmp_path, stop_grace_seconds=2.0)
    started = time.monotonic()
    patient.run(Dispatch(role="genome", key="k2", payload={
        "command": [sys.executable, "-c", script]}))
    patient_elapsed = time.monotonic() - started
    assert patient_elapsed > quick_elapsed + 0.5, \
        f"the configured grace was ignored: {quick_elapsed:.2f}s then {patient_elapsed:.2f}s"


def test_an_orphaned_worker_blocks_a_second_dispatch(tmp_path):
    """A worker that outlived its supervisor must not run beside another.

    The record carries the worker's pid, so a successor can tell that the
    worker is still running.  Reconciling such a record is refused, because
    closing it would let the next dispatch start a second worker.
    """
    import os
    import signal
    from mishe_tauftauf.core import SubprocessWorker
    marker = tmp_path / "orphan-pid"
    # The worker records its own pid and then sleeps.
    script = (
        "import os, time\n"
        f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    store = DispatchStore(tmp_path / "dispatch")
    supervisor = Supervisor(store=store, registry=StaticRoleRegistry(["genome"]),
                            worker=SubprocessWorker(timeout=60, cwd=tmp_path))
    # The supervisor is killed while its worker runs, so the record keeps
    # the started state and the worker keeps running.
    import threading
    result = {}

    def run_turn():
        result["record"] = supervisor.dispatch("genome", "orphan-1", payload={
            "command": [sys.executable, "-c", script]})

    thread = threading.Thread(target=run_turn)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while (not marker.exists() or not marker.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.is_file() and marker.read_text().strip(), "the worker never started"
        pid = int(marker.read_text().strip())
        # The record names the worker, so a successor can find it.
        record = store.read("genome")
        assert record is not None
        assert record.pid == pid, f"the record names {record.pid}, not the worker {pid}"
        # The reconciliation must be refused while the worker is alive.
        refused = False
        try:
            supervisor.reconcile("genome", detail="checking the orphan")
        except SupervisorError:
            refused = True
        assert refused, "a live worker's record was reconciled away"
        assert _alive(pid)
    finally:
        try:
            os.kill(int(marker.read_text().strip()), signal.SIGKILL)
        except (OSError, ValueError):
            pass
        thread.join(timeout=30)


def _ignore(value):
    return value


def _dispatch(supervisor, role, key):
    try:
        supervisor.dispatch(role, key, payload={"command": [sys.executable, "-c", "print('x')"]})
    except Exception:
        pass
