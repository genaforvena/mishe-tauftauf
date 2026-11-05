"""``mishe yield``: how a woken mind ends its wake.

Settling updates the wall, advances the role's cursor to what the wake saw,
turns ``--continue``/``--again``/``--when`` into intentions, renews the idle
initiative, and writes the receipt last. The receipt is what lets the wake
runner mark the dispatch completed; a mind that never reaches it leaves the
dispatch open for the next wake to reconcile. Settling twice is a no-op.
"""
from __future__ import annotations

from pathlib import Path

from . import intentions, owed, signal_store
from .state_files import read_json, write_json

IDLE_ID = "idle-initiative"
DEFAULT_IDLE_SECONDS = 6 * 3600
RESULTS = ("changed", "verified", "blocked")


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
        intentions.add(home, role, "continue", note="continue the work started in the previous wake", due=now)
    if again is not None:
        intentions.add(home, role, "again", note="scheduled return", due=now + again)
    if when is not None:
        intentions.add(home, role, f"when-{when['signal']}", note="signal condition you asked to be woken on", when=when)
    for name, entry in sorted(signal_store.registry(home).items()):
        if role in entry.get("subscribers", []) and "reoffer" in entry:
            sig = signal_store.read(home, name, now=now)
            from . import work_state, dispositions
            acknowledged = work_state.incident_wait(home, role, name, now=now, repo=Path(home).parent)
            known = dispositions.active(home, name, now=now)
            if known and known["kind"] == "known":
                intentions.add(home, role, f"disposition-{name}", due=known["recheck_at"], note="Known incident disposition expires; choose action")
            if (sig.state != "OK" or sig.value == "RED") and not acknowledged and not (known and known["kind"] == "known"):       # level: a standing fault returns
                intentions.add(home, role, f"reoffer-{name}", due=now + entry["reoffer"],
                               note=f"signal {name} is still not clear; investigate or disposition it")
    intentions.add(home, role, IDLE_ID, note="idle initiative: look for useful work",
                   due=now + idle_seconds, spontaneous=True)
    from . import work_state
    work_state.settle_waits(home, role, now=now)
    write_json(receipt_path(home, key), {"role": role, "key": key, "result": result,
                                         "continue": continue_, "settled_at": now, "artifacts": artifact_facts})
    return f"yield {role} {key} settled ({result})"
