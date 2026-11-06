"""``mishe wake ROLE``: one short-lived, bounded turn for a persistent identity.

The deterministic owed check runs first, so a spurious trigger costs nothing.
Dispatch bookkeeping is the kept ``core`` supervisor: one dispatch at a time,
a durable record, an unknown effect never replayed. A wake that does not yield
leaves its dispatch open; the next wake of that role reconciles it first.
"""
from __future__ import annotations

import os
import time
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import dossier, owed, yielding
from .core.dispatch_record import process_alive
from .core.interfaces import RECONCILED, STARTED, UNKNOWN, WorkerResult
from .core.registry import StaticRoleRegistry
from .core.state import DispatchRecord, DispatchStore
from .core.supervisor import DispatchBusy, Supervisor, SupervisorError
from .state_files import read_json, write_json

DEFAULT_TIMEOUT = 1800.0


class EngineNotStarted(Exception):
    """The engine command never started, so it had no effect."""


class EngineTimeout(Exception):
    """The engine ran past its bound; its effect is unknown."""


@dataclass
class WakeResult:
    outcome: str      # skipped completed reconciled unknown failed blocked busy held
    detail: str = ""


def _key(role: str, now_ns: int) -> str:
    return f"wake-{role}-{now_ns}"


def systemd_starter(role: str) -> None:
    subprocess.run(["systemctl", "--user", "start", "--no-block", f"mishe-wake@{role}.service"],
                   check=False, capture_output=True)


def default_mail_reader(home, role: str, after: int) -> list:
    from .feed import Feed
    from .wall import addressed
    return [(e.sequence, e.body) for e in Feed(Path(home)).role_entries(role)
            if e.sequence > after and addressed(e, role)]


def next_role(home, roles, *, now: float, exclude: str | None = None) -> str | None:
    """Reconcile open effects first; rotate owed work after the last settled role."""
    store = DispatchStore(Path(home) / "dispatch")
    for record in store.open_records():
        if record.role != exclude:
            return record.role
    ordered = list(roles)
    if exclude in ordered:
        offset = ordered.index(exclude) + 1
        ordered = ordered[offset:] + ordered[:offset]
    for role in ordered:
        if role != exclude and owed.check(home, role, now=now).reasons:
            return role
    return None


def _execute(home, role, key, *, engine, now, snap, mail, reconcile_of, repo, timeout, on_start=None):
    """Write the wake's files, run the engine, report whether it yielded."""
    snap.tape_seq = max([owed.tape_cursor(home, role)] + [seq for seq, _ in mail])
    yielding.wakes_dir(home).mkdir(parents=True, exist_ok=True)
    write_json(yielding.snapshot_path(home, key), {"role": role, "snapshot": snap.to_json()})
    from . import work_receipts
    write_json(yielding.wakes_dir(home) / f"{key}.work-start.json",
               work_receipts.begin(repo, role=role, key=key, now=now, home=home))
    prompt = yielding.wakes_dir(home) / f"{key}.prompt.md"
    prompt.write_text(dossier.build(home, role, snap, key=key, now=now, mail=mail,
                                    reconcile_of=reconcile_of, repo=repo), encoding="utf-8")
    env = dict(os.environ, MISHE_SEED_HOME=str(home), MISHE_ROLE=role, MISHE_WAKE_KEY=key)
    engine_started = time.monotonic()
    try:
        code = engine.run(prompt, env=env, cwd=repo or Path(home), timeout=timeout, on_start=on_start)
    except EngineNotStarted as exc:
        return "failed", f"engine did not start: {exc}"
    except EngineTimeout as exc:
        return "unknown", f"engine timed out: {exc}"
    finally:
        work_receipts.complete(home, key, now=time.time(), engine_seconds=time.monotonic() - engine_started)
    if yielding.receipt_path(home, key).exists():
        return "yielded", f"engine exit {code}"
    return "unknown", f"engine exit {code} without a yield"


