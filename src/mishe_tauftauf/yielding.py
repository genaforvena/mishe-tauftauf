"""``mishe yield``: how a woken mind ends its wake.

Settling updates the wall, advances the role's cursor to what the wake saw,
turns ``--continue``/``--again``/``--when`` into intentions, renews the idle
initiative, and writes the receipt last. The receipt is what lets the wake
runner mark the dispatch completed; a mind that never reaches it leaves the
dispatch open for the next wake to reconcile. Settling twice is a no-op.
"""
from __future__ import annotations

from pathlib import Path

from . import intentions, owed
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
           idle_seconds: float = DEFAULT_IDLE_SECONDS, wall_writer=None) -> str:
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
    intentions.add(home, role, IDLE_ID, note="idle initiative: look for useful work",
                   due=now + idle_seconds, spontaneous=True)
    write_json(receipt_path(home, key), {"role": role, "key": key, "result": result,
                                         "continue": continue_, "settled_at": now})
    return f"yield {role} {key} settled ({result})"
