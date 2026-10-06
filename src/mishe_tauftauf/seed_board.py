"""Small deterministic view of tasks and work receipts in the shared text tape."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .feed import FeedEntry


TASK_RE = re.compile(r"^\[task\]\s+(\S+)\s+owner=([a-z0-9-]+)(?:\s|$)")
STATE_RE = re.compile(r"^\[(taking|done|dropped)\]\s+(\S+)(?:\s|$)")
WAKE_RE = re.compile(r"^seed wake ([a-z0-9-]+) observation=([1-9][0-9]*)(?: event=[1-9][0-9]*)?(?: task=(\S+))?\Z")
YIELD_RE = re.compile(r"^seed yield ([a-z0-9-]+) wake=([1-9][0-9]*)(?: continue=1)?\Z")
SETTLED_RE = re.compile(r"^Turn settled \(([a-z]+)\);")
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
    for entry in entries:
        if entry.source != "seed" or not entry.body:
            continue
        if match := WAKE_RE.match(entry.body.splitlines()[0]):
            if match.group(1) == channel:
                wakes[entry.sequence] = (match.group(2), match.group(3))
    work = []
    for entry in entries:
        if entry.source != "seed" or not entry.body:
            continue
        lines = entry.body.splitlines()
        if not (match := YIELD_RE.match(lines[0])) or match.group(1) != channel:
            continue
        settled = next((found for line in lines[1:] if (found := SETTLED_RE.match(line))), None)
        if settled is None:
            continue
        wake = int(match.group(2))
        observation, task = wakes.get(wake, (str(wake), None))
        work.append(Work(channel, wake, observation, settled.group(1), task))
    return work


def repeated_no_change(entries: list[FeedEntry], channel: str, count: int = 3) -> list[Work]:
    recent = work_receipts(entries, channel)[-count:]
    same_observation = len({work.observation for work in recent}) == 1 and len({work.task for work in recent}) == 1
    if len(recent) == count and same_observation and all(
            work.result in {"verified", "blocked", "unspecified"} for work in recent):
        return recent
    return []
