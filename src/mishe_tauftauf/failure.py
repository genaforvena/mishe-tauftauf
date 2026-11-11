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


def resolver_role(home, unit: str) -> str:
    """The role ``report`` announces to, mirroring its undeclared-role fallback so the re-offer it adds and the
    re-offer ``clear_recovered`` removes are the same file."""
    from . import roles
    role = resolver_for(home, unit)
    if role not in roles.declared(home) and roles.declared(home):
        role = "steward" if "steward" in roles.declared(home) else FALLBACK_RESOLVER
    return role


def refusal(unit: str) -> str | None:
    """Why ``unit`` may not be recorded as a failure, or None. A signal name is not a unit: recording
    ``failed-mishe-doctor-service`` as a unit minted ``failed-failed-...`` signals and re-offers (2026-11-07)."""
    from . import work_boundary
    if work_boundary.unit_load_state(unit) == "not-found":
        return f"refused: {unit} is not a systemd unit (LoadState=not-found); nothing recorded"
    return None


def _retire_reoffers(home, name: str) -> None:
    """Drop ``reoffer-<name>`` wherever it sits. It can be minted under the signal's resolver (``report``) or under a
    subscriber role (``yielding.settle``); a GREEN failure signal leaves neither a standing fault to return, so both
    are residue. A later failed run re-adds it, so this cannot lose a live fault."""
    for role_dir in (Path(home) / "intentions").glob("*"):
        intentions.remove(home, role_dir.name, f"reoffer-{name}")


def clear_recovered(home, *, now, last_exit=None) -> list[str]:
    """Observe GREEN every RED failure signal whose unit has since exited 0 after the RED reading.

    A failure is a level, so the record must follow the unit: without this a unit that failed once stayed RED after
    its next run succeeded (2026-11-08: nine cleared by hand). Only the unit's latest exit decides; a unit that is still
    failing, or whose exit time is unreadable, stays RED. An incident written before ``unit`` was recorded is left alone.

    Recovery also retires the pending ``reoffer-<name>`` intention: it was scheduled to return a still-standing fault,
    and once the signal is GREEN it only fires a spurious wake (2026-11-10: a recovered pub service still woke Health).
    A signal that was already GREEN when this ran — recovered by a path that predates that retirement (the 2026-11-10
    ``failed-mishe-doctor-service`` residue, and the same shape under ``failed-mishe-producer-*-service``) — carries the
    same spurious re-offer and is retired here too, so a stale re-offer cannot outlive the next sweep.
    """
    from . import work_boundary
    home = Path(home)
    last_exit = last_exit or work_boundary.unit_last_exit
    cleared = []
    for name in sorted(signal_store.registry(home)):
        if not work_boundary.failure_signal(name):
            continue
        reading = signal_store.recorded(home, name)
        if not reading or reading.get("state") != signal_store.OK:
            continue
        if reading.get("value") == "GREEN":
            _retire_reoffers(home, name)     # already recovered: any re-offer is residue, not a standing fault
            continue
        if reading.get("value") != "RED":
            continue
        incident = read_json(home / "work/incidents" / f"{name}.json")
        unit = incident.get("unit") if isinstance(incident, dict) else None
        if not isinstance(unit, str):
            continue
        exit_ = last_exit(unit)
        observed = reading.get("observed_at")
        if exit_ and exit_[1] and isinstance(observed, (int, float)) and exit_[0] > observed:
            signal_store.observe(home, name, "GREEN", now=now, source="failure-recovered")
            _retire_reoffers(home, name)
            cleared.append(name)
    return cleared


def default_notify(home, role: str, text: str) -> None:
    from . import wall
    wall.message(Path(home), "mishe", role, text)


def report(home, unit: str, *, now: float, notify=None, signature="unit-failed") -> str:
    home = Path(home)
    name, role = units.signal_name(unit), resolver_role(home, unit)
    from . import work_state, dispositions
    incident_path = home / "work/incidents" / f"{name}.json"
    accounting_error = None
    try:
        previous = read_json(incident_path) or {}
        if not isinstance(previous, dict) or not isinstance(previous.get("count", 0), int):
            raise ValueError("malformed incident accounting")
    except (OSError, ValueError) as exc:
        previous = {}
        accounting_error = str(exc)
    # Every distinct failure mode this incident has shown. A unit that alternates between two modes (exit-code, then
    # start-limit-hit, then exit-code) must not look new on each alternation: 2026-11-05 host-capacity did exactly that
    # and re-announced itself to Health every two minutes although its signal stayed RED with a re-offer pending.
    seen_modes = previous.get("signatures") if isinstance(previous.get("signatures"), list) else []
    seen_modes = [m for m in seen_modes if isinstance(m, str)] or ([previous["signature"]] if isinstance(previous.get("signature"), str) else [])
    unseen = signature not in seen_modes and (previous.get("signature", "unit-failed") != signature or not seen_modes)
    current = signal_store.read(home, name, now=now) if name in signal_store.registry(home) else None
    try:
        if accounting_error:
            raise ValueError(accounting_error)  # retain malformed original bytes
        experiment = read_json(home/"work/experiment/manifest.json")
        if isinstance(experiment, dict) and experiment.get("fault_events_version") == 1:
            if not all(isinstance(experiment.get(f), (int, float)) for f in ("start", "end")):
                raise ValueError("malformed experiment accounting window")
            if experiment["start"] <= now < experiment["end"]:
                novel = unseen or current is None or current.state != signal_store.OK or current.value != "RED"
                write_json(home/"work/fault-events"/f"{int(now*1e9)}-{name}.json",
                           dict(version=1, at=now, unit=unit, role=role, signature=signature, novel=novel))
        write_json(incident_path, dict(unit=unit, signature=signature, signatures=(seen_modes + [signature] if signature not in seen_modes else seen_modes),
                                      count=previous.get("count", 0) + 1, first_seen=previous.get("first_seen", now), last_seen=now))
    except (OSError, ValueError, TypeError) as exc:
        accounting_error = str(exc)
    if accounting_error:
        gap = "work-accounting"
        if gap not in signal_store.registry(home):
            signal_store.register(home, gap, max_age=300, subscribers=["genome"])
        edge = signal_store.observe(home, gap, "RED", now=now, source="failure-accounting")
        if edge:
            (notify or (lambda r, t: default_notify(home, r, t)))("genome", "Work accounting UNKNOWN; primary failure routing continues. Repair within six hours: " + accounting_error)
            owed.touch_inbox(home, "genome")
            intentions.add(home, "genome", "work-accounting", due=now+21600, note="Repair lost accounting coverage; preserve original primary fault")
    # A standing fault is announced once. While its signal is already RED and a re-offer is still pending, every
    # further failed run (a producer that fails each minute) adds nothing: the re-offer returns it to the resolver at
    # the bounded interval. Without this each failure appended a DM and woke the resolver every couple of minutes.
    if name in signal_store.registry(home):
        current = signal_store.read(home, name, now=now)
        pending = [i for i in intentions.list_(home, role) if i.get("id") == f"reoffer-{name}" and i.get("due", 0) > now]
        acknowledged = work_state.incident_wait(home, role, name, now=now, repo=home.parent)
        known = dispositions.active(home, name, now=now)
        if current.state == signal_store.OK and current.value == "RED" and not unseen and (pending or acknowledged or (known and known["kind"] == "known")):
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
