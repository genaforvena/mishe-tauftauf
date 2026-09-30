"""Small deterministic view of tasks and work receipts in the shared text tape."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .feed import FeedEntry
from .records import payload


TASK_RE = re.compile(r"^\[task\]\s+(\S+)\s+owner=([a-z0-9-]+)(?:\s|$)")
STATE_RE = re.compile(r"^\[(taking|done|dropped)\]\s+(\S+)(?:\s|$)")
WORK_RE = re.compile(r"^\[work\] channel=([a-z0-9-]+) wake=(\d+) observation=(\S+) result=(\S+) ")
CLAIM_RE = re.compile(r"^\[task-claim\] (\S+) owner=([a-z0-9-]+) previous=([a-z0-9-]+)(?:\s|$)")


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
    task: str | None = None
    step: str | None = None
    outcome: str | None = None


def open_tasks(entries: list[FeedEntry]) -> list[Task]:
    from .task_state import states

    return sorted((Task(state.identity, state.owner, state.activity, state.sequence)
                   for state in states(entries).values()), key=lambda task: task.sequence)


def work_receipts(entries: list[FeedEntry], channel: str) -> list[Work]:
    work = []
    for entry in entries:
        if entry.source != "seed":
            continue
        if match := WORK_RE.match(entry.body):
            if match.group(1) == channel:
                task = re.search(r"(?:^| )task=(\S+)(?: |$)", entry.body.splitlines()[0])
                step = re.search(r"(?:^| )task_step=(\S+)(?: |$)", entry.body.splitlines()[0])
                outcome = re.search(r"(?:^| )task_outcome=(\S+)(?: |$)", entry.body.splitlines()[0])
                data = payload(entry) if any(line.startswith("[record] ") for line in entry.body.splitlines()) else {}
                work.append(Work(channel, int(match.group(2)), match.group(3), match.group(4),
                                 task.group(1) if task else data.get("task"), step.group(1) if step else data.get("task_step"),
                                 outcome.group(1) if outcome else data.get("task_outcome")))
    return work


def repeated_no_change(entries: list[FeedEntry], channel: str, count: int = 3) -> list[Work]:
    recent = work_receipts(entries, channel)[-count:]
    same_legacy_step = len({work.observation for work in recent}) == 1 and len({(work.task, work.step) for work in recent}) == 1
    same_outcome = all(work.task and work.outcome for work in recent) and len({(work.task, work.outcome) for work in recent}) == 1
    if len(recent) == count and (same_legacy_step or same_outcome) and all(
            work.result in {"verified", "blocked", "unspecified"} for work in recent):
        return recent
    return []
