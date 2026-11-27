"""The single failure target: any failed wake, producer or doctor run lands here.

A failure is a RED signal (a record that goes UNKNOWN if nobody clears it), an
inbox touch plus a message for the resolver, and a re-offer intention so a
standing fault returns to its resolver at a bounded interval until cleared.
"""
from __future__ import annotations

from pathlib import Path

from . import intentions, owed, revive_boundary, signal_store, units, wall_text
from .state_files import read_json, write_json

DEFAULT_RESOLVER = "health"
FALLBACK_RESOLVER = "genome"
SIGNAL_MAX_AGE = 7 * 86400
REOFFER_SECONDS = 3600


def resolver_for(home, unit: str, *, configured=None) -> str:
    """The role that resolves ``unit``'s failure. ``configured`` is the declared unit→role map when the
    caller already computed it (``duties.resolvers``); otherwise the site's files are read, owner's wins."""
    if configured is None:
        configured = read_json(Path(home) / "resolvers.generated.json") or {}
    configured = {**configured, **(read_json(Path(home) / "resolvers.json") or {})}   # the owner's file wins
    if unit in configured:
        return configured[unit]
    if units.is_health_wake(unit):
        return FALLBACK_RESOLVER             # health cannot be its own resolver
    return DEFAULT_RESOLVER


def resolver_role(home, unit: str, *, declared=None, configured=None) -> str:
    """The role ``report`` announces to, mirroring its undeclared-role fallback so the re-offer it adds and the
    re-offer ``clear_recovered`` removes are the same file. ``declared`` and ``configured`` let a caller that
    already holds the roster and the resolver map render a generated unit without re-reading the site
    (``units.failure_watch`` does, so a unit's path is watched before that unit has ever failed)."""
    from . import roles
    role = resolver_for(home, unit, configured=configured)
    declared = roles.declared(home) if declared is None else declared
    if role not in declared and declared:
        role = "steward" if "steward" in declared else FALLBACK_RESOLVER
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


def _unit_for(name: str, incident) -> str | None:
    """The unit a failure signal records. An incident written before ``unit`` was recorded (2026-11-13: work-trial and
    work-watch) or with no incident file at all (dispatch) still names its unit in the signal: ``failed-mishe-producer-X-service``
    is ``mishe-producer-X.service``. The derived name is accepted only when ``units.signal_name`` maps it back to ``name``,
    so a lossy mapping cannot clear a different unit."""
    unit = incident.get("unit") if isinstance(incident, dict) else None
    if isinstance(unit, str):
        return unit
    return units.producer_unit_for_signal(name)


RECOVERY_STABLE_S = 600   # a restarted unit must stay active this long before its failure record clears (no crash loops)


