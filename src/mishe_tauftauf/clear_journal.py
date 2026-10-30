"""Decode the wall-clear journals a supervisor writes under ``checks/``."""
from __future__ import annotations

import json
from pathlib import Path


def paths(home: Path) -> list[Path]:
    """Clear journal paths for this site; none means no rotation was recorded."""
    return sorted((home / "checks").glob("wall-clear-*.json"))


def read(path: Path) -> dict | None:
    """One journal as an object.

    A journal that cannot be read, or whose text is not a JSON object, is
    unreadable monitoring damage rather than a healthy absent rotation: it
    returns None so the consumer stays UNKNOWN instead of silently passing.
    """
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return journal if isinstance(journal, dict) else None


def role_from_filename(name: str) -> str | None:
    """Role encoded in a ``wall-clear-<role>-<seq>.json`` filename.

    Returns the role when the filename matches the wall-clear journal
    naming convention, otherwise ``None``. The string operations live here
    so consumers do not reconstruct protocol from prose — this file is the
    approved boundary for wall-clear journals per ``parsing-policy.json``.
    """
    if not name.startswith("wall-clear-") or not name.endswith(".json"):
        return None
    rest = name[len("wall-clear-") : -len(".json")]
    if "-" not in rest:
        return None
    role = rest.rsplit("-", 1)[0]
    return role if role else None
