"""The deterministic owed check: does this role have anything to do?

Owed means: an unread inbox touch, a subscribed signal whose revision moved
past the role's cursor, or an intention that is due or whose condition holds.
Nothing owed means no model call. A cursor moves only when a wake yields, and
only to the snapshot taken when that wake began, so input that lands mid-wake
stays owed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import intentions, signal_store
from .state_files import read_json, write_json

BUDGET_SIGNAL = "budget"
BUDGET_OK = "GREEN"


@dataclass
class Snapshot:
    reasons: list = field(default_factory=list)
    held: list = field(default_factory=list)
    inbox_ns: int = 0
    signal_revs: dict = field(default_factory=dict)
    consumed: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {"reasons": self.reasons, "held": self.held, "inbox_ns": self.inbox_ns,
                "signal_revs": self.signal_revs, "consumed": self.consumed}

    @classmethod
    def from_json(cls, data: dict) -> "Snapshot":
        return cls(list(data.get("reasons", [])), list(data.get("held", [])),
                   int(data.get("inbox_ns", 0)), dict(data.get("signal_revs", {})),
                   list(data.get("consumed", [])))


def inbox_path(home, role: str) -> Path:
    return Path(home) / "inbox" / role


def touch_inbox(home, role: str) -> None:
    """Mark mail for ``role``. The mtime always moves forward, even on a coarse clock."""
    import time
    path = inbox_path(home, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = path.stat().st_mtime_ns
    except FileNotFoundError:
        previous = 0
    path.touch()
    stamp = max(time.time_ns(), previous + 1)
    os.utime(path, ns=(stamp, stamp))


def cursor_path(home, role: str) -> Path:
    return Path(home) / "cursors" / f"{role}.json"


def _cursor(home, role: str):
    """The role's cursor; zeros if none yet; None if present but unreadable."""
    try:
        data = read_json(cursor_path(home, role))
    except ValueError:
        return None
    if data is None:
        return {"inbox_ns": 0, "signals": {}}
    return data if isinstance(data, dict) else None


def _budget_green(home, now: float) -> bool:
    try:
        sig = signal_store.read(home, BUDGET_SIGNAL, now=now)
    except ValueError:
        return False
    return sig.state == "OK" and sig.value == BUDGET_OK


def check(home, role: str, *, now: float) -> Snapshot:
    cursor = _cursor(home, role)
    snap = Snapshot()
    if cursor is None:
        snap.reasons.append("cursor-unreadable")       # never mistaken for "nothing owed"
        return snap
    try:
        snap.inbox_ns = inbox_path(home, role).stat().st_mtime_ns
    except FileNotFoundError:
        snap.inbox_ns = 0
    if snap.inbox_ns > cursor.get("inbox_ns", 0):
        snap.reasons.append("inbox")
    for name, entry in sorted(signal_store.registry(home).items()):
        if role in entry.get("subscribers", []):
            snap.signal_revs[name] = signal_store.rev(home, name)
            if snap.signal_revs[name] > cursor.get("signals", {}).get(name, 0):
                snap.reasons.append(f"signal:{name}")
    green = None
    for item in intentions.list_(home, role):
        if not intentions.is_met(home, item, now=now):
            continue
        tag = f"intention:{item['id']}"
        if item.get("spontaneous"):
            green = _budget_green(home, now) if green is None else green
            if not green:
                snap.held.append(tag)
                continue
        snap.reasons.append(tag)
        snap.consumed.append(item["id"])
    return snap


def advance(home, role: str, snap: Snapshot) -> None:
    """Move the cursor to what the wake saw and drop the intentions it consumed."""
    write_json(cursor_path(home, role), {"inbox_ns": snap.inbox_ns, "signals": snap.signal_revs})
    for id in snap.consumed:
        intentions.remove(home, role, id)
