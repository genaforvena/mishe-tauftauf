"""Intentions: what a mind said it would come back to, as one file each.

``intentions/ROLE/ID.json`` is ``{id, note, due?, when?, spontaneous?}``. ``due``
is an epoch time; ``when`` is a signal condition ``{signal, equals?|state?}``.
A ``spontaneous`` intention is the mind's own initiative and is held by the
budget gate; the others answer something that happened.
"""
from __future__ import annotations

from pathlib import Path

from . import signal_store
from .state_files import read_json, write_json

_ID_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")


def _dir(home, role: str) -> Path:
    return Path(home) / "intentions" / role


def add(home, role: str, id: str, *, note: str, due: float | None = None,
        when: dict | None = None, spontaneous: bool = False) -> None:
    if not id or not set(id) <= _ID_OK:
        raise ValueError(f"intention id {id!r} must be lowercase letters, digits, - _ or .")
    if due is None and when is None:
        raise ValueError("an intention needs a due time or a signal condition")
    entry = {"id": id, "note": note, "spontaneous": bool(spontaneous)}
    if due is not None:
        entry["due"] = float(due)
    if when is not None:
        if "signal" not in when or not ({"equals", "state"} & set(when)):
            raise ValueError("when needs a signal and equals or state")
        entry["when"] = when
    write_json(_dir(home, role) / f"{id}.json", entry)


def remove(home, role: str, id: str) -> None:
    try:
        (_dir(home, role) / f"{id}.json").unlink()
    except FileNotFoundError:
        pass


def list_(home, role: str) -> list[dict]:
    found = []
    for path in sorted(_dir(home, role).glob("*.json")):
        entry = read_json(path)
        if isinstance(entry, dict) and entry.get("id"):
            found.append(entry)
    return found


def is_met(home, entry: dict, *, now: float) -> bool:
    if "due" in entry and now >= entry["due"]:
        return True
    when = entry.get("when")
    if when:
        try:
            sig = signal_store.read(home, when["signal"], now=now)
        except ValueError:
            return False                       # unregistered signal never fires
        if "equals" in when:
            return sig.state == "OK" and sig.value == when["equals"]
        return sig.state == when["state"]
    return False


def next_due(home) -> float | None:
    """Earliest ``due`` over every role's intentions, or None."""
    times = [e["due"] for role_dir in (Path(home) / "intentions").glob("*")
             for e in list_(home, role_dir.name) if "due" in e]
    return min(times) if times else None
