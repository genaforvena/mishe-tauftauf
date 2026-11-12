"""``mishe yield``: how a woken mind ends its wake.

Settling updates the wall, advances the role's cursor to what the wake saw,
turns ``--continue``/``--again``/``--when`` into intentions, renews the idle
initiative, and writes the receipt last. The receipt is what lets the wake
runner mark the dispatch completed; a mind that never reaches it leaves the
dispatch open for the next wake to reconcile. Settling twice is a no-op.
"""
from __future__ import annotations

from pathlib import Path

from . import budget_producer, intentions, owed, signal_store
from .state_files import read_json, write_json

IDLE_ID = "idle-initiative"
DEFAULT_IDLE_SECONDS = 2 * 3600  # idle-initiative pace; was 6h (Ilya DM 299699: ~2h for roles with open plates)
WHEN_DEADLINE_SECONDS = 6 * 3600  # fallback timer paired with a --when that names no --again
# A continue yield must not be due at once: a due-now continue re-arms the next wake in the same
# minute, and the restart storm trips the unit's start limit (2026-11-11 health wall).
CONTINUE_FLOOR_SECONDS = 120
OPEN_LANE_SECONDS = 30 * 60  # a role with a ready lane and spare budget returns in 30 min (Ilya DM 300036)
BUDGET_MAX_AGE = 3600
SPARE_5H, SPARE_7D = 0.30, 0.30
RESULTS = ("changed", "verified", "blocked")


def spare_budget(home, now) -> bool:
    """True only on fresh evidence: some account has both 5h and 7d windows FRESH, recent, not yet reset and above the spare floor.
    The reset rule is the router's own (budget_producer.window_fresh_at), so a window cannot read as spare past its reset.
    Missing, stale or unreadable evidence is UNKNOWN and reads as False."""
    data = read_json(Path(home) / "engine" / "budget.json")
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        return False
    for acct in data["accounts"].values():
        try:
            w = acct["windows"]
            ok = acct.get("state") == "FRESH"
            for name, floor in (("5h", SPARE_5H), ("7d", SPARE_7D)):
                x = w[name]
                ok = ok and x.get("state") == "FRESH" and 0 <= now - float(x["observed_at"]) <= BUDGET_MAX_AGE and float(x["remaining"]) >= floor
                ok = ok and budget_producer.window_fresh_at(x.get("observed_at"), x.get("reset_at"), now)
            if ok:
                return True
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return False


def open_lane(home, role) -> bool:
    from . import work_state
    record = work_state.load(home, role)
    return bool(record and record.get("state") != "UNKNOWN" and any(t.get("state") == "ready" for t in record.get("tasks", [])))


def idle_delay(home, role, now, idle_seconds) -> float:
    if idle_seconds == DEFAULT_IDLE_SECONDS and open_lane(home, role) and spare_budget(home, now):
        return OPEN_LANE_SECONDS
    return idle_seconds


def wakes_dir(home) -> Path:
    return Path(home) / "wakes"


def receipt_path(home, key: str) -> Path:
    return wakes_dir(home) / f"{key}.yield.json"


def snapshot_path(home, key: str) -> Path:
    return wakes_dir(home) / f"{key}.snapshot.json"


def settle(home, role: str, key: str, *, text: str, result: str, now: float,
           continue_: bool = False, again: float | None = None, when: dict | None = None,
           idle_seconds: float = DEFAULT_IDLE_SECONDS, wall_writer=None, artifacts=(), work_record=None) -> str:
    if result not in RESULTS:
        raise ValueError(f"result must be one of {', '.join(RESULTS)}")
    if not text or text.isspace():
        raise ValueError("a yield needs wall text")
    if receipt_path(home, key).exists():
        return f"yield {role} {key} already settled"
    raw = read_json(snapshot_path(home, key))
    if raw is None:
        raise ValueError(f"no wake {key!r} is open for {role!r}")
    snap = owed.Snapshot.from_json(raw["snapshot"])
    if raw.get("role") != role:
        raise ValueError(f"wake {key!r} belongs to {raw.get('role')!r}, not {role!r}")
    from . import work_receipts
    artifact_facts = [work_receipts.artifact(home, p) for p in artifacts]
    if work_record is not None:
        from . import work_state
        work_state.save(home, role, work_record)
    if wall_writer is None:
        from . import wall
        wall_writer = wall.write
    wall_writer(Path(home), role, text)
    owed.advance(home, role, snap)
    if continue_:
        intentions.add(home, role, "continue", note="continue the work started in the previous wake", due=now + CONTINUE_FLOOR_SECONDS)
    # A signal wait always carries a deadline (DM 300071 item 3): without --again the
    # yield sets the default one rather than leaving a wait that may never fire.
    deadline_note = ""
    if when is not None and again is None:
        again = WHEN_DEADLINE_SECONDS
        deadline_note = f"; no --again given, so a {WHEN_DEADLINE_SECONDS // 3600}h deadline was set with the signal wait"
    # The fallback timer and the signal condition of one yield share a pair: whichever fires first retires the other.
    pair = key if (again is not None and when is not None) else None
    if again is not None:
        intentions.add(home, role, "again", note="scheduled return", due=now + again, pair=pair)
    dead_wait = ""
    if when is not None and when["signal"] not in signal_store.registry(home):
        dead_wait = f"; WARNING --when names signal {when['signal']!r}, which is not registered, so it cannot fire until it is registered"
    if when is not None:
        intentions.add(home, role, f"when-{when['signal']}", note="signal condition you asked to be woken on", when=when, pair=pair)
    for name, entry in sorted(signal_store.registry(home).items()):
        if role in entry.get("subscribers", []) and "reoffer" in entry:
            sig = signal_store.read(home, name, now=now)
            from . import work_state, dispositions
            acknowledged = work_state.incident_wait(home, role, name, now=now, repo=Path(home).parent)
            known = dispositions.active(home, name, now=now)
            if known and known["kind"] == "known":
                intentions.add(home, role, f"disposition-{name}", due=known["recheck_at"], note="Known incident disposition expires; choose action")
            if sig.state != "OK" or sig.value == "RED":       # level: a standing fault returns
                if not acknowledged and not (known and known["kind"] == "known"):
                    intentions.add(home, role, f"reoffer-{name}", due=now + entry["reoffer"],
                                   note=f"signal {name} is still not clear; investigate or disposition it")
            else:                                             # clear again: the re-offer is residue, not a standing fault
                intentions.remove(home, role, f"reoffer-{name}")
    intentions.add(home, role, IDLE_ID, note="idle initiative: look for useful work",
                   due=now + idle_delay(home, role, now, idle_seconds), spontaneous=True)
    from . import work_state
    work_state.settle_waits(home, role, now=now)
    write_json(receipt_path(home, key), {"role": role, "key": key, "result": result,
                                         "continue": continue_, "settled_at": now, "artifacts": artifact_facts})
    return f"yield {role} {key} settled ({result}){dead_wait}{deadline_note}"
