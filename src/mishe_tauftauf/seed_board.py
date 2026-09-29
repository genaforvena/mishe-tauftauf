"""Small deterministic view of tasks and work receipts in the shared text tape."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .feed import FeedEntry


TASK_RE = re.compile(r"^\[task\]\s+(\S+)\s+owner=([a-z0-9-]+)(?:\s|$)")
STATE_RE = re.compile(r"^\[(taking|done|dropped)\]\s+(\S+)(?:\s|$)")
WORK_RE = re.compile(r"^\[work\] channel=([a-z0-9-]+) wake=(\d+) observation=(\S+) result=(\S+) ")


@dataclass(frozen=True)
class Task:
    identity: str
    owner: str
    status: str
    sequence: int


@dataclass(frozen=True)
class Work:
    channel: str
    wake: int
    observation: str
    result: str


def open_tasks(entries: list[FeedEntry]) -> list[Task]:
    tasks: dict[str, Task] = {}
    for entry in entries:
        first = entry.body.splitlines()[0].lstrip(" \t") if entry.body else ""
        if match := TASK_RE.match(first):
            identity = match.group(1)
            if identity not in tasks:
                tasks[identity] = Task(identity, match.group(2), "open", entry.sequence)
        elif match := STATE_RE.match(first):
            old = tasks.get(match.group(2))
            if old is not None:
                tasks[old.identity] = Task(old.identity, old.owner, match.group(1), entry.sequence)
    return sorted((task for task in tasks.values() if task.status not in {"done", "dropped"}),
                  key=lambda task: task.sequence)


def work_receipts(entries: list[FeedEntry], channel: str) -> list[Work]:
    work = []
    for entry in entries:
        if entry.source != "seed":
            continue
        if match := WORK_RE.match(entry.body):
            if match.group(1) == channel:
                work.append(Work(channel, int(match.group(2)), match.group(3), match.group(4)))
    return work


def repeated_no_change(entries: list[FeedEntry], channel: str, count: int = 3) -> list[Work]:
    recent = work_receipts(entries, channel)[-count:]
    if len(recent) == count and len({work.observation for work in recent}) == 1 and all(
            work.result in {"verified", "blocked", "unspecified"} for work in recent):
        return recent
    return []
