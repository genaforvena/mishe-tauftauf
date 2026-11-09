"""Several minds may run at once, up to a configured limit; the default stays one."""
import json
import threading

import pytest

from mishe_tauftauf.core import DispatchStore, StaticRoleRegistry, Supervisor, SupervisorError, WorkerResult
from mishe_tauftauf.core.supervisor import DispatchBusy

ROLES = ("genome", "health", "docs")


class Gate:
    """A worker that blocks until released, so wakes overlap."""

    def __init__(self):
        self.started, self.release = threading.Semaphore(0), threading.Event()

    def run(self, dispatch):
        self.started.release()
        self.release.wait(10)
        return WorkerResult(outcome="completed", detail="ok")


def make(tmp_path, worker, timeout=1.0):
    return Supervisor(store=DispatchStore(tmp_path / "dispatch"), registry=StaticRoleRegistry(ROLES),
                      worker=worker, lock_timeout=timeout)


def limit(tmp_path, n):
    (tmp_path / "wake-concurrency.json").write_text(json.dumps({"max": n}))


def run_in_thread(sup, role, key, errors):
    def target():
        try:
            sup.dispatch(role, key)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=target)
    thread.start()
    return thread


def test_default_limit_is_one(tmp_path):
    gate = Gate()
    sup = make(tmp_path, gate)
    errors = []
    first = run_in_thread(sup, "genome", "k1", errors)
    gate.started.acquire(timeout=5)
    with pytest.raises(DispatchBusy):
        sup.dispatch("health", "k2")
    gate.release.set()
    first.join()
    assert not errors


@pytest.mark.parametrize("bad", ["x", 0, 99, True, -1])
def test_a_bad_limit_reads_as_one(tmp_path, bad):
    (tmp_path / "wake-concurrency.json").write_text(json.dumps({"max": bad}))
    assert DispatchStore(tmp_path / "dispatch").max_concurrent() == 1
    (tmp_path / "wake-concurrency.json").write_text("not json")
    assert DispatchStore(tmp_path / "dispatch").max_concurrent() == 1


def test_two_slots_run_two_roles_and_refuse_a_third_and_a_repeat(tmp_path):
    limit(tmp_path, 2)
    gate = Gate()
    sup = make(tmp_path, gate)
    errors = []
    a = run_in_thread(sup, "genome", "k1", errors)
    b = run_in_thread(sup, "health", "k2", errors)
    gate.started.acquire(timeout=5)
    gate.started.acquire(timeout=5)
    store = sup.store
    assert store.is_running("genome") and store.is_running("health")
    assert store.stale_open_records() == []          # running records are not stale
    with pytest.raises(DispatchBusy):                 # no third slot
        sup.dispatch("docs", "k3")
    with pytest.raises(DispatchBusy):                 # a role never runs twice
        sup.dispatch("genome", "k4")
    gate.release.set()
    a.join()
    b.join()
    assert not errors
    assert not store.is_running("genome")
    assert sup.status("genome").state.name == "completed"


def test_a_dead_open_record_still_blocks_other_roles(tmp_path):
    limit(tmp_path, 2)

    class Dies:
        def run(self, dispatch):
            raise RuntimeError("provider died")

    sup = make(tmp_path, Dies())
    with pytest.raises(RuntimeError):
        sup.dispatch("genome", "k1")
    assert [r.role for r in sup.store.stale_open_records()] == ["genome"]
    with pytest.raises(SupervisorError):
        sup.dispatch("health", "k2")


def test_a_delivery_style_exclusive_lock_waits_for_every_running_wake(tmp_path):
    import fcntl
    limit(tmp_path, 2)
    gate = Gate()
    sup = make(tmp_path, gate)
    errors = []
    t = run_in_thread(sup, "genome", "k1", errors)
    gate.started.acquire(timeout=5)
    with sup.store.lock_path.open("a+") as handle:
        with pytest.raises(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    gate.release.set()
    t.join()
    with sup.store.lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)   # free again
    assert not errors


def test_a_running_role_cannot_be_reconciled_away(tmp_path):
    limit(tmp_path, 2)
    gate = Gate()
    sup = make(tmp_path, gate)
    errors = []
    t = run_in_thread(sup, "genome", "k1", errors)
    gate.started.acquire(timeout=5)
    with pytest.raises(SupervisorError):
        sup.reconcile("genome", detail="checked")
    gate.release.set()
    t.join()
