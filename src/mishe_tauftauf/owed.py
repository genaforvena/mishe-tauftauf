"""The deterministic owed check: does this role have anything to do?

Owed means: an unread inbox touch, a subscribed signal whose revision moved
past the role's cursor, or an intention that is due or whose condition holds.
Nothing owed means no model call. When a wake yields, its cursor normally moves
to the snapshot taken at wake start. Settlement can absorb a fresh all-clear
reading when every intervening revision is accounted for by an all-clear edge.
Replaying a verified recovery would spend a wake that finds nothing owed.
"""
from __future__ import annotations

import os
import time
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
    tape_seq: int = 0

    def to_json(self) -> dict:
        return {"reasons": self.reasons, "held": self.held, "inbox_ns": self.inbox_ns,
                "signal_revs": self.signal_revs, "consumed": self.consumed,
                "tape_seq": self.tape_seq}

    @classmethod
    def from_json(cls, data: dict) -> "Snapshot":
        return cls(list(data.get("reasons", [])), list(data.get("held", [])),
                   int(data.get("inbox_ns", 0)), dict(data.get("signal_revs", {})),
                   list(data.get("consumed", [])),
                   int(data.get("tape_seq", 0)))


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


def tape_cursor(home, role: str) -> int:
    cursor = _cursor(home, role) or {}
    value = cursor.get("tape_seq", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _budget_green(home, now: float, role: str) -> bool:
    try:
        config = read_json(Path(home) / "engines" / f"{role}.json")
        config = {} if config is None else config
        if not isinstance(config, dict): return False
        name = config.get("budget_signal", BUDGET_SIGNAL)
        if not isinstance(name, str) or not name: return False
        sig = signal_store.read(home, name, now=now)
    except (OSError, ValueError):
        return False
    if sig.state == "OK" and sig.value == BUDGET_OK:
        return True
    # A free route has no account window, so the Claude budget signal cannot describe it.
    # An admitted live free route admits spontaneous work; a missing, stale or retired
    # free reading fails closed because it comes from the same budget view.
    from . import yielding
    return yielding.fresh_free_route(home, now)


def inbox_unread(home, role: str) -> bool:
    """True when the inbox changed since the role's cursor last advanced (an unread DM is open work)."""
    cursor = _cursor(home, role)
    if cursor is None:
        return False
    try:
        return inbox_path(home, role).stat().st_mtime_ns > cursor.get("inbox_ns", 0)
    except FileNotFoundError:
        return False


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
                # Most signals carry a state word (GREEN/RED), so any value change is a state change
                # worth a wake. A signal registered ``wake_on="state"`` carries a drifting reading
                # (footprint: bytes of anon/charged memory, rewritten every sample); waking its
                # resolver on every sample is a storm, and the wake would find nothing owed. It wakes
                # only when the reading is actionable (not OK: silence, expiry or a failed read).
                if entry.get("wake_on", "change") != "state" or signal_store.read(home, name, now=now).state != "OK":
                    snap.reasons.append(f"signal:{name}")
    green = None
    for item in intentions.list_(home, role):
        if not intentions.is_met(home, item, now=now):
            continue
        tag = f"intention:{item['id']}"
        if item.get("spontaneous"):
            green = _budget_green(home, now, role) if green is None else green
            if not green:
                snap.held.append(tag)
                continue
        snap.reasons.append(tag)
        snap.consumed.append(item["id"])
    return snap


def _green_history_covers(edges, *, after: int, through: int) -> bool:
    last_rev = after
    for edge in edges:
        rev = edge.get("rev")
        if type(rev) is not int:
            return False
        if not after < rev <= through:
            continue
        if rev != last_rev + 1 or edge.get("state") != "OK" or edge.get("value") != BUDGET_OK:
            return False
        last_rev = rev
    return last_rev == through


def _settled_signal_revs(home, snap: Snapshot, *, now: float) -> dict:
    """Absorb a mid-wake recovery only when the edge log accounts for every revision.

    The live revision is read before the reading, so a later failure stays ahead
    of the cursor. A missing edge can hide a failure even when the latest reading
    is fresh, since the store publishes each revision before it appends its edge.
    """
    revs = dict(snap.signal_revs)
    recovered = {}
    for name, snap_rev in revs.items():
        live_rev = signal_store.rev(home, name)
        if snap_rev >= live_rev:
            continue
        try:
            reading = signal_store.read(home, name, now=now)
        except (OSError, ValueError, KeyError):
            continue
        if reading.state == "OK" and reading.value == BUDGET_OK:
            recovered[name] = live_rev
    if not recovered:
        return revs
    try:
        histories = signal_store.history(home, recovered)
    except (OSError, UnicodeError):
        return revs
    for name, live_rev in recovered.items():
        if _green_history_covers(histories[name], after=revs[name], through=live_rev):
            revs[name] = live_rev
    return revs


def advance(home, role: str, snap: Snapshot, *, now: float | None = None) -> None:
    """Move the cursor to what the wake saw and drop the intentions it consumed.

    Intentions that share a ``pair`` with a consumed one answered the same
    yield, so they go too: a fallback timer does not fire after its signal did.
    ``now`` defaults to the wall clock; settle passes its own clock.
    """
    now = time.time() if now is None else now
    write_json(cursor_path(home, role), {"inbox_ns": snap.inbox_ns, "signals": _settled_signal_revs(home, snap, now=now),
                                         "tape_seq": snap.tape_seq})
    listed = intentions.list_(home, role)
    consumed = set(snap.consumed)
    pairs = {e["pair"] for e in listed if e["id"] in consumed and e.get("pair")}
    for id in snap.consumed:
        intentions.remove(home, role, id)
    for e in listed:
        if pairs and e.get("pair") in pairs:
            intentions.remove(home, role, e["id"])
    # The recheck belongs to the deadline ``again``: once that deadline is answered or retired, its recheck is orphaned
    # (DM 300172). The recheck itself carries no pair, since consuming it must not retire the deadline or its signal.
    if "again" in snap.consumed or any(e["id"] == "again" and e.get("pair") in pairs for e in listed):
        intentions.remove(home, role, "again-recheck")
