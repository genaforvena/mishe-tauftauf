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
    from .seed import _lock
    from .task_state import _evidence, _event, states

    _event(identity)
    path, digest = _evidence(evidence)
    data = dict(owner="genome", producer=producer, reason=reason.strip(), evidence=path, evidence_sha256=digest)
    body = f"[landing] {identity}\n" + json.dumps(data, sort_keys=True)
    validate(source, body)
    with _lock(home):
        current = states(Feed(home).entries()).get(identity)
        if current is None or current.owner != "genome":
            raise ValueError("landing requires an open genome-owned task")
        return Feed(home).append(source, body +
                                f"\nLanding {identity} from {producer} reserves genome's next available turn "
                                f"in queue order, when its task prerequisite is ready. Reason: {reason.strip()}. "
                                f"Evidence: {path}. Continue this delivery before optional new source work.", task_control=True)


def line(entries: list[FeedEntry]) -> str:
    from .task_state import eligible, select_task

    pending = queue(entries)
    ready = [task for task in pending if eligible(task, entries)]
    if not pending:
        return "LANDING: CLEAR — no registered delivery debt"
    selected = select_task(entries, "genome")
    next_id = selected.identity if selected and selected.identity in {task.identity for task in pending} else "reserved or retry required"
    return (f"LANDING: HOLD new source production — {len(pending)} pending, {len(ready)} prerequisites ready; "
            f"next={next_id}; "
            "finish review/commit/push before optional source improvements")
