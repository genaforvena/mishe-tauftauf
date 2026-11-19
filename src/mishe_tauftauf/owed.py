"""The deterministic owed check: does this role have anything to do?

Owed means: an unread inbox touch, a subscribed signal whose revision moved
past the role's cursor, or an intention that is due or whose condition holds.
Nothing owed means no model call. A cursor moves only when a wake yields, and
only to the snapshot taken when that wake began, so input that lands mid-wake
stays owed — except a subscribed signal whose live reading is the all-clear
word by settle time: that recovery was published while the wake ran (the
wake's own repair, or a concurrent observer confirming it), and replaying it
would spend a wake that finds nothing owed.
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


def _settled_signal_revs(home, snap: Snapshot, *, now: float) -> dict:
    """Cursor signal revs for settle: the wake-start snapshot, plus a mid-wake recovery.

    A subscribed signal whose every stored edge after the wake-start rev is the all-clear word
    was recovered by a concurrent observer while the wake ran (the in-flight delivery's doctor
    probe, 2026-11-19 19:41Z). Recording the wake-start rev anyway spent the next dispatch
    tick on a health wake that found nothing owed (``signal:doctor`` at 19:49Z), so the cursor
    takes the live rev instead. Any other edge in that window keeps the wake-start rev: a
    mid-wake RED that recovered before settle still wakes the subscriber on the next tick
    (genome review DM #303268, 2026-11-19 — the flap is the failure, and absorbing it
    contradicted this fix's own "a failure is never swallowed"), and so does an UNKNOWN, an
    expiry or any value that is not the all-clear word. The live rev is taken before the
    reading: a RED stored between the two loads stays ahead of the cursor either way. A
    recovery the subscriber never saw is a bounded loss — the next dossier prints the live
    value — a swallowed failure would not be.
    """
    revs = dict(snap.signal_revs)
    live = {name: signal_store.rev(home, name) for name in revs}
    moved = [name for name, snap_rev in revs.items() if snap_rev < live[name]]
    histories = signal_store.history(home, moved)
    for name, snap_rev in revs.items():
        if snap_rev >= live[name]:
            continue
        try:
            reading = signal_store.read(home, name, now=now)
        except (OSError, ValueError, KeyError):
            continue          # unregistered or unreadable by settle time: keep the snapshot rev
        if reading.state != "OK" or reading.value != BUDGET_OK:
            continue
        window = [edge for edge in histories.get(name, ())
                  if snap_rev < edge.get("rev", 0) <= live[name]]
        if all(edge.get("state") == "OK" and edge.get("value") == BUDGET_OK for edge in window):
            revs[name] = live[name]
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
