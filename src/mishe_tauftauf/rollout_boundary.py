"""Decode rollout observation and paused sentinel for Deployment transfer.

Boundary for ``health/observed-shas.json``, ``health/observed.json`` and
``health/rollout-paused.json``. Internal consumers act on decoded values
(``list[str]`` shas, optional ready seconds, paused bool); human prose
and malformed evidence stay explicit and never become a guessed rollout.
"""

from __future__ import annotations

import json
from pathlib import Path


def _decode_observed_text(text: str) -> tuple[list[str] | None, object | None]:
    data = json.loads(text)
    if isinstance(data, list):
        return [str(x) for x in data], None
    if isinstance(data, dict) and isinstance(data.get("shas"), list):
        shas = [str(x) for x in data["shas"]]
        ready = data.get("ready_for_seconds")
        return shas, ready
    raise ValueError("malformed observed snapshot: expected list or {shas: list}")


def read_observed_snapshot(home: Path) -> tuple[list[str] | None, object | None]:
    """Return (observed_shas, ready_for_seconds) or (None, None) when absent.

    Tries ``health/observed-shas.json`` then ``health/observed.json``. A
    missing file means no snapshot. Malformed JSON raises ``ValueError`` so
    the caller keeps the condition UNKNOWN and retains original bytes if
    desired; the informational rollout does not become a guessed value.
    """
    for name in ("health/observed-shas.json", "health/observed.json"):
        path = home / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        return _decode_observed_text(text)
    return None, None


def read_rollout_paused(home: Path) -> bool:
    """Return True when ``health/rollout-paused.json`` declares paused.

    Accepts ``{"paused": true}`` or bare ``true``. Missing, unreadable or
    malformed file returns False (not paused); callers treat paused as an
    explicit opt-in sentinel, not an error to be guessed.
    """
    path = home / "health/rollout-paused.json"
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeError, ValueError):
        return False
    if isinstance(data, dict) and data.get("paused") is True:
        return True
    if data is True:
        return True
    return False
