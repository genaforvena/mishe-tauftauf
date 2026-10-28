"""The core supervisor's contract: declared roles, one at a time, no replay."""
import pytest

from mishe_tauftauf.core import (DispatchStore, StaticRoleRegistry, Supervisor,
                                 SupervisorError, WorkerResult)


class RecordingWorker:
    """A worker that reports what it was asked to do."""

    def __init__(self, outcome="completed", detail="done", raises=None):
        self.outcome = outcome
        self.detail = detail
        self.raises = raises
        self.seen = []

    def run(self, dispatch):
        self.seen.append(dispatch)
        if self.raises is not None:
            raise self.raises
        return WorkerResult(outcome=self.outcome, detail=self.detail)


def make_supervisor(tmp_path, worker=None, roles=("genome", "health")):
    return Supervisor(store=DispatchStore(tmp_path / "dispatch"),
                      registry=StaticRoleRegistry(roles),
                      worker=worker or RecordingWorker())


def test_only_a_declared_role_may_be_dispatched(tmp_path):
    supervisor = make_supervisor(tmp_path)
    with pytest.raises(SupervisorError):
        supervisor.dispatch("witness", "wake-1")
    assert supervisor.status("witness") is None


def test_a_completed_dispatch_is_recorded_and_readable(tmp_path):
    worker = RecordingWorker(detail="settled wake 41")
    supervisor = make_supervisor(tmp_path, worker)
    record = supervisor.dispatch("genome", "wake-41")
    assert record.state.name == "completed"
    assert record.detail == "settled wake 41"
    assert supervisor.status("genome").key == "wake-41"


def test_a_second_dispatch_of_the_same_role_is_refused_while_one_is_open(tmp_path):
    class Blocking(RecordingWorker):
        def run(self, dispatch):
            self.seen.append(dispatch)
            raise RuntimeError("provider died")

    supervisor = make_supervisor(tmp_path, Blocking())
    with pytest.raises(RuntimeError):
        supervisor.dispatch("genome", "wake-1")
    record = supervisor.status("genome")
    assert record.state.name == "unknown"
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "wake-2")


def test_an_open_dispatch_blocks_every_role(tmp_path):
    class Blocking(RecordingWorker):
        def run(self, dispatch):
            self.seen.append(dispatch)
            raise RuntimeError("provider died")

    supervisor = make_supervisor(tmp_path, Blocking())
    with pytest.raises(RuntimeError):
        supervisor.dispatch("genome", "wake-1")
    with pytest.raises(SupervisorError):
        supervisor.dispatch("health", "wake-2")


def test_a_failed_worker_is_recorded_as_failed_not_unknown(tmp_path):
    supervisor = make_supervisor(tmp_path, RecordingWorker(outcome="failed", detail="no route"))
    record = supervisor.dispatch("genome", "wake-1")
    assert record.state.name == "failed"
    # A failed worker that reported its failure did not hide an effect, so
    # the next dispatch is allowed.
    supervisor.dispatch("genome", "wake-2")


def test_a_skipped_turn_is_not_a_failure(tmp_path):
    supervisor = make_supervisor(tmp_path, RecordingWorker(outcome="skipped", detail="no turn owed"))
    record = supervisor.dispatch("genome", "wake-1")
    assert record.state.name == "skipped"
    assert record.state.name != "failed"


def test_effect_free_resident_polls_do_not_accumulate_keys_or_erase_work(tmp_path):
    worker = RecordingWorker()
    supervisor = make_supervisor(tmp_path, worker)
    supervisor.dispatch("genome", "actual-work")
    before = supervisor.store.path_for("genome").read_bytes()
    worker.outcome = "skipped"
    for number in range(50):
        result = supervisor.dispatch("genome", f"idle-{number}", discard_skipped_poll=True)
        assert result.state.name == "skipped"
    assert supervisor.store.path_for("genome").read_bytes() == before
    assert supervisor.store.used_keys("genome") == {"actual-work"}
    empty = make_supervisor(tmp_path / "empty", RecordingWorker(outcome="skipped"))
    for number in range(10):
        empty.dispatch("genome", f"idle-{number}", discard_skipped_poll=True)
    assert empty.status("genome") is None
    assert empty.store.used_keys("genome") == set()
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "actual-work")


def test_uncertain_resident_poll_remains_open(tmp_path):
    worker = RecordingWorker(outcome="unknown", detail="delivery uncertain")
    supervisor = make_supervisor(tmp_path, worker)
    result = supervisor.dispatch("genome", "poll", discard_skipped_poll=True)
    assert result.state.name == "unknown"
    assert supervisor.status("genome").key == "poll"
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "next", discard_skipped_poll=True)


def test_poll_restore_failure_keeps_claim_open(tmp_path, monkeypatch):
    supervisor = make_supervisor(tmp_path, RecordingWorker(outcome="skipped"))
    def fail_restore(*args):
        raise OSError("restore interrupted")
    monkeypatch.setattr(supervisor.store, "restore_after_skipped_poll", fail_restore)
    with pytest.raises(OSError):
        supervisor.dispatch("genome", "poll", discard_skipped_poll=True)
    assert supervisor.status("genome").state.name == "started"
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "next", discard_skipped_poll=True)


def test_an_unknown_dispatch_reconciles_once_and_only_once(tmp_path):
    class Blocking(RecordingWorker):
        def run(self, dispatch):
            self.seen.append(dispatch)
            raise RuntimeError("worker killed")

    supervisor = make_supervisor(tmp_path, Blocking())
    with pytest.raises(RuntimeError):
        supervisor.dispatch("genome", "wake-1")
    closed = supervisor.reconcile("genome", detail="the wall showed no effect")
    assert closed.state.name == "reconciled"
    with pytest.raises(SupervisorError):
        supervisor.reconcile("genome", detail="again")


def test_reconciling_a_named_dispatch_refuses_a_different_key(tmp_path):
    class Blocking(RecordingWorker):
        def run(self, dispatch):
            self.seen.append(dispatch)
            raise RuntimeError("worker killed")

    supervisor = make_supervisor(tmp_path, Blocking())
    with pytest.raises(RuntimeError):
        supervisor.dispatch("genome", "wake-1")
    with pytest.raises(SupervisorError):
        supervisor.reconcile("genome", detail="wrong one", key="wake-9")
    assert supervisor.status("genome").state.name == "unknown"


def test_reconciling_a_finished_dispatch_is_refused(tmp_path):
    supervisor = make_supervisor(tmp_path)
    supervisor.dispatch("genome", "wake-1")
    with pytest.raises(SupervisorError):
        supervisor.reconcile("genome", detail="nothing to reconcile")


def test_a_restarted_supervisor_reads_the_open_dispatch_and_refuses_to_replay(tmp_path):
    class Blocking(RecordingWorker):
        def run(self, dispatch):
            self.seen.append(dispatch)
            raise RuntimeError("worker killed")

    first = make_supervisor(tmp_path, Blocking())
    with pytest.raises(RuntimeError):
        first.dispatch("genome", "wake-1")
    # A fresh supervisor over the same store is the successor's view.
    second = make_supervisor(tmp_path, Blocking())
    assert second.open_dispatch("genome").key == "wake-1"
    with pytest.raises(SupervisorError):
        second.dispatch("genome", "wake-1")
    assert len(second.worker.seen) == 0


def test_a_dispatch_needs_a_durable_key(tmp_path):
    supervisor = make_supervisor(tmp_path)
    with pytest.raises(SupervisorError):
        supervisor.dispatch("genome", "")
