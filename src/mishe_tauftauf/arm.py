"""The single scheduler: one timer armed to the earliest thing that is due.

Due things are intention ``due`` times and signal expiry (``seen + max_age``).
Whoever changes either calls ``arm``. When the timer fires, ``due`` expires
silent signals (an edge subscribers are woken by) and arms the next one.
"""
from __future__ import annotations

import subprocess
import time
import uuid
from datetime import datetime, timezone

from . import intentions, signal_store, units

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


def systemd_scheduler(epoch: float | None, *, command: list[str]) -> None:
    """Replace the transient next-due timer. ``None`` just removes it."""
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
    subprocess.run(["systemd-run", "--user", f"--unit={unit}",
                    f"--on-calendar={moment:%Y-%m-%d %H:%M:%S} UTC",
                    "--timer-property=AccuracySec=1s", "--collect", *command],
                   check=True, capture_output=True)


def arm(home, *, scheduler) -> float | None:
    when = target(home)
    scheduler(when)
    return when


def due(home, *, now: float, scheduler, roles=(), starter=None) -> list[str]:
    """Timer handler: expire silent signals, wake an owed role, re-arm."""
    changed = signal_store.expire(home, now=now)
    if starter is not None:
        from . import owed
        for role in roles:
            if owed.check(home, role, now=now).reasons:
                starter(role)
                break
    arm(home, scheduler=scheduler)
    return changed