class _Worker:
    def __init__(self, home, engine, now, mail_reader, repo, timeout):
        self.home, self.engine, self.now = home, engine, now
        self.mail_reader, self.repo, self.timeout = mail_reader, repo, timeout

    def run(self, dispatch):
        role, key = dispatch.role, dispatch.key
        from .delivery_requests import admission_path
        if (Path(self.home) / "hold").exists() or admission_path(self.home).exists():
            return WorkerResult("skipped", "delivery admission or hold arrived while waiting")
        snap = owed.check(self.home, role, now=self.now)
        if not snap.reasons:
            return WorkerResult("skipped", "nothing owed")
        mail = self.mail_reader(self.home, role, owed.tape_cursor(self.home, role))
        state, detail = _execute(self.home, role, key, engine=self.engine, now=self.now, snap=snap,
                                 mail=mail, reconcile_of=None, repo=self.repo, timeout=self.timeout,
                                 on_start=dispatch.payload.get("publish_pid"))
        return WorkerResult({"yielded": "completed"}.get(state, state), detail)


def run_wake(home, role: str, *, roles, engine, now: float, mail_reader=default_mail_reader,
             starter=systemd_starter, repo: Path | None = None, timeout: float = DEFAULT_TIMEOUT,
             lock_wait: float = 0.0) -> WakeResult:
    """``lock_wait`` bounds how long this wake waits for a live wake to release the lock.

    The default refuses at once (``busy``). The units pass the wake timeout so a path
    trigger that lands during another wake waits for it and then gets its own turn,
    instead of exiting 0 with the owed wake dropped.
    """
    home = Path(home)
    from . import delivery_requests
    if (home / "hold").exists() or delivery_requests.admission_path(home).exists():
        return WakeResult("held", "hold or delivery admission present")
    store = DispatchStore(home / "dispatch")
    supervisor = Supervisor(store=store, registry=StaticRoleRegistry(roles),
                            worker=_Worker(home, engine, now, mail_reader, repo, timeout),
                            lock_timeout=lock_wait)
    try:
        with supervisor.exclusive():      # a live wake holds this; its open record is not stale
            pass
    except DispatchBusy:
        return WakeResult("busy", "another wake is running")
    # All contenders must choose the same head, including that head's own wake.
    # Excluding oneself first makes two unknown records defer to each other forever.
    opened = store.open_records()
    if opened and opened[0].role != role:
        starter(opened[0].role)
        return WakeResult("blocked", f"{opened[0].role} has an open dispatch to reconcile first")
    mine = store.read(role)
    try:
        if mine is not None and mine.state in (UNKNOWN, STARTED):
            result = _reconcile(home, role, mine, supervisor, engine, now, mail_reader, repo, timeout)
        else:
            record = supervisor.dispatch(role, _key(role, int(now * 1e9) + os.getpid()),
                                         discard_skipped_poll=True)
            result = WakeResult(record.state.name, record.detail)
    except DispatchBusy:
        return WakeResult("busy", "another wake is running")
    except SupervisorError as exc:
        return WakeResult("blocked", str(exc))
    # The engine has exited and the supervisor has persisted settlement and
    # released its lock. A delivery requested by this wake can now acquire it.
    if result.outcome in ("completed", "reconciled"):
        delivery_requests.consume(home)
    if result.outcome != "skipped":
        following = next_role(home, roles, now=now, exclude=role)
        if following:
            starter(following)
    return result


def _reconcile(home, role, record, supervisor, engine, now, mail_reader, repo, timeout) -> WakeResult:
    store = supervisor.store
    with supervisor.exclusive():
        from .delivery_requests import admission_path
        if (Path(home) / "hold").exists() or admission_path(home).exists():
            return WakeResult("held", "hold or delivery admission arrived while waiting")
        if record.pid is not None and process_alive(record.pid):
            return WakeResult("blocked", f"worker pid {record.pid} still alive")
        snap = owed.check(home, role, now=now)
        mail = mail_reader(home, role, owed.tape_cursor(home, role))
        key = _key(role, int(now * 1e9) + os.getpid())
        state, detail = _execute(home, role, key, engine=engine, now=now, snap=snap, mail=mail,
                                 reconcile_of=record, repo=repo, timeout=timeout,
                                 on_start=lambda pid: store.write(DispatchRecord(
                                     role=role, key=record.key, state=record.state,
                                     detail=record.detail, pid=pid, attempt=record.attempt)))
        if state == "yielded":
            store.write(DispatchRecord(role=role, key=record.key, state=RECONCILED,
                                       detail=f"reconciled by {key}", pid=record.pid, attempt=record.attempt + 1))
            return WakeResult("reconciled", f"reconciled by {key}")
        store.write(DispatchRecord(role=role, key=record.key, state=UNKNOWN,
                                   detail=f"{record.detail}; reconcile wake {key}: {detail}",
                                   pid=None, attempt=record.attempt + 1))
        return WakeResult("unknown", detail)
