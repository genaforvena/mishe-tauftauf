"""``mishe wake ROLE``: one short-lived, bounded turn for a persistent identity.

The deterministic owed check runs first, so a spurious trigger costs nothing.
Dispatch bookkeeping is the kept ``core`` supervisor: one dispatch at a time,
a durable record, an unknown effect never replayed. A wake that does not yield
leaves its dispatch open; the next wake of that role reconciles it first.
"""
from __future__ import annotations

import os
import sys
import time
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import delivery_requests, dossier, feed, owed, wake_memory, work_receipts, yielding
from .wall import addressed
from .core.dispatch_record import process_alive
from .core.interfaces import RECONCILED, STARTED, UNKNOWN, WorkerResult
from .core.registry import StaticRoleRegistry
from .core.state import DispatchRecord, DispatchStore
from .core.supervisor import DispatchBusy, Supervisor, SupervisorError
from .state_files import read_json, write_json

DEFAULT_TIMEOUT = 1800.0
# Minimum gap between two starts of the same role from resume_owed. 150 s keeps two starts in any
# 300 s window, under the protected host StartLimitBurst of 3, with one start of headroom for path units.
START_SPACING_SECONDS = 150.0
START_LEDGER = "wake-start-ledger.json"
# Bound on one start request, enforced by a `timeout -s KILL` wrapper. The wrapper is a separate process,
# so it can still end the client if the calling wake dies. This bounds the CLIENT only: a job that systemd
# already queued may be accepted after its client is killed (not established either way; see the wall).
START_REQUEST_TIMEOUT_SECONDS = 60.0
# Allowance for the wrapper's own launch latency after request_at is recorded (not measured; a guess).
START_LAUNCH_SLACK_SECONDS = 30.0
# The write-ahead record covers the bound, the launch slack and the spacing (see _start_once).
START_WRITE_AHEAD_SECONDS = START_REQUEST_TIMEOUT_SECONDS + START_LAUNCH_SLACK_SECONDS + START_SPACING_SECONDS


class EngineNotStarted(Exception):
    """The engine command never started, so it had no effect."""


class EngineRejected(EngineNotStarted):
    """The engine ran, but its model refused before any tool call: no effect, and the candidate is unusable now."""


class EngineExhausted(EngineRejected):
    """The account behind the candidate is out of quota (HTTP 402, insufficient balance, usage limit).

    Not a per-model refusal: every role and model on that account fails the same way until its window resets,
    so the router cools the account, not just the candidate for the role.
    """


class EngineRateLimited(EngineRejected):
    """The provider refused with a rate limit (HTTP 429) before any tool call.

    No effect happened, so the wake falls through to the next candidate at once. The cool-off it
    leaves is short and scoped to the role that hit the limit: the same provider may serve
    another role minutes later, and a quota window would be the wrong remedy (operator DM 299311).
    """


class EngineTimeout(Exception):
    """The engine ran past its bound; its effect is unknown."""


@dataclass
class WakeResult:
    outcome: str      # skipped completed reconciled unknown failed blocked busy held
    detail: str = ""


def _key(role: str, now_ns: int) -> str:
    return f"wake-{role}-{now_ns}"