def clear_recovered(home, *, now, last_exit=None, active_since=None) -> list[str]:
    """Observe GREEN every RED failure signal whose unit has since recovered after the failure reading.

    A failure is a level, so the record must follow the unit: without this a unit that failed once stayed RED after
    its next run succeeded (2026-11-08: nine cleared by hand). Recovery is either the unit's latest exit being clean
    after the failure, or (for a long-running unit that never exits) the unit being active for RECOVERY_STABLE_S
    after the failure. A unit that is still failing, or whose times are unreadable, stays RED. An incident written
    before ``unit`` was recorded is left alone.

    Recovery also retires the pending ``reoffer-<name>`` intention: it was scheduled to return a still-standing fault,
    and once the signal is GREEN it only fires a spurious wake (2026-11-10: a recovered pub service still woke Health).
    A signal that was already GREEN when this ran — recovered by a path that predates that retirement (the 2026-11-10
    ``failed-mishe-doctor-service`` residue, and the same shape under ``failed-mishe-producer-*-service``) — carries the
    same spurious re-offer and is retired here too, so a stale re-offer cannot outlive the next sweep.

    An already-GREEN record whose unit has run cleanly since the record was last confirmed is then refreshed, not
    rewritten: a recovered failure is a level nothing else re-observes, so it would otherwise age into a false UNKNOWN
    that wakes its subscriber (2026-11-16: camera-livingroom). A unit that keeps exiting is confirmed by a clean exit
    after the record's freshness stamp; a long-running unit that never exits is confirmed by still being active and
    stable, not by restarting (its start time does not move, so a start-after-the-stamp test could never hold on a
    second pass), the same distinction the initial clear makes. The unit is the evidence, so a unit that stops stops
    being confirmed and the record lapses as before.
    """
    from . import work_boundary
    home = Path(home)
    candidates = []
    for name in sorted(signal_store.registry(home)):
        if not work_boundary.failure_signal(name):
            continue
        reading = signal_store.recorded(home, name)
        if not reading:
            continue
        state, value = reading.get("state"), reading.get("value")
        already_green = state == signal_store.OK and value == "GREEN"
        if already_green:
            _retire_reoffers(home, name)     # already recovered: any re-offer is residue, not a standing fault
        # A RED failure is a level the sweep clears. A failure that lapsed to UNKNOWN by expiry (nobody cleared it within
        # SIGNAL_MAX_AGE) is still a standing record, and the clean-exit test below is the same evidence for it: 2026-11-13
        # failed-mishe-producer-work-watch-service stayed UNKNOWN for a week while its unit exited 0 every 2 minutes.
        expired = state == signal_store.UNKNOWN and value is None
        red = state == signal_store.OK and value == "RED"
        if not (already_green or expired or red):
            continue
        incident = read_json(home / "work/incidents" / f"{name}.json")
        unit = _unit_for(name, incident)
        if unit is None:
            continue
        if already_green:
            # A recovered record is kept fresh by the unit's next clean run, and the sweep is the only thing that looks
            # at the unit while the record is GREEN: without it the record ages into a false UNKNOWN that wakes its
            # subscriber (2026-11-16: the camera-livingroom producer's failure signal expired to UNKNOWN at 00:17Z and
            # woke Senses although the producer had just exited 0). The stamp it is confirmed against is the freshness
            # stamp, not the stored edge, so a unit that stops stops being confirmed and ages out as before. A record
            # that already lapsed has nothing to keep fresh; expiry and the recovery path below handle it.
            fresh = signal_store.read(home, name, now=now)
            if fresh.state != signal_store.OK:
                continue
            since = fresh.observed_at
        elif expired:
            # An expired reading is cleared by an exit after the last failure the incident saw. With no incident
            # last_seen (missing record), the expiry write is the proxy: a clean exit after a week of standing record is
            # the recovery the sweep looks for, and a recurrence writes a new incident.
            last_seen = incident.get("last_seen") if isinstance(incident, dict) else None
            since = last_seen if isinstance(last_seen, (int, float)) else reading.get("observed_at")
        else:
            since = reading.get("observed_at")     # a RED reading is cleared by an exit after the RED was observed
        if not isinstance(since, (int, float)):
            continue
        candidates.append((name, unit, since, already_green, reading, incident))

    properties = (work_boundary.unit_recovery_properties({unit for _, unit, *_ in candidates})
                  if last_exit is None or active_since is None else {})
    cleared = []
    for name, unit, since, already_green, reading, incident in candidates:
        values = properties.get(unit, {})
        exit_ = last_exit(unit) if last_exit else work_boundary.unit_last_exit(unit, values)
        exited_clean = bool(exit_ and exit_[1] and exit_[0] > since)
        # A long-running unit has no exit to show: it recovered if it has stayed active since the failure, for a while.
        started = None
        if not exited_clean:
            started = active_since(unit) if active_since else work_boundary.unit_active_since(values)
        # A still-flagged record needs the unit to have (re)started after the failure it records. An already-GREEN
        # recovered record needs only a stable-active unit: a long-running unit's start time is fixed, so
        # ``started > since`` would fail on every pass after the refresh that moved the stamp, and the record would
        # age to a false UNKNOWN anyway (2026-11-16: the dual-probe service, active since 11-14 00:50Z).
        # A wake unit is not a long-running service: it runs one engine and its success is its clean exit (the yield).
        # Being active for a while proves nothing (2026-11-27: senses GREEN at 22:02Z while its 22:00:21Z run had
        # just failed without a yield), so only a service may clear on stable activity.
        is_wake = revive_boundary.unit_kind(unit) == "wake-service"
        running_clean = started is not None and now - started >= RECOVERY_STABLE_S \
            and (already_green or (started > since and not is_wake))
        if not (exited_clean or running_clean):
            continue
        # A failure handler can publish a new reading while the manager call is in flight.
        if (signal_store.recorded(home, name) != reading
                or read_json(home / "work/incidents" / f"{name}.json") != incident):
            continue
        if already_green:
            signal_store.refresh(home, name, now=now)     # not an edge: never rewrites NAME.json, never wakes a subscriber
            continue
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
    text = wall_text.FAILURE_NOTICE.format(unit=unit, name=name)
    (notify or (lambda r, t: default_notify(home, r, t)))(role, text)
    owed.touch_inbox(home, role)
    return role
