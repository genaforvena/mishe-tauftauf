"""The single scheduler: one timer armed to the earliest thing that is due.

Due things are intention ``due`` times and signal expiry (``seen + max_age``).
Whoever changes either calls ``arm``. When the timer fires, ``due`` expires
silent signals (an edge subscribers are woken by) and arms the next one.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone

from . import intentions, signal_store

TIMER_UNIT = "mishe-next-due"


def target(home) -> float | None:
    times = [t for t in (intentions.next_due(home), signal_store.next_expiry(home)) if t is not None]
    return min(times) if times else None


def systemd_scheduler(epoch: float | None, *, command: list[str]) -> None:
    """Replace the transient next-due timer. ``None`` just removes it."""
    subprocess.run(["systemctl", "--user", "stop", f"{TIMER_UNIT}.timer"],
                   check=False, capture_output=True)
    if epoch is None:
        return
    moment = datetime.fromtimestamp(max(epoch, time.time() + 1), timezone.utc)
    subprocess.run(["systemd-run", "--user", f"--unit={TIMER_UNIT}",
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