def systemd_starter(role: str) -> bool:
    """Ask systemd to start the wake unit; False when systemd refused (e.g. start-limit-hit).

    A refused start is reported on stderr (the journal of the calling unit) instead of being discarded,
    so a debounce that could not start its wake is a visible failure, and the caller does not record it.
    """
    # The request runs under coreutils `timeout` in a process that is not this caller's child-wait, so the
    # client is ended at START_REQUEST_TIMEOUT_SECONDS if this caller dies. The Python timeout is a belt only.
    argv = ["timeout", "-s", "KILL", str(int(START_REQUEST_TIMEOUT_SECONDS)),
            "systemctl", "--user", "start", "--no-block", f"mishe-wake@{role}.service"]
    result = subprocess.run(argv, check=False, capture_output=True, text=True,
                            timeout=START_REQUEST_TIMEOUT_SECONDS + 5)
    # `timeout -s KILL` also signals its own process group, so the caller observes -9 (a signal death),
    # not 124/137. Every signal death or timeout exit is unresolved, never refused.
    if result.returncode in (124, 137) or result.returncode < 0:
        # The request was ended at its bound (or by a signal): acceptance is unresolved, not refused. The
        # caller keeps the spacing record at the post-request clock (conservative); the role stays owed.
        sys.stderr.write(f"wake start for {role} unresolved after {START_REQUEST_TIMEOUT_SECONDS:.0f} s; "
                         f"spacing kept\n")
        return None
    if result.returncode != 0:
        sys.stderr.write(f"wake start refused for {role} (exit {result.returncode}): {result.stderr}\n")
        return False
    return True


def _start_outcome(starter, role: str) -> str:
    # systemd_starter: True accepted, False refused, None unresolved (timeout or signal death). An
    # unresolved request may still be accepted later, so it is never treated as a refusal or as an
    # acceptance at the post-request clock (field review of 9f1c9c1e: 1001 replaced the 1240 record).
    result = starter(role)
    if result is False:
        return "refused"
    if result is None:
        return "unresolved"
    return "accepted"


def default_mail_reader(home, role: str, after: int) -> list:
    return [(e.sequence, e.body) for e in feed.Feed(Path(home)).role_entries(role)
            if e.sequence > after and addressed(e, role)]


def next_role(home, roles, *, now: float, exclude: str | None = None) -> str | None:
    """Reconcile open effects first; rotate owed work after the last settled role."""
    store = DispatchStore(Path(home) / "dispatch")
    for record in store.stale_open_records():       # a running role's open record is not stale
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


