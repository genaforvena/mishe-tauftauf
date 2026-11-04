"""One-time migration of each mind's state from the resident runtime.

Nothing is reset. A wake the old tape shows as pending becomes an open dispatch
(unknown effect) that the role's first wake reconciles first; a ``--continue``
yield becomes a ``continue`` intention; messages addressed after the last yield
stay unread. Wall "next actions" are prose and are never interpreted here: each
role gets a first-wake intention asking the mind to turn them into intentions.
Re-running changes nothing that already exists.
"""
from __future__ import annotations

from pathlib import Path

from . import intentions, owed, yielding
from .chat_protocol import decode_lifecycle
from .core.interfaces import UNKNOWN
from .core.state import DispatchRecord, DispatchStore
from .feed import Feed
from .state_files import write_json

FIRST_WAKE_ID = "migrate-wall-next-actions"
FIRST_WAKE_NOTE = ("first wake on the observation-driven runtime: read your wall's next actions and turn each "
                   "into an intention (`mishe yield --again/--when`), then drop what is stale from the wall")


def role_state(home, role: str) -> dict:
    """pending wake (tape sequence), last yield entry sequence, whether it asked to continue."""
    pending, last_yield, cont = None, 0, False
    for entry in Feed(Path(home)).role_entries(role):
        event = decode_lifecycle(entry)
        if event is None or event.role != role:
            continue
        if event.kind == "wake":
            pending = entry.sequence
        elif event.kind == "yield" and pending == event.wake:
            pending, last_yield, cont = None, entry.sequence, event.continue_work
    return {"pending": pending, "last_yield": last_yield, "continue": cont}


def _add_once(home, role: str, id: str, **kw) -> bool:
    if any(i["id"] == id for i in intentions.list_(home, role)):
        return False
    intentions.add(home, role, id, **kw)
    return True


def apply(home, roles, *, now: float) -> dict:
    from .wake import default_mail_reader
    home = Path(home)
    store = DispatchStore(home / "dispatch")
    report = {"open_dispatches": [], "continue": [], "unread": {}}
    for role in roles:
        state = role_state(home, role)
        if state["pending"] is not None and store.read(role) is None:
            store.write(DispatchRecord(
                role=role, key=f"legacy-wake-{role}-{state['pending']}", state=UNKNOWN,
                detail=f"wake {state['pending']} was open on the old runtime's tape at migration; effect unknown"))
            report["open_dispatches"].append(role)
        if not owed.cursor_path(home, role).exists():
            seen = state["pending"] if state["pending"] is not None else state["last_yield"]
            unread = default_mail_reader(home, role, seen)
            write_json(owed.cursor_path(home, role), {"inbox_ns": 0 if unread else _inbox_ns(home, role),
                                                      "signals": {}, "tape_seq": seen})
            if unread:
                owed.touch_inbox(home, role)
                report["unread"][role] = len(unread)
        if state["continue"] and state["pending"] is None and _add_once(
                home, role, "continue", note="continue the work you asked to continue before migration", due=now):
            report["continue"].append(role)
        _add_once(home, role, FIRST_WAKE_ID, note=FIRST_WAKE_NOTE, due=now)
        _add_once(home, role, yielding.IDLE_ID, note="idle initiative: look for useful work",
                  due=now + yielding.DEFAULT_IDLE_SECONDS, spontaneous=True)
    return report


def _inbox_ns(home, role: str) -> int:
    try:
        return owed.inbox_path(home, role).stat().st_mtime_ns
    except FileNotFoundError:
        return 0
