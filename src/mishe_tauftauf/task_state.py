"""Feed-backed next steps and one-shot retry predicates for resident tasks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed, FeedEntry
from .observations import validate_slug
from .seed_board import TASK_RE, STATE_RE, CLAIM_RE

EVENT_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
TASK_WAKE_RE = re.compile(r"seed wake ([a-z0-9-]+) observation=\d+(?: event=\d+)? task=(\S+)\Z")
TASK_YIELD_RE = re.compile(r"seed yield ([a-z0-9-]+) wake=(\d+)(?: continue=1)?\Z")


@dataclass(frozen=True)
class TaskState:
    identity: str
    owner: str
    sequence: int
    status: str = "ready"
    next_step: str = "Read acceptance and choose one bounded step."
    reason: str = ""
    evidence: str = ""
    evidence_sha256: str = ""
    progress: str = ""
    retry_event: str | None = None
    retry_at: str | None = None
    attempt_wake: int | None = None
    attempt_observation: int | None = None
    helpers: tuple[str, ...] = ()
    retry_after: int | None = None
    offer_evidence: str = ""
    offer_evidence_sha256: str = ""


def _deadline(value: str) -> datetime:
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("retry deadline must include a timezone")
    return date


def _event(value: str) -> str:
    if not EVENT_RE.fullmatch(value):
        raise ValueError("retry event must be a short token of letters, numbers, dot, colon, dash or underscore")
    return value


def _evidence(path: Path) -> tuple[str, str]:
    try:
        resolved = path.resolve()
        with resolved.open("rb") as handle:
            data = handle.read(1024 * 1024 + 1)
    except OSError as exc:
        raise ValueError(f"evidence file unavailable: {path}: {exc}") from exc
    if not data or len(data) > 1024 * 1024:
        raise ValueError("evidence file must contain 1 byte to 1 MiB")
    return str(resolved), hashlib.sha256(data).hexdigest()


def states(entries: list[FeedEntry]) -> dict[str, TaskState]:
    result = {}
    closed = set()
    for entry in entries:
        first = entry.body.splitlines()[0].lstrip(" \t") if entry.body else ""
        if match := TASK_RE.match(first):
            identity, owner = match.groups()
            if identity not in result and identity not in closed:
                result[identity] = TaskState(identity, owner, entry.sequence)
        elif match := STATE_RE.match(first):
            if match.group(1) in {"done", "dropped"}:
                result.pop(match.group(2), None)
                closed.add(match.group(2))
        elif entry.source == "seed" and (match := CLAIM_RE.match(first)):
            old = result.get(match.group(1))
            if old is None or old.owner != match.group(3) or match.group(2) not in old.helpers:
                raise ValueError(f"invalid helper claim at entry {entry.sequence}")
            result[old.identity] = replace(old, owner=match.group(2), helpers=())
        if not entry.body.startswith("[task-state] "):
            continue
        try:
            first, payload = entry.body.split("\n", 1)
            identity = first.removeprefix("[task-state] ")
            data = json.loads(payload.splitlines()[0])
            if not isinstance(data, dict) or not isinstance(data.get("helpers", []), (list, tuple)):
                raise ValueError("invalid state fields")
            data["helpers"] = tuple(data.get("helpers", ()))
            state = TaskState(**data)
            if state.identity != identity or state.status not in {"ready", "waiting"}:
                raise ValueError("invalid identity or status")
            if state.retry_event is not None:
                _event(state.retry_event)
            if state.retry_at is not None:
                _deadline(state.retry_at)
            for helper in state.helpers:
                validate_slug(helper)
            if not isinstance(state.next_step, str) or not state.next_step.strip():
                raise ValueError("missing next step")
            if identity not in result:
                raise ValueError("task state has no open task")
            if state.owner != result[identity].owner or entry.source not in {state.owner, "seed"}:
                raise ValueError("task state owner mismatch")
            result[identity] = replace(state, sequence=entry.sequence,
                                      retry_after=state.retry_after or entry.sequence)
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError(f"invalid task state at entry {entry.sequence}: {exc}") from exc
    return result


def _signals(entries: list[FeedEntry]) -> dict[str, int]:
    result = {}
    for entry in entries:
        if entry.body.startswith("[task-event] "):
            first = entry.body.splitlines()[0]
            result[_event(first.removeprefix("[task-event] "))] = entry.sequence
    return result


def eligible(state: TaskState, entries: list[FeedEntry], now: datetime | None = None) -> bool:
    if state.status == "ready":
        return True
    now = now or datetime.now(timezone.utc)
    return bool((state.retry_event and _signals(entries).get(state.retry_event, 0) > (state.retry_after or state.sequence))
                or (state.retry_at and now >= _deadline(state.retry_at)))


def select_task(entries: list[FeedEntry], owner: str, now: datetime | None = None) -> TaskState | None:
    pending = {}
    for entry in entries:
        if entry.source != "seed":
            continue
        first = entry.body.splitlines()[0]
        if match := TASK_WAKE_RE.fullmatch(first):
            pending[(match.group(1), entry.sequence)] = match.group(2)
        elif match := TASK_YIELD_RE.fullmatch(first):
            pending.pop((match.group(1), int(match.group(2))), None)
    reserved = set(pending.values())
    plans = sorted((state for state in states(entries).values() if state.identity not in reserved),
                   key=lambda state: state.sequence)
    own = [state for state in plans if state.owner == owner and eligible(state, entries, now)]
    offered = [state for state in plans if owner in state.helpers and eligible(state, entries, now)]
    return next(iter(own or offered), None)


def _owned(entries: list[FeedEntry], identity: str, owner: str) -> TaskState:
    state = states(entries).get(identity)
    if state is None:
        raise ValueError(f"task {identity} is not open")
    if state.owner != validate_slug(owner):
        raise ValueError(f"task {identity} belongs to owner {state.owner}")
    return state


def _append(home: Path, source: str, state: TaskState) -> FeedEntry:
    return Feed(home).append(source, f"[task-state] {state.identity}\n" + json.dumps(asdict(state), sort_keys=True) +
                             f"\nTask {state.identity} is {state.status}, owned by {state.owner}; "
                             f"next step: {state.next_step}. " +
                             (f"Waiting because {state.reason}. " if state.reason else f"Progress: {state.progress}. ") +
                             f"Evidence: {state.evidence or 'pending delivered-attempt handoff'}; "
                             f"retry: {state.retry_event or state.retry_at or 'new checked next step'}.")


def set_step(home: Path, identity: str, owner: str, next_step: str, progress: str, evidence: Path) -> FeedEntry:
    from .seed import _lock

    if not next_step.strip() or not progress.strip():
        raise ValueError("next step and concrete progress are required")
    path, digest = _evidence(evidence)
    with _lock(home):
        old = _owned(Feed(home).entries(), identity, owner)
        if old.next_step == next_step.strip() and old.evidence_sha256 == digest:
            raise ValueError("unchanged step and evidence; record a wait predicate instead")
        return _append(home, owner, replace(old, status="ready", next_step=next_step.strip(),
                       progress=progress.strip(), evidence=path, evidence_sha256=digest,
                       reason="", retry_event=None, retry_at=None, retry_after=None))


def wait_for(home: Path, identity: str, owner: str, next_step: str, reason: str, evidence: Path,
             *, retry_event: str | None = None, retry_at: str | None = None) -> FeedEntry:
    from .seed import _lock

    if not next_step.strip() or not reason.strip() or not (retry_event or retry_at):
        raise ValueError("waiting requires next step, reason, and retry event or deadline")
    if retry_event:
        _event(retry_event)
    if retry_at:
        _deadline(retry_at)
    path, digest = _evidence(evidence)
    with _lock(home):
        old = _owned(Feed(home).entries(), identity, owner)
        return _append(home, owner, replace(old, status="waiting", next_step=next_step.strip(),
                       reason=reason.strip(), evidence=path, evidence_sha256=digest,
                       retry_event=retry_event, retry_at=retry_at, retry_after=None))


def offer(home: Path, identity: str, owner: str, helpers: list[str], evidence: Path) -> FeedEntry:
    from .seed import _lock

    roles = tuple(dict.fromkeys(validate_slug(role) for role in helpers))
    if owner in roles:
        raise ValueError("helper list must name other owners")
    path, digest = _evidence(evidence)
    with _lock(home):
        old = _owned(Feed(home).entries(), identity, owner)
        return _append(home, owner, replace(old, helpers=roles, offer_evidence=path, offer_evidence_sha256=digest))


def signal(home: Path, event: str, source: str, evidence: Path, reason: str) -> FeedEntry:
    from .seed import _lock

    _event(event)
    if not reason.strip():
        raise ValueError("event needs an explanation of what changed")
    path, digest = _evidence(evidence)
    with _lock(home):
        return Feed(home).append(source, f"[task-event] {event}\n" + json.dumps(
            {"reason": reason.strip(), "evidence": path, "evidence_sha256": digest}, sort_keys=True))


def record_attempt(home: Path, state: TaskState, wake: int, observation: int, *, owner: str | None = None) -> FeedEntry:
    """Called inside the supervisor's seed lock, before delivering the wake."""
    current = states(Feed(home).entries()).get(state.identity)
    if current is None or current.sequence != state.sequence:
        raise ValueError("task state changed before attempt; reconcile before delivery")
    if owner is not None and owner != state.owner:
        if owner not in state.helpers:
            raise ValueError("owner is not an offered helper")
        Feed(home).append("seed", f"[task-claim] {state.identity} owner={owner} previous={state.owner}\n"
                          f"The supervisor assigned the offered ready step to {owner} before delivery; "
                          "other minds must preserve its active work.")
        state = replace(state, owner=owner, helpers=())
    return _append(home, "seed", replace(state, status="waiting", retry_event=None, retry_at=None, retry_after=None,
                   reason="Attempt delivered; record checked progress and next step, or a retry predicate.",
                   attempt_wake=wake, attempt_observation=observation))


def lines(entries: list[FeedEntry], owner: str | None = None) -> list[str]:
    output = []
    for state in sorted(states(entries).values(), key=lambda state: state.sequence):
        if owner is not None and state.owner != owner:
            continue
        retry = state.retry_event or state.retry_at or "new checked step required"
        output.append(f"TASK STEP {state.identity} owner={state.owner} state={state.status} "
                      f"attempt={state.attempt_wake or 'none'} observation={state.attempt_observation or 'none'} "
                      f"retry={retry}: {state.next_step}")
        if state.helpers:
            output.append("  OFFERED HELPERS: " + ",".join(state.helpers))
        if state.reason:
            output.append(f"  WAIT: {state.reason}")
    return output