def _ledger_update(home, change=None) -> dict:
    """Read, optionally change, and write the shared start ledger under one lock.

    Path-triggered debounce units, resume_owed and run_wake all write it, so the read-modify-write
    is serialised with an exclusive flock on a sibling lock file (DM 300359: unlocked writers could
    drop each other's starts).
    """
    import fcntl
    path = Path(home) / START_LEDGER
    with open(path.with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            ledger = read_json(path) or {}
            if change is not None:
                # Persist even when the change raises: a start accepted before the failure is real
                # evidence and must not be lost (review of 906a146e: a later exception dropped it).
                try:
                    change(ledger)
                finally:
                    write_json(path, ledger)
            return ledger
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def record_wake_start(home, role: str, *, now: float) -> None:
    """Record that a wake for ``role`` has started, whatever triggered it.

    Path-triggered starts (the debounced units) never pass through ``resume_owed``, so without this
    record a resume could start a fourth unit inside the host's 300 s start limit (DM 300352).
    """
    def _record(ledger: dict) -> None:
        # Only forward: a delayed record carrying an older `now` must not move the clock back below a
        # start already accepted (review of e77d2c1a: 200 accepted, then 100 recorded, next start at 250).
        last = ledger.get(role)
        if last is None or float(last) < now:
            ledger[role] = now

    _ledger_update(home, _record)


def _start_once(home, ledger: dict, role: str, starter, clock) -> tuple[str, float | None]:
    """Check the spacing, request the start and record it. Runs inside the held ledger lock.

    Returns ("start", recorded_time), ("wait", seconds_left) or ("refused", None). The record is
    written AHEAD of the request, so a process that dies inside the starter (review of e77d2c1a:
    os._exit in the second starter) still leaves a spacing record. That write-ahead value is
    request_at + START_WRITE_AHEAD_SECONDS (request + 60 s bound + 150 s spacing). The request is
    killed by its detached `timeout` wrapper no later than request_at + 60, so any acceptance happens at
    or before request_at + 60 even if the caller dies; the next request therefore waits to at least
    acceptance + 150 (review of 5716e061: a record of request_at + 150 let a delayed acceptance at
    +290 be followed by a request at +300, 10 s after it). After acceptance the record is moved to the post-acceptance time,
    which is exact; a refusal restores the previous record, so a refused start is not recorded.
    """
    last = ledger.get(role)
    request_at = clock()
    if last is not None and request_at - float(last) < START_SPACING_SECONDS:
        return "wait", START_SPACING_SECONDS - (request_at - float(last))
    ledger[role] = request_at + START_WRITE_AHEAD_SECONDS
    write_json(Path(home) / START_LEDGER, ledger)
    outcome = _start_outcome(starter, role)
    if outcome == "accepted":
        ledger[role] = max(clock(), request_at)
        return "start", ledger[role]
    if outcome == "unresolved":
        # Keep the forward write-ahead record: a late acceptance must still be inside the spacing.
        return "start", ledger[role]
    if last is None:
        ledger.pop(role, None)
    else:
        ledger[role] = last
    return "refused", None


def _ledger_start(home, role: str, starter) -> str:
    """Start ``role`` only through the shared ledger: spacing check, write-ahead, record.

    run_wake's successor and blocked-branch starts used to call the starter directly, so they
    bypassed the spacing and recorded nothing (review of 8799918d: a successor start could land
    inside 150 s of another start of the same role). A start inside the spacing is not sent: the
    role stays owed, and the next arm or resume starts it, so nothing is dropped and nothing sleeps
    inside a wake. Returns the _start_once outcome: "start", "wait" or "refused".
    """
    decision: dict = {}
    _ledger_update(home, lambda ledger: decision.update(
        result=_start_once(home, ledger, role, starter or systemd_starter, time.time)))
    return decision["result"][0]


def debounced_start(home, role: str, window: float, *, clock=time.time, sleep=time.sleep,
                    starter=None) -> float | None:
    """Wait out the coalescing window, then the start spacing, then start the wake.

    The spacing check, the start request and the ledger record happen in ONE locked ledger change.
    The spacing clock is read inside the lock before the request, and the recorded time is read AFTER
    systemd accepted the request. A recorded time is therefore never earlier than the real request, so
    the next check (reading the clock before its own request) guarantees the real gap between two
    requests is at least START_SPACING_SECONDS (review of 906a146e: a clock read before a slow starter
    let a start land 148 s after the recorded time). Only the sleeps run outside the lock. A refused
    start is NOT recorded, and this raises so the debounce unit fails visibly; the role stays owed and
    the next resume_owed or arm retries it. Returns the time recorded for the accepted start, or None.
    """
    starter = systemd_starter if starter is None else starter
    sleep(window)
    while True:
        decision: dict = {}
        _ledger_update(home, lambda ledger: decision.update(
            result=_start_once(home, ledger, role, starter, clock)))
        kind, value = decision["result"]
        if kind == "start":
            return value
        if kind == "refused":
            raise RuntimeError(f"systemd refused the wake start for {role}; it stays owed for the next arm")
        sleep(value)


def resume_owed(home, roles, *, now: float, clock=time.time, starter=None) -> list[str]:
    """Start a wake unit for every role that is owed work, not just the first.

    A wake that fired while the plant was held exits without running, so nothing
    else re-fires it when the hold is released; every resume path calls this so each
    owed role gets its own start. Starts are non-blocking; the wake lock queues them.
    ``now`` decides which roles are owed; the spacing uses ``clock`` read inside the lock, per role,
    and the recorded time is read after systemd accepted that role's request (see debounced_start).
    """
    starter = systemd_starter if starter is None else starter
    store = DispatchStore(Path(home) / "dispatch")
    started: list[str] = []
    for record in store.stale_open_records():
        if record.role not in started:
            started.append(record.role)
    for role in roles:
        if role not in started and owed.check(home, role, now=now).reasons:
            started.append(role)
    # Every arm calls this, and a producer finishing arms; without spacing one owed role is started
    # on each finish and the host start limit (3 per 300 s, protected) refuses the rest, leaving the
    # wake failed and the role owed again (2026-11-11 genome loop). A throttled role is retried by the
    # next arm after the spacing, so a start is delayed, never dropped while the role stays owed.
    # One locked step per role: each role's accepted start is persisted before the next role's request,
    # so a failure or exit inside a later starter cannot erase an earlier accepted start.
    for role in started:
        _ledger_update(home, lambda ledger, role=role: _start_once(home, ledger, role, starter, clock))
    return started


def _restore_memory_floor(home, role, key) -> dict:
    """Restore this wake's own memory floor and record what the unit ran under.

    A protected host drop-in shadows the generated unit's ``MemoryHigh`` with a
    lower value, and at that throttle the engine stalls until it runs out its
    budget (2026-11-10: five MiniMax wakes timed out at 1800 s; the steward
    unit's cgroup read ``memory.events high 588``). The floor is applied to the
    running unit from inside the wake, so the protected drop-in is not edited.

    The reading is written next to the wake's other evidence so a later failure
    can be told apart from a plain slow engine.
    """
    unit = wake_memory.own_unit_name(role)
    record = wake_memory.ensure_wake_memory_high(unit)
    path = wake_memory.memory_high_path(unit)
    throttle = None
    if path is not None:
        try:
            throttle = (path / "memory.high").read_text(encoding="utf-8")
        except OSError:
            throttle = None
    record["memory_high"] = throttle
    write_json(Path(home) / "wakes" / f"{key}.memory.json", record)
    return record


def _execute(home, role, key, *, engine, now, snap, mail, reconcile_of, repo, timeout, on_start=None):
    """Write the wake's files, run the engine, report whether it yielded."""
    snap.tape_seq = max([owed.tape_cursor(home, role)] + [seq for seq, _ in mail])
    yielding.wakes_dir(home).mkdir(parents=True, exist_ok=True)
    write_json(yielding.snapshot_path(home, key), {"role": role, "snapshot": snap.to_json()})
    _restore_memory_floor(home, role, key)
    write_json(yielding.wakes_dir(home) / f"{key}.work-start.json",
               work_receipts.begin(repo, role=role, key=key, now=now, home=home))
    prompt = yielding.wakes_dir(home) / f"{key}.prompt.md"
    prompt.write_text(dossier.build(home, role, snap, key=key, now=now, mail=mail,
                                    reconcile_of=reconcile_of, repo=repo, timeout=timeout), encoding="utf-8")
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
        if (Path(self.home) / "hold").exists() or delivery_requests.admission_path(self.home).exists():
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
    # Recorded before the held return: a start that only returns "held" still spends the host's
    # start-limit budget, so resume_owed must see it (DM 300359).
    record_wake_start(home, role, now=now)
    if (home / "hold").exists() or delivery_requests.admission_path(home).exists():
        return WakeResult("held", "hold or delivery admission present")
    store = DispatchStore(home / "dispatch")
    supervisor = Supervisor(store=store, registry=StaticRoleRegistry(roles),
                            worker=_Worker(home, engine, now, mail_reader, repo, timeout),
                            lock_timeout=lock_wait)
    try:
        with supervisor.running(role) as release_claim:      # wait for this role's own live wake and a free slot
            release_claim()
    except DispatchBusy:
        return WakeResult("busy", "another wake is running")
    # All contenders must choose the same head, including that head's own wake.
    # Excluding oneself first makes two unknown records defer to each other forever.
    # Only a record whose role is not running blocks: a running wake's open record is normal.
    opened = store.stale_open_records()
    if opened and opened[0].role != role:
        _ledger_start(home, opened[0].role, starter)
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
            _ledger_start(home, following, starter)
    return result


def _reconcile(home, role, record, supervisor, engine, now, mail_reader, repo, timeout) -> WakeResult:
    store = supervisor.store
    with supervisor.running(role) as release_claim:
        release_claim()
        if (Path(home) / "hold").exists() or delivery_requests.admission_path(home).exists():
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
