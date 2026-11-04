"""The single failure target: any failed wake, producer or doctor run lands here.

A failure is a RED signal (a record that goes UNKNOWN if nobody clears it), an
inbox touch plus a message for the resolver, and a re-offer intention so a
standing fault returns to its resolver at a bounded interval until cleared.
"""
from __future__ import annotations

from pathlib import Path

from . import intentions, owed, signal_store, units
from .state_files import read_json

DEFAULT_RESOLVER = "health"
FALLBACK_RESOLVER = "genome"
SIGNAL_MAX_AGE = 7 * 86400
REOFFER_SECONDS = 3600


def resolver_for(home, unit: str) -> str:
    configured = {**(read_json(Path(home) / "resolvers.generated.json") or {}),
                  **(read_json(Path(home) / "resolvers.json") or {})}     # the owner's file wins
    if unit in configured:
        return configured[unit]
    if units.is_health_wake(unit):
        return FALLBACK_RESOLVER             # health cannot be its own resolver
    return DEFAULT_RESOLVER


def default_notify(home, role: str, text: str) -> None:
    from . import wall
    wall.message(Path(home), "mishe", role, text)


def report(home, unit: str, *, now: float, notify=None) -> str:
    home = Path(home)
    name, role = units.signal_name(unit), resolver_for(home, unit)
    if name not in signal_store.registry(home):
        signal_store.register(home, name, max_age=SIGNAL_MAX_AGE, subscribers=[role],
                              reoffer=REOFFER_SECONDS)
    signal_store.observe(home, name, "RED", now=now, source="mishe-failed")
    intentions.add(home, role, f"reoffer-{name}", note=f"{unit} is still failing; investigate or clear signal {name}",
                   due=now + REOFFER_SECONDS)
    text = (f"{unit} failed. Signal {name} is RED. Find the cause (journalctl --user -u {unit}), "
            f"fix or disposition it, then `mishe signal observe {name} GREEN`.")
    (notify or (lambda r, t: default_notify(home, r, t)))(role, text)
    owed.touch_inbox(home, role)
    return role
