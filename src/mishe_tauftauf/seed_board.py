"""Small deterministic view of tasks and work receipts in the shared text tape."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .feed import FeedEntry


TASK_RE = re.compile(r"^\[task\]\s+(\S+)\s+owner=([a-z0-9-]+)(?:\s|$)")
STATE_RE = re.compile(r"^\[(taking|done|dropped)\]\s+(\S+)(?:\s|$)")
from .chat_protocol import decode_lifecycle
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


def open_tasks(entries: list[FeedEntry]) -> list[Task]:
    from .task_state import states

    return sorted((Task(state.identity, state.owner, state.activity, state.sequence)
                   for state in states(entries).values()), key=lambda task: task.sequence)


def work_receipts(entries: list[FeedEntry], channel: str) -> list[Work]:
    """Settlements in the current feed format.

    A `seed wake <channel> observation=<M>` line names the observation a turn
    started from; the matching `seed yield <channel> wake=<N>` line records the
    settled result. The wake's sequence is the receipt's wake number.
    """
    wakes: dict[int, tuple[str, str | None]] = {}
    decoded = [(entry, decode_lifecycle(entry)) for entry in entries]
    for entry, control in decoded:
        if control and control.kind == "wake" and control.role == channel:
            wakes[entry.sequence] = (str(control.observation), control.task)
    work = []
    for entry, control in decoded:
        if not control or control.kind != "yield" or control.role != channel or control.result is None:
            continue
        wake = control.wake
        observation, task = wakes.get(wake, (str(wake), None))
        work.append(Work(channel, wake, observation, control.result, task))
    return work


def repeated_no_change(entries: list[FeedEntry], channel: str, count: int = 3) -> list[Work]:
    recent = work_receipts(entries, channel)[-count:]
    same_observation = len({work.observation for work in recent}) == 1 and len({work.task for work in recent}) == 1
    if len(recent) == count and same_observation and all(
            work.result in {"verified", "blocked", "unspecified"} for work in recent):
        return recent
    return []
