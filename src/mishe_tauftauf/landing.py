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


def dependency_ids(tasks, identity: str) -> set[str]:
    """Traverse open children and concrete producers, never completed history."""
    children: dict[str, list[str]] = {}
    for task in tasks.values():
        if task.parent:
            children.setdefault(task.parent, []).append(task.identity)
    pending = [identity]
    visited = set()
    while pending:
        cursor = pending.pop()
        if cursor in visited or cursor not in tasks:
            continue
        visited.add(cursor)
        pending.extend(children.get(cursor, []))
        if tasks[cursor].retry_task:
            pending.append(tasks[cursor].retry_task)
    return visited


def check_waits(tasks, identities) -> None:
    for identity in identities:
        task = tasks[identity]
        if task.status == "waiting" and task.retry_event and not (task.retry_task or task.retry_at):
            raise ValueError(f"landing prerequisite {identity} requires a concrete producer task or an external "
                             "retry deadline; use task step for an owned production step")


def priority_ids(entries: list[FeedEntry]) -> dict[str, int]:
    """An open delivery's concrete producers share its original queue position."""
    from .task_state import states

    tasks = states(entries)
    positions: dict[str, int] = {}
    for identity, sequence in registrations(entries).items():
        for cursor in dependency_ids(tasks, identity):
            positions[cursor] = min(sequence, positions.get(cursor, sequence))
    return positions


def register(home: Path, identity: str, source: str, producer: str, reason: str, evidence: Path) -> FeedEntry:
    from .seed import _lock
    from .task_state import _evidence, _event, states

    _event(identity)
    path, digest = _evidence(evidence)
    data = dict(owner="genome", producer=producer, reason=reason.strip(), evidence=path, evidence_sha256=digest)
    body = f"[landing] {identity}\n" + json.dumps(data, sort_keys=True)
    validate(source, body)
    with _lock(home):
        tasks = states(Feed(home).entries())
        current = tasks.get(identity)
        if current is None or current.owner != "genome":
            raise ValueError("landing requires an open genome-owned task")
        check_waits(tasks, dependency_ids(tasks, identity))
        return Feed(home).append(source, body +
                                f"\nLanding {identity} from {producer} reserves genome's next available turn "
                                f"in queue order, when its task prerequisite is ready. Reason: {reason.strip()}. "
                                f"Evidence: {path}. Continue this delivery before optional new source work.", task_control=True)


def line(entries: list[FeedEntry]) -> str:
    from .task_state import eligible, pending_tasks, select_task

    pending = queue(entries)
    ready = [task for task in pending if eligible(task, entries)]
    if not pending:
        return "LANDING: CLEAR — no registered delivery debt"
    selected = select_task(entries, "genome")
    priorities = priority_ids(entries)
    active = [f"{owner}:{identity}@{wake}" for (owner, wake), identity in pending_tasks(entries).items()
              if identity in priorities]
    next_id = selected.identity if selected and selected.identity in priorities else "reserved or retry required"
    return (f"LANDING: HOLD new source production — {len(pending)} pending, {len(ready)} prerequisites ready; "
            f"active={','.join(active) or 'none'}; "
            f"next={next_id}; "
            "next is after active turns; a delivered attempt is reserved to finish, not blocked")
