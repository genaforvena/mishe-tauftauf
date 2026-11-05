"""The single failure target: any failed wake, producer or doctor run lands here.

A failure is a RED signal (a record that goes UNKNOWN if nobody clears it), an
inbox touch plus a message for the resolver, and a re-offer intention so a
standing fault returns to its resolver at a bounded interval until cleared.
"""
from __future__ import annotations

from pathlib import Path

from . import intentions, owed, signal_store, units
from .state_files import read_json, write_json

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


def report(home, unit: str, *, now: float, notify=None, signature="unit-failed") -> str:
    home = Path(home)
    name, role = units.signal_name(unit), resolver_for(home, unit)
    from . import work_state, dispositions, roles
    if role not in roles.declared(home) and roles.declared(home):
        role = "steward" if "steward" in roles.declared(home) else FALLBACK_RESOLVER
    incident_path = home / "work/incidents" / f"{name}.json"
    try:
        previous = read_json(incident_path) or {}
    except (OSError, ValueError):
        previous = {}
    fresh = previous.get("signature", "unit-failed") != signature
    current = signal_store.read(home, name, now=now) if name in signal_store.registry(home) else None
    try:
        experiment = read_json(home/"work/experiment/manifest.json")
    except (OSError, ValueError):
        experiment = None
    if isinstance(experiment, dict) and experiment.get("fault_events_version") == 1 and experiment["start"] <= now < experiment["end"]:
        novel = fresh or current is None or current.state != signal_store.OK or current.value != "RED"
        write_json(home/"work/fault-events"/f"{int(now*1e9)}-{name}.json",
                   dict(version=1, at=now, unit=unit, role=role, signature=signature, novel=novel))
    write_json(incident_path, dict(signature=signature, count=previous.get("count", 0) + 1,
                                  first_seen=previous.get("first_seen", now), last_seen=now))
    # A standing fault is announced once. While its signal is already RED and a re-offer is still pending, every
    # further failed run (a producer that fails each minute) adds nothing: the re-offer returns it to the resolver at
    # the bounded interval. Without this each failure appended a DM and woke the resolver every couple of minutes.
    if name in signal_store.registry(home):
        current = signal_store.read(home, name, now=now)
        pending = [i for i in intentions.list_(home, role) if i.get("id") == f"reoffer-{name}" and i.get("due", 0) > now]
        acknowledged = work_state.incident_wait(home, role, name, now=now, repo=home.parent)
        known = dispositions.active(home, name, now=now)
        if current.state == signal_store.OK and current.value == "RED" and not fresh and (pending or acknowledged or (known and known["kind"] == "known")):
            return role
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
