"""Durable signals: the files that observation scripts write and systemd watches.

``signals/NAME.json`` holds the last reading and is replaced only when the value
or state changes, so a path unit on it fires on an edge and never on a refresh.
``signals/NAME.seen`` is touched on every successful observation; its mtime is
freshness. A reading older than its declared ``max_age`` is UNKNOWN. The
registry (``signals/registry.json``) declares max_age and subscribing roles.
"""
from __future__ import annotations

import os
from pathlib import Path

from .live_signal import OK, UNKNOWN, Signal
from .state_files import read_json, write_json

_NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def _dir(home) -> Path:
    path = Path(home) / "signals"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check_name(name: str) -> str:
    if not name or not set(name) <= _NAME_OK:
        raise ValueError(f"signal name {name!r} must be lowercase letters, digits, - or _")
    return name


def _touch_seen(home, name: str, at: float) -> None:
    seen = _dir(home) / f"{name}.seen"
    seen.touch()
    os.utime(seen, ns=(int(at * 1e9), int(at * 1e9)))


def registry(home) -> dict:
    data = read_json(_dir(home) / "registry.json")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("signal registry must be an object")
    return data


def register(home, name: str, *, max_age: float, subscribers=(), reoffer: float | None = None) -> None:
    _check_name(name)
    if not max_age > 0:
        raise ValueError("max_age must be positive")
    data = registry(home)
    data[name] = {"max_age": float(max_age), "subscribers": sorted(set(subscribers))}
    if reoffer is not None:
        data[name]["reoffer"] = float(reoffer)
    write_json(_dir(home) / "registry.json", data)


def _entry(home, name: str) -> dict:
    entry = registry(home).get(name)
    if entry is None:
        raise ValueError(f"signal {name!r} is not registered")
    return entry


def _load(home, name: str) -> dict | None:
    """The stored reading, or None when absent or unreadable (never repaired)."""
    try:
        data = read_json(_dir(home) / f"{name}.json")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def rev(home, name: str) -> int:
    data = _load(home, name)
    value = data.get("rev") if data else 0
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def recorded(home, name: str) -> dict | None:
    """The last stored reading as written (value, state, observed_at, source), or None. Never repaired."""
    return _load(home, name)


def _seen(home, name: str) -> float | None:
    try:
        return (_dir(home) / f"{name}.seen").stat().st_mtime_ns / 1e9
    except FileNotFoundError:
        return None


def _store(home, name: str, *, value, state: str, reason: str, at: float, source: str) -> None:
    path = _dir(home) / f"{name}.json"
    if path.exists() and _load(home, name) is None:
        os.replace(path, path.with_name(f"{name}.corrupt-{int(at)}"))   # keep the evidence
    write_json(path, {"value": value, "state": state, "reason": reason,
                         "observed_at": at, "source": source, "rev": rev(home, name) + 1})


def observe(home, name: str, value, *, now: float, source: str) -> bool:
    """Record a successful observation. True when it is an edge (json replaced)."""
    _entry(home, name)
    current = _load(home, name)
    edge = not (current and current.get("state") == OK and current.get("value") == value)
    if edge:
        _store(home, name, value=value, state=OK, reason="", at=now, source=source)
    _touch_seen(home, name, now)
    return edge


def observe_unknown(home, name: str, *, now: float, reason: str, source: str) -> bool:
    """Record a failed observation as UNKNOWN now, so a stale GREEN is never left standing.

    Unlike ``observe`` this does not touch the ``.seen`` freshness stamp: a failure is not evidence
    that the input was fresh. True when it is an edge (json replaced).
    """
    _entry(home, name)
    current = _load(home, name)
    edge = not (current and current.get("state") == UNKNOWN and current.get("reason") == reason)
    if edge:
        _store(home, name, value=None, state=UNKNOWN, at=now, source=source, reason=reason)
    return edge


def read(home, name: str, *, now: float) -> Signal:
    """The current reading. Missing, corrupt or expired input is UNKNOWN."""
    max_age = _entry(home, name)["max_age"]
    data = _load(home, name)
    seen = _seen(home, name)
    if data is None or seen is None:
        return Signal(None, None, name, UNKNOWN, "no readable observation")
    if data.get("state") != OK:
        return Signal(None, data.get("observed_at"), name, data.get("state") or UNKNOWN,
                      data.get("reason") or "")
    if now - seen > max_age:
        return Signal(None, seen, name, UNKNOWN, f"expired after {max_age:g}s (age {now - seen:.1f}s)")
    return Signal(data.get("value"), seen, data.get("source") or name, OK)


def expire(home, *, now: float) -> list[str]:
    """Turn every expired OK signal UNKNOWN on disk (an edge). Names changed."""
    changed = []
    for name, entry in sorted(registry(home).items()):
        data = _load(home, name)
        seen = _seen(home, name)
        if data is None or seen is None or data.get("state") != OK:
            continue
        if now - seen > entry["max_age"]:
            _store(home, name, value=None, state=UNKNOWN, at=now, source=data.get("source") or name,
                   reason=f"expired after {entry['max_age']:g}s without observation")
            changed.append(name)
    return changed


def next_expiry(home) -> float | None:
    """Earliest ``seen + max_age`` among signals still OK, or None."""
    due = []
    for name, entry in registry(home).items():
        data = _load(home, name)
        seen = _seen(home, name)
        if data and data.get("state") == OK and seen is not None:
            due.append(seen + entry["max_age"])
    return min(due) if due else None
