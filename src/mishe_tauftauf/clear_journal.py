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
