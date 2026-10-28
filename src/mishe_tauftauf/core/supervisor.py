"""The supervisor: one role at a time, one durable record each, no replay.

The rules it enforces are the whole point:

* only a declared role is dispatched;
* one dispatch at a time across all roles, so two workers never share a wake;
* a dispatch whose outcome was not recorded stays open and blocks the next
  attempt, because its effect may have happened;
* an open dispatch is reconciled explicitly, never by retried.

The "one at a time" rule is held by a lock that spans the whole
check-and-claim, so two processes cannot both decide that no dispatch is
open and then each start one.
"""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Sequence

from .dispatch_record import process_alive as _process_alive
from .interfaces import (COMPLETED, FAILED, RECONCILED, SKIPPED, STARTED,
                         UNKNOWN, Dispatch, Observation, RoleRegistry, Worker)
from .registry import check_registry
from .state import DispatchRecord, DispatchStore


class SupervisorError(RuntimeError):
    """The supervisor refused to act, and says why."""


class DispatchBusy(SupervisorError):
    """Another process holds the dispatch lock."""


class Supervisor:
    """Dispatch one role's work at a time through a durable record.

    The supervisor owns no protocol.  What a dispatch means, what the worker
    does with it and how the result becomes a canonical record are the
    adapter's business; the supervisor owns the ordering and the refusal
    rules.
    """

    def __init__(self, *, store: DispatchStore, registry: RoleRegistry,
                 worker: Worker, observer=None, lock_timeout: float = 5.0):
        check_registry(registry)
        self.store = store
        self.registry = registry
        self.worker = worker
        self.observer = observer
        self.lock_timeout = lock_timeout

    def status(self, role: str) -> DispatchRecord | None:
        """The record a successor reads for ``role``."""
        return self.store.read(role)

    def open_dispatch(self, role: str | None = None) -> DispatchRecord | None:
        """The record that still holds an unresolved effect, if any.

        A record in ``started`` state whose worker is still running is
        open: it names work that may have had an effect, so it blocks the
        next dispatch until somebody checks it.
        """
        if role is not None:
            record = self.store.read(role)
            return record if record is not None and record.state in {STARTED, UNKNOWN} else None
        open_records = self.store.open_records()
        return open_records[0] if open_records else None

    @contextmanager
    def _claim(self):
        """Hold the dispatch lock across the whole check-and-claim.

        The lock is a file lock, so it is held across processes and not only
        within one.  It is released when the block ends, including when the
        block raises.

        The wait is bounded by ``lock_timeout``: a caller that cannot get the
        lock is refused rather than waiting forever, because a supervisor
        that blocks here is a supervisor that stops observing.
        """
        path = self.store.lock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.lock_timeout
        with path.open("a+") as handle:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise DispatchBusy(
                            "another process holds the dispatch lock; one mind runs at a time"
                        ) from exc
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def dispatch(self, role: str, key: str, payload: dict | None = None) -> DispatchRecord:
        """Run one dispatch for ``role``.

        Refuses when the role is not declared, when an earlier dispatch
        anywhere is still open, or when the durable record cannot be read.
        The refusal is explicit: the caller must reconcile, not retry.

        The lock is held for the whole dispatch, including the worker's
        run.  One mind at a time means one *running* mind at a time: if the
        lock were released while the worker ran, a second role could start
        a turn beside it.
        """
        if not self.registry.knows(role):
            raise SupervisorError(f"role {role!r} is not declared for this supervisor")
        if not key:
            raise SupervisorError("a dispatch needs a durable key")
        with self._claim():
            # Every role's record is read, not just this role's: one active
            # mind at a time is a plant-wide rule, so an open dispatch for
            # another role blocks this one too.  The check and the claim are
            # under one lock, so two processes cannot both see "nothing open"
            # and each start a turn.
            for record in self.store.records():
                if record.state in {STARTED, UNKNOWN}:
                    raise SupervisorError(
                        f"dispatch {record.key!r} for role {record.role!r} is {record.state.name}; "
                        "reconcile it before dispatching")
            existing = self.store.read(role)
            # A key names one piece of work, for the whole history of the
            # role and not only for its latest record.  Dispatching a key
            # that was used before would run that work again.
            if key in self.store.used_keys(role):
                raise SupervisorError(
                    f"dispatch {key!r} for role {role!r} has already been "
                    "dispatched; a new dispatch needs a new key")
            attempt = (existing.attempt + 1) if existing is not None else 1
            record = DispatchRecord(role=role, key=key, state=STARTED, attempt=attempt)
            self.store.write(record)
            request = Dispatch(role=role, key=key, payload=dict(payload or {}))
            # The worker's pid is written into the record as soon as the
            # worker exists, so a successor that reads the record while the
            # worker runs can find it and refuse to sit beside it.
            def publish(pid: int) -> None:
                current = self.store.read(role)
                if current is None or current.state != STARTED:
                    return
                self.store.write(DispatchRecord(role=role, key=key, state=STARTED,
                                                detail=current.detail, pid=pid,
                                                attempt=attempt))
            request = Dispatch(role=role, key=key,
                               payload=dict(payload or {}, publish_pid=publish))
            try:
                result = self.worker.run(request)
            except BaseException as exc:
                # The worker raised.  Its effect is unknown, not absent: the
                # record stays open so a successor reconciles instead of replaying.
                current = self.store.read(role)
                record = DispatchRecord(role=role, key=key, state=UNKNOWN,
                                        detail=f"worker raised: {exc}",
                                        pid=current.pid if current is not None else None,
                                        attempt=attempt)
                self.store.write(record)
                raise
            state = {"completed": COMPLETED, "failed": FAILED, "skipped": SKIPPED,
                     "unknown": UNKNOWN}[result.outcome]
            record = DispatchRecord(role=role, key=key, state=state,
                                    detail=result.detail, pid=result.pid,
                                    attempt=attempt)
            self.store.write(record)
            return record

    def reconcile(self, role: str, *, detail: str, key: str | None = None) -> DispatchRecord:
        """Close an open dispatch after a human or an adapter checked it.

        This is the only way out of ``started`` or ``unknown``.  It records
        what was found; it never re-runs the work.

        The reconciliation is under the dispatch lock.  Without it, an
        operator could close a dispatch whose worker was still running, and
        the next role would start a turn beside it — two minds on the plant
        at once, which is the rule the lock exists to hold.
        """
        if not detail:
            raise SupervisorError("a reconciliation must say what was checked")
        with self._claim():
            record = self.store.read(role)
            if record is None:
                raise SupervisorError(f"there is no dispatch to reconcile for role {role!r}")
            if record.state not in {STARTED, UNKNOWN}:
                raise SupervisorError(
                    f"dispatch {record.key!r} is {record.state.name}; only an open dispatch reconciles")
            if key is not None and key != record.key:
                raise SupervisorError(
                    f"dispatch {record.key!r} is not the dispatch named {key!r}")
            if record.pid is not None and _process_alive(record.pid):
                # The worker is still running.  Closing the record now would
                # let the next dispatch start a second worker beside it.
                raise SupervisorError(
                    f"dispatch {record.key!r} still has a live worker "
                    f"(pid {record.pid}); stop it before reconciling")
            closed = DispatchRecord(role=record.role, key=record.key, state=RECONCILED,
                                    detail=detail, pid=record.pid, attempt=record.attempt)
            self.store.write(closed)
        return closed

    def observe(self) -> Observation:
        """The current reading of whether the supervisor's state is trustworthy.

        With no observation source there is no evidence, so the reading is
        UNKNOWN — never healthy.  A missing detector is not a passing
        detector.
        """
        if self.observer is None:
            return Observation(fresh=False, healthy=False,
                               detail="no observation source is configured")
        return self.observer()
