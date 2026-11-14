"""The single scheduler: one timer armed to the earliest thing that is due.

Due things are intention ``due`` times and signal expiry (``seen + max_age``).
Whoever changes either calls ``arm``. When the timer fires, ``due`` expires
silent signals (an edge subscribers are woken by) and arms the next one.
"""
from __future__ import annotations

import fcntl
import subprocess
from contextlib import contextmanager
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone

from . import intentions, signal_store, units
from .timestamp_text import decode_listed_next_instants

TIMER_UNIT = "mishe-next-due"
# Each arm gets its own transient unit name. The due handler is itself a running
# unit; re-arming under the same name collides with it (systemd-run rc=1).
TIMER_GLOB = f"{TIMER_UNIT}-*.timer"
# An already-overdue target (after an outage, or while wakes are blocked) is retried on a backoff. Re-arming it
# for now+1 s made the due handler fire every second and ask for a wake each time; the host start limit
# (3 per 300 s) then refused them, left the wake units failed and the owed minds unwakeable.
OVERDUE_RETRY_SECONDS = 120.0


def target(home) -> float | None:
    times = [t for t in (intentions.next_due(home), signal_store.next_expiry(home)) if t is not None]
    return min(times) if times else None


def _pending_timers() -> list[str]:
    """Names of earlier next-due timers (stopping a timer leaves a running service alone)."""
    return sorted(set(units.user_timer_names(TIMER_GLOB) + [f"{TIMER_UNIT}.timer"]))


@contextmanager
def _replacement_lock(lock: Path | None):
    """Serialise the stop-then-start replacement across processes.

    Without it two arms interleave: A starts its fresh transient timer (the start job is
    pending, so ``systemctl list-units --type=timer --all`` already shows it) and B's
    ``systemctl stop`` cancels A's pending start job, so A's ``systemd-run`` returns rc=1
    and the producer that armed fails (2026-11-11 00:52:46Z delivery-requests, 01:50:07Z
    household-daylight). ``None`` keeps callers that never touch systemd lock-free.
    """
    if lock is None:
        yield
        return
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _retry_due_within_window(now: float) -> bool:
    """True when a pending next-due timer already fires within the overdue retry window.

    Parsed from ``systemctl list-timers`` NEXT column (``Sat 2026-11-14 05:10:58 UTC``).
    An unparseable line is skipped, so the caller falls back to replacing the timer.
    """
    listed = subprocess.run(["systemctl", "--user", "list-timers", "--all", "--plain", "--no-legend",
                             TIMER_GLOB], check=False, capture_output=True, text=True).stdout
    return any(nxt.timestamp() <= now + OVERDUE_RETRY_SECONDS
               for nxt in decode_listed_next_instants(listed))


def _source_root() -> str:
    return str(Path(__file__).resolve().parent.parent)


def _armed_by_this_release(timers: list[str]) -> bool:
    """True when every pending next-due timer's service runs this release's source.

    A timer keeps the PYTHONPATH of the release that armed it until it fires, so after a pin move
    an old-release timer would run old code. A retry in the window is only kept when it is ours.
    """
    root = _source_root()
    for timer in timers:
        service = subprocess.run(["systemctl", "--user", "show", timer, "-p", "Triggers", "--value"],
                                 check=False, capture_output=True, text=True).stdout.strip()
        if not service:
            continue  # no such unit under that name
        env = subprocess.run(["systemctl", "--user", "show", service, "-p", "Environment", "--value"],
                             check=False, capture_output=True, text=True).stdout.split()
        if f"PYTHONPATH={root}" not in env:
            return False
    return True


def systemd_scheduler(epoch: float | None, *, command: list[str],
                      lock: Path | None = None) -> None:
    """Replace the transient next-due timer. ``None`` just removes it."""
    with _replacement_lock(lock):
        # An overdue target already has a retry pending inside the backoff window: keep it.
        # Replacing it on every arm pushed the retry out again and it never fired (2026-11-14 churn).
        # Only when that retry was armed by this release: an old-pin timer must be replaced (c14bac1f move observe failure).
        if (epoch is not None and epoch <= time.time() and _retry_due_within_window(time.time())
                and _armed_by_this_release(_pending_timers())):
            return
        for timer in _pending_timers():
            subprocess.run(["systemctl", "--user", "stop", timer],
                           check=False, capture_output=True)
        if epoch is None:
            return
        now = time.time()
        # A genuinely upcoming time keeps its precision (at least a second out); one that is already overdue backs off.
        when = max(epoch, now + 1) if epoch > now else now + OVERDUE_RETRY_SECONDS
        moment = datetime.fromtimestamp(when, timezone.utc)
        unit = f"{TIMER_UNIT}-{uuid.uuid4().hex[:12]}"
        result = subprocess.run(["systemd-run", "--user", f"--unit={unit}",
                                 "--slice=mishe-plants.slice",
                                 "--setenv=PYTHONPATH=" + str(Path(__file__).resolve().parent.parent),
                                 f"--on-calendar={moment:%Y-%m-%d %H:%M:%S} UTC",
                                 "--timer-property=AccuracySec=1s", "--collect", *command],
                                capture_output=True)
        # The manager error text is the only way to diagnose a refused arm (DM 299366):
        # without it a failed arm looks like a generic producer failure.
        if result.returncode != 0:
            raise RuntimeError(f"systemd-run rc={result.returncode} unit={unit}: {units.manager_error(result)}")


def arm(home, *, scheduler) -> float | None:
    when = target(home)
    scheduler(when)
    return when


def due(home, *, now: float, scheduler, roles=(), starter=None) -> list[str]:
    """Timer handler: expire silent signals, wake an owed role, re-arm."""
    from . import work_state
    from pathlib import Path
    work_state.sweep(home, repo=Path(home).parent, now=now)
    changed = signal_store.expire(home, now=now)
    if starter is not None:
        from . import owed
        for role in roles:
            if owed.check(home, role, now=now).reasons:
                starter(role)
                break
    arm(home, scheduler=scheduler)
    return changed
