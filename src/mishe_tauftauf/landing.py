"""Explicit, feed-backed landing priority without changing task-state wire records."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .feed import Feed, FeedEntry
from .observations import validate_slug


def validate(source: str, body: str) -> tuple[str, dict]:
    from .task_state import _event

    try:
        first, rest = body.split("\n", 1)
        identity = _event(first.removeprefix("[landing] "))
        data = json.loads(rest.splitlines()[0])
        if not isinstance(data, dict) or data.get("owner") != "genome" or source not in {"genome", "operator"}:
            raise ValueError("landing registration belongs to genome or operator")
        validate_slug(data["producer"])
        if not isinstance(data.get("reason"), str) or not data["reason"].strip() or not data.get("evidence"):
            raise ValueError("landing registration requires reason and evidence")
        if not re.fullmatch(r"[0-9a-f]{64}", data.get("evidence_sha256", "")):
            raise ValueError("landing registration requires evidence digest")
        return identity, data
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ValueError(f"invalid landing control: {exc}") from exc


def registrations(entries: list[FeedEntry]) -> dict[str, int]:
    """First registration owns position; progress and duplicate notices cannot move it."""
    positions = {}
    for entry in entries:
        if entry.body.startswith("[landing] "):
            identity, _ = validate(entry.source, entry.body)
            positions.setdefault(identity, entry.sequence)
    return positions


def queue(entries: list[FeedEntry]):
    from .task_state import states

    tasks = states(entries)
    return [tasks[identity] for identity in registrations(entries)
            if identity in tasks]


def register(home: Path, identity: str, source: str, producer: str, reason: str, evidence: Path) -> FeedEntry:
    raise ValueError("raw landing registration is retired; authors use delivery submit with a committed candidate")


def line(entries: list[FeedEntry]) -> str:
    pending = queue(entries)
    return (f"LANDING: LEGACY recovery — {len(pending)} unfinished historical deliveries; "
            "source production allowed; authors submit committed candidates") if pending else (
            "LANDING: CLEAR — no historical delivery debt; source production allowed")
