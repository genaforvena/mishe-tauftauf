"""Feed-backed next steps and one-shot retry predicates for resident tasks."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import ExitStack
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed, FeedEntry
from .observations import validate_slug
from .seed_board import TASK_RE, STATE_RE, CLAIM_RE
from .records import payload as record_payload

EVENT_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
from .chat_protocol import decode_lifecycle


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
    parent: str | None = None
    managed: bool = False
    activity: str = "open"
    producer: str = ""
    alternative: str | None = None
    retry_task: str | None = None
    delivery: str = ""
    delivery_token: str = ""


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


def validate_control(source: str, body: str) -> None:
    """Reject malformed typed control before its bytes enter the shared tape."""
    if body.startswith("[landing] "):
        from .landing import validate
        validate(source, body)
        return
    try:
        first, rest = body.split("\n", 1)
        if first.startswith("[task-claim] ") and CLAIM_RE.fullmatch(first):
            if source != "seed" or not CLAIM_RE.fullmatch(first):
                raise ValueError("invalid claim")
            return
        tag, identity = first.split(" ", 1)
        _event(identity)
        data = json.loads(rest.splitlines()[0])
        if not isinstance(data, dict):
            raise ValueError("control payload must be an object")
        if tag == "[task-event]":
            if not data.get("reason") or not data.get("evidence"):
                raise ValueError("event requires reason and evidence")
            digest = data.get("evidence_sha256", "")
        elif tag in {"[task-state]", "[task-add]", "[task-close]", "[task-reopen]", "[task-claim]"}:
            if tag == "[task-claim]":
                validate_slug(data.pop("previous_owner"))
            if not isinstance(data.get("helpers", []), (list, tuple)):
                raise ValueError("helpers must be a list")
            state = TaskState(**data)
            if state.identity != identity or state.status not in {"ready", "waiting", "done", "dropped"}:
                raise ValueError("invalid task identity or status")
            if not state.next_step.strip() or source not in {state.owner, "seed", "operator"}:
                raise ValueError("invalid task owner or next step")
            validate_slug(state.owner)
            for helper in state.helpers:
                validate_slug(helper)
            if state.retry_event:
                _event(state.retry_event)
            if state.retry_at:
                _deadline(state.retry_at)
            if state.retry_task:
                _event(state.retry_task)
            if state.delivery:
                _event(state.delivery)
                _event(state.delivery_token)
                if state.owner != "genome":
                    raise ValueError("integration task belongs to genome")
            if state.parent:
                _event(state.parent)
            if state.producer:
                validate_slug(state.producer)
            if tag == "[task-claim]" and (source != state.owner or not state.attempt_wake or state.activity != "taking"):
                raise ValueError("claim requires its mind's active wake and selection")
            digest = state.evidence_sha256
            if tag != "[task-state]" and not state.evidence:
                raise ValueError("lifecycle operation requires evidence")
        else:
            raise ValueError("unrecognized tag")
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid evidence digest")
        if tag != "[task-state]" and not digest:
            raise ValueError("missing evidence digest")
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f"invalid task control: {exc}") from exc


def registry(entries: list[FeedEntry]) -> dict[str, TaskState]:
    """One lifecycle projection for scheduling and display, including terminal IDs."""
    result = {}
    active = {}
    for entry in entries:
        first = entry.body.splitlines()[0].lstrip(" \t") if entry.body else ""
        control = decode_lifecycle(entry)
        if control:
            if control.kind == "wake":
                active[control.role] = entry.sequence
            elif control.kind == "yield" and active.get(control.role) == control.wake:
                active.pop(control.role, None)
        if match := TASK_RE.match(first):
            identity, owner = match.groups()
            if identity not in result:
                result[identity] = TaskState(identity, owner, entry.sequence)
        elif match := STATE_RE.match(first):
            action, identity = match.groups()
            old = result.get(identity)
            # The owner and the operator may transition a task. The automated CI
            # follower ("sync") is also accepted: it opens a kernel-sync task on a
            # linked-site hold and closes it with "[done]" after the replant.
            if (old and old.status not in {"done", "dropped"}
                    and entry.source in {old.owner, "operator", "sync"}):
                if action == "taking":
                    result[identity] = replace(old, activity="taking")
                elif not old.managed:
                    result[identity] = replace(old, status=action, sequence=entry.sequence)
        elif entry.source == "seed" and (match := CLAIM_RE.match(first)):
            old = result.get(match.group(1))
            if old is None or old.owner != match.group(3) or match.group(2) not in old.helpers:
                raise ValueError(f"invalid helper claim at entry {entry.sequence}")
            result[old.identity] = replace(old, owner=match.group(2), helpers=())
        tag = next((tag for tag in ("task-state", "task-add", "task-close", "task-reopen", "task-claim")
                    if entry.body.startswith(f"[{tag}] ")), None)
        if tag == "task-claim" and CLAIM_RE.fullmatch(first):
            continue
        if tag is None:
            continue
        # Historical notes used this reserved tag before writers validated it.
        # Only payload-free task-state notes are ignorable; corrupt JSON is not.
        if tag == "task-state" and "\n" not in entry.body:
            continue
        try:
            first, payload = entry.body.split("\n", 1)
            identity = first.removeprefix(f"[{tag}] ")
            data = dict(record_payload(entry))
            previous_owner = data.pop("previous_owner", None) if tag == "task-claim" else None
            if not isinstance(data, dict) or not isinstance(data.get("helpers", []), (list, tuple)):
                raise ValueError("invalid state fields")
            if tag == "task-state":
                known = {field.name for field in fields(TaskState)}
                data = {key: value for key, value in data.items() if key in known}
            data["helpers"] = tuple(data.get("helpers", ()))
            state = TaskState(**data)
            if state.identity != identity or state.status not in {"ready", "waiting", "done", "dropped"}:
                raise ValueError("invalid identity or status")
            if state.retry_event is not None:
                _event(state.retry_event)
            if state.retry_at is not None:
                _deadline(state.retry_at)
            for helper in state.helpers:
                validate_slug(helper)
            if not isinstance(state.next_step, str) or not state.next_step.strip():
                raise ValueError("missing next step")
            old = result.get(identity)
            if tag == "task-state" and old is not None and old.status in {"done", "dropped"}:
                continue
            if tag == "task-claim":
                if (old is None or old.status in {"done", "dropped"} or old.owner != previous_owner
                        or state.owner != entry.source or active.get(state.owner) != state.attempt_wake
                        or state.status != "waiting" or state.activity != "taking"):
                    raise ValueError("invalid mind claim or active wake")
            elif tag == "task-add":
                if old is not None or entry.source not in {state.owner, "operator"}:
                    raise ValueError("task already exists or creator mismatch")
                if state.parent and (state.parent not in result or result[state.parent].status in {"done", "dropped"}):
                    raise ValueError("parent task is not open")
                if state.parent:
                    result[state.parent] = replace(result[state.parent], managed=True)
            else:
                if old is None or (old.status in {"done", "dropped"} and tag != "task-reopen"):
                    raise ValueError("task state has no open task")
                if state.owner != old.owner or entry.source not in {state.owner, "seed", "operator"}:
                    raise ValueError("task state owner mismatch")
                if tag == "task-reopen" and (old.status not in {"done", "dropped"} or not state.managed):
                    raise ValueError("reopen requires a terminal task")
            if tag in {"task-add", "task-close", "task-reopen"}:
                if not state.evidence or not re.fullmatch(r"[0-9a-f]{64}", state.evidence_sha256):
                    raise ValueError("lifecycle operation requires checked evidence")
                if tag == "task-close" and state.status not in {"done", "dropped"}:
                    raise ValueError("close requires terminal status")
            elif state.status not in {"ready", "waiting"}:
                raise ValueError("step cannot close a task")
            result[identity] = replace(state, sequence=entry.sequence,
                                      retry_after=state.retry_after or entry.sequence)
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError(f"invalid task state at entry {entry.sequence}: {exc}") from exc
    return result


def states(entries: list[FeedEntry]) -> dict[str, TaskState]:
    return {identity: state for identity, state in registry(entries).items()
            if state.status not in {"done", "dropped"}}


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
    completed = registry(entries).get(state.retry_task) if state.retry_task else None
    return bool((completed and completed.status == "done" and completed.sequence > (state.retry_after or state.sequence))
                or (state.retry_event and _signals(entries).get(state.retry_event, 0) > (state.retry_after or state.sequence))
                or (state.retry_at and now >= _deadline(state.retry_at)))


def pending_tasks(entries: list[FeedEntry]) -> dict[tuple[str, int], str]:
    pending = {}
    for entry in entries:
        first = entry.body.splitlines()[0]
        if entry.body.startswith("[task-claim] ") and not CLAIM_RE.fullmatch(first):
            data = record_payload(entry)
            pending[(data["owner"], data["attempt_wake"])] = data["identity"]
        else:
            control = decode_lifecycle(entry)
            if control and control.kind == "wake" and control.task is not None:
                pending[(control.role, entry.sequence)] = control.task
            elif control and control.kind == "yield":
                pending.pop((control.role, control.wake), None)
    return pending


def active_wakes(entries: list[FeedEntry]) -> dict[str, int]:
    active = {}
    for entry in entries:
        control = decode_lifecycle(entry)
        if control is None:
            continue
        if control.kind == "wake":
            active[control.role] = entry.sequence
        elif control.kind == "yield" and active.get(control.role) == control.wake:
            active.pop(control.role, None)
    return active


def candidates(entries: list[FeedEntry], now: datetime | None = None) -> list[TaskState]:
    """Advisory shared work; a role is a responsibility, not an eligibility partition."""
    reserved = set(pending_tasks(entries).values())
    return sorted((s for s in states(entries).values()
                   if s.identity not in reserved and eligible(s, entries, now)), key=lambda s: s.sequence)


def board(entries: list[FeedEntry]) -> list[str]:
    output = ["SHARED TASK BOARD — choose useful work and claim before acting."]
    priorities = {s.identity: s.sequence for s in states(entries).values() if s.delivery}
    ready = {s.identity for s in candidates(entries)}
    reserved = set(pending_tasks(entries).values())
    for state in sorted(states(entries).values(), key=lambda s: (priorities.get(s.identity, float("inf")), s.sequence)):
        if state.identity in priorities:
            output.append(f"MAIN INTEGRATION PRIORITY: {state.identity}; ready integration position {priorities[state.identity]}.")
        availability = "reserved" if state.identity in reserved else "ready" if state.identity in ready else "waiting"
        output.append(f"{state.identity}: {availability}; responsible mind {state.owner}. Next: {state.next_step}")
        if state.reason:
            output.append(f"  Reason: {state.reason}")
        if state.evidence:
            output.append(f"  Evidence: {state.evidence}")
        if state.retry_task:
            dependency = registry(entries).get(state.retry_task)
            output.append(f"  Producer {state.retry_task}: {dependency.status if dependency else 'UNKNOWN missing record'}.")
            if dependency and dependency.status == "done":
                output.append(f"  Completed result: {dependency.progress}. Evidence: {dependency.evidence}")
        elif state.retry_event or state.retry_at:
            output.append(f"  Retry: {state.retry_event or state.retry_at}.")
    return output


def _claim_delivery_owner(home: Path, identity: str, owner: str) -> None:
    # The claim caller holds delivery then seed locks at mutation admission.
    # An initial unlocked read supplies early feedback only.
    if (home / "deliveries" / f"{identity}.json").exists():
        from .delivery import load
        record = load(home, identity)
        if owner != record["owner"]:
            raise ValueError("author retains delivery ownership; choose another eligible task")


def claim(home: Path, identity: str, owner: str, wake: int, reason: str, evidence: Path) -> FeedEntry:
    """Atomically reserve the mind's own choice; one record transfers and consumes it."""
    from .seed import _lock
    from .delivery import _lock as delivery_lock

    _event(identity)
    validate_slug(owner)
    if not reason.strip():
        raise ValueError("claim requires an explanation of why this step is useful")
    path, digest = _evidence(evidence)
    initial = Feed(home).entries()
    prior = registry(initial).get(identity)
    if active_wakes(initial).get(owner) != wake:
        raise ValueError("claim requires this mind's exact active wake")
    if prior is None or prior.status in {"done", "dropped"}:
        raise ValueError("task is missing or terminal; completed work cannot be claimed")
    _claim_delivery_owner(home, identity, owner)
    if prior.delivery and owner != "genome":
        raise ValueError("main integration is serialized by genome; choose another eligible task")
    if identity in pending_tasks(initial).values() or (owner, wake) in pending_tasks(initial):
        raise ValueError("task or wake is already reserved by an active claim")
    if not eligible(prior, initial):
        raise ValueError("task prerequisite unchanged; wait for its exact retry")
    from .post_check import require
    from .coordination_checks import episode
    selection = episode(home, owner, reason, context={"task": identity, "wake": wake, "evidence": path, "evidence_sha256": digest})
    require(home, owner, reason, context=selection, stage="selection")
    with _lock(home), ExitStack() as commit_locks:
        entries = Feed(home).entries()
        if active_wakes(entries).get(owner) != wake:
            raise ValueError("claim requires this mind's exact active wake")
        old = registry(entries).get(identity)
        if old is None or old.status in {"done", "dropped"}:
            raise ValueError("task is missing or terminal; completed work cannot be claimed")
        _claim_delivery_owner(home, identity, owner)
        if old.delivery and owner != "genome":
            raise ValueError("main integration is serialized by genome; choose another eligible task")
        pending = pending_tasks(entries)
        if identity in pending.values() or (owner, wake) in pending:
            raise ValueError("task or wake is already reserved by an active claim")
        if not eligible(old, entries):
            raise ValueError("task prerequisite unchanged; wait for its exact retry")
        if old.sequence != prior.sequence:
            raise ValueError("task changed during selection checks; reconcile and resubmit")
        if _evidence(Path(path))[1] != digest:
            raise ValueError("claim evidence changed during selection review; reconcile and resubmit")
        state = replace(old, owner=owner, helpers=(), status="waiting", activity="taking",
                        attempt_wake=wake, attempt_observation=None, retry_event=None, retry_at=None,
                        retry_task=None, retry_after=None, progress=reason.strip(),
                        evidence=path, evidence_sha256=digest,
                        reason="The mind claimed this step; reconcile its effects before another attempt.")
        def claim_commit_guard():
            commit_locks.enter_context(delivery_lock(home))
            _claim_evidence_guard(path, digest)
            _claim_delivery_owner(home, identity, owner)
            if old.delivery:
                from .delivery import load
                current = load(home, old.delivery)
                token = f"{current['head']}:{current['transition']}"
                if current["phase"] != "ready" or token != old.delivery_token:
                    raise ValueError("delivery changed during claim admission; reconcile the current transition")
        data = asdict(state)
        data["previous_owner"] = old.owner
        return Feed(home).append_task_control(owner, f"[task-claim] {identity}\n" + json.dumps(data, sort_keys=True) +
            f"\n{owner} chose {identity} for wake {wake}: {reason.strip()} "
            f"The selected step is {old.next_step} Evidence: {path}. "
            "Other minds must preserve this active attempt until its handoff settles.", commit_guard=claim_commit_guard)


def _claim_evidence_guard(path, digest):
    if _evidence(Path(path))[1] != digest:
        raise ValueError("claim evidence changed during final publication admission; reconcile and resubmit")


def select_task(entries: list[FeedEntry], owner: str, now: datetime | None = None) -> TaskState | None:
    reserved = set(pending_tasks(entries).values())
    plans = sorted((state for state in states(entries).values() if state.identity not in reserved),
                   key=lambda state: state.sequence)
    own = [state for state in plans if state.owner == owner and eligible(state, entries, now)]
    offered = [state for state in plans if owner in state.helpers and eligible(state, entries, now)]
    integrations = [state for state in own if state.delivery]
    if integrations:
        return integrations[0]
    return next(iter(own or offered), None)


def independent_opportunity(entries: list[FeedEntry], owner: str) -> str | None:
    """Allow one alternative-work decision per stable checked waiting backlog."""
    waiting = [state for state in states(entries).values() if state.owner == owner]
    if not waiting or any(state.status != "waiting" or not (state.retry_event or state.retry_at or state.retry_task)
                          for state in waiting):
        return None
    inputs = sorted((s.identity, s.next_step, s.reason, s.retry_event, s.retry_at, s.retry_task, s.alternative)
                    for s in waiting)
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    first = f"[task-opportunity] owner={owner} waiting={digest}"
    if any((control := decode_lifecycle(entry)) and control.kind == "wake" and control.role == owner
           and first in entry.body.splitlines() for entry in entries):
        return None
    return digest


def _owned(entries: list[FeedEntry], identity: str, owner: str) -> TaskState:
    state = states(entries).get(identity)
    if state is None:
        raise ValueError(f"task {identity} is not open")
    if state.owner != validate_slug(owner):
        raise ValueError(f"task {identity} belongs to owner {state.owner}")
    return state


def _append(home: Path, source: str, state: TaskState, tag: str = "task-state", *, commit_guard=None) -> FeedEntry:
    before = registry(Feed(home).entries()).get(state.identity)
    transition = (f"moves from {before.status} to {state.status}" if before and before.status != state.status
                  else f"remains {state.status}" if before else f"registers as {state.status}")
    return Feed(home).append_task_control(source, f"[{tag}] {state.identity}\n" + json.dumps(asdict(state), sort_keys=True) +
                             f"\nTask {state.identity} {transition}, owned by {state.owner}; "
                             f"next step: {state.next_step}. " +
                             (f"Waiting because {state.reason}. " if state.reason else f"Progress: {state.progress}. ") +
                             f"Evidence: {state.evidence or 'pending delivered-attempt handoff'}; "
                             f"retry: {state.retry_event or state.retry_at or state.retry_task or 'new checked next step'}.", commit_guard=commit_guard)


def set_step(home: Path, identity: str, owner: str, next_step: str, progress: str, evidence: Path) -> FeedEntry:
    from .seed import _lock

    if not next_step.strip() or not progress.strip():
        raise ValueError("next step and concrete progress are required")
    path, digest = _evidence(evidence)
    with _lock(home):
        old = _owned(Feed(home).entries(), identity, owner)
        if old.delivery:
            raise ValueError("integration readiness belongs to delivery check; progress prose cannot rearm it")
        if old.next_step == next_step.strip() and (old.evidence_sha256 == digest or old.progress == progress.strip()):
            raise ValueError("unchanged step and evidence; record a wait predicate instead")
        return _append(home, owner, replace(old, status="ready", next_step=next_step.strip(),
                       progress=progress.strip(), evidence=path, evidence_sha256=digest,
                       reason="", retry_event=None, retry_at=None, retry_task=None, retry_after=None,
                       producer="", alternative=None))


def wait_for(home: Path, identity: str, owner: str, next_step: str, reason: str, evidence: Path,
             *, retry_event: str | None = None, retry_at: str | None = None,
             retry_task: str | None = None, producer: str | None = None,
             alternative: str | None = None, commit_guard=None) -> FeedEntry:
    from .seed import _lock

    if not next_step.strip() or not reason.strip() or not (retry_event or retry_at or retry_task):
        raise ValueError("waiting requires next step, reason, and retry event or deadline")
    if retry_event:
        _event(retry_event)
    if retry_at:
        _deadline(retry_at)
    path, digest = _evidence(evidence)
    with _lock(home):
        entries = Feed(home).entries()
        old = _owned(entries, identity, owner)
        if old.delivery:
            raise ValueError("integration readiness belongs to delivery check; use delivery integrate")
        plans = registry(entries)
        status = "waiting"
        if retry_task:
            dependency = plans.get(retry_task)
            if dependency is None or dependency.status == "dropped":
                raise ValueError("retry task must name a producer that can complete")
            if producer and dependency.owner != producer:
                raise ValueError("retry task producer mismatch")
            producer = dependency.owner
            pending = [retry_task]
            visited = set()
            while pending:
                cursor = pending.pop()
                if cursor == identity:
                    raise ValueError("task prerequisite cycle")
                if cursor in visited:
                    continue
                visited.add(cursor)
                if cursor in plans and plans[cursor].status not in {"done", "dropped"}:
                    if plans[cursor].retry_task:
                        pending.append(plans[cursor].retry_task)
                    pending.extend(s.identity for s in plans.values()
                                   if s.parent == cursor and s.status not in {"done", "dropped"})
            if dependency.status == "done":
                status = "ready"
        if alternative:
            candidate = plans.get(alternative)
            if not candidate or candidate.parent != identity or candidate.owner != owner or candidate.status != "ready":
                raise ValueError("alternative must be an owned ready child of this task")
        return _append(home, owner, replace(old, status=status, next_step=next_step.strip(),
                       reason=reason.strip(), evidence=path, evidence_sha256=digest,
                       retry_event=retry_event, retry_at=retry_at, retry_task=retry_task, retry_after=None,
                       producer=validate_slug(producer or owner), alternative=alternative), commit_guard=commit_guard)


def offer(home: Path, identity: str, owner: str, helpers: list[str], evidence: Path) -> FeedEntry:
    from .seed import _lock

    roles = tuple(dict.fromkeys(validate_slug(role) for role in helpers))
    if owner in roles:
        raise ValueError("helper list must name other owners")
    path, digest = _evidence(evidence)
    with _lock(home):
        old = _owned(Feed(home).entries(), identity, owner)
        if old.delivery:
            raise ValueError("delivery integration is serialized by genome; it cannot be offered")
        return _append(home, owner, replace(old, helpers=roles, offer_evidence=path, offer_evidence_sha256=digest))


def signal(home: Path, event: str, source: str, evidence: Path, reason: str, *, commit_guard=None) -> FeedEntry:
    from .seed import _lock

    _event(event)
    if not reason.strip():
        raise ValueError("event needs an explanation of what changed")
    path, digest = _evidence(evidence)
    with _lock(home):
        return Feed(home).append_task_control(source, f"[task-event] {event}\n" + json.dumps(
            {"reason": reason.strip(), "evidence": path, "evidence_sha256": digest}, sort_keys=True),
            commit_guard=commit_guard)


def record_attempt(home: Path, state: TaskState, wake: int, observation: int, *, owner: str | None = None) -> FeedEntry:
    """Called inside the supervisor's seed lock, before delivering the wake."""
    current = states(Feed(home).entries()).get(state.identity)
    if current is None or current.sequence != state.sequence:
        raise ValueError("task state changed before attempt; reconcile before delivery")
    if owner is not None and owner != state.owner:
        if owner not in state.helpers:
            raise ValueError("owner is not an offered helper")
        Feed(home).append_task_control("seed", f"[task-claim] {state.identity} owner={owner} previous={state.owner}\n"
                          f"The supervisor assigned the offered ready step to {owner} before delivery; "
                          "other minds must preserve its active work.")
        state = replace(state, owner=owner, helpers=())
    return _append(home, "seed", replace(state, status="waiting", retry_event=None, retry_at=None, retry_task=None, retry_after=None,
                   reason="Attempt delivered; record checked progress and next step, or a retry predicate.",
                   attempt_wake=wake, attempt_observation=observation))


def lines(entries: list[FeedEntry], owner: str | None = None) -> list[str]:
    output = []
    for state in sorted(states(entries).values(), key=lambda state: state.sequence):
        if owner is not None and state.owner != owner:
            continue
        retry = state.retry_event or state.retry_at or state.retry_task or "new checked step required"
        output.append(f"TASK STEP {state.identity} owner={state.owner} state={state.status} "
                      f"attempt={state.attempt_wake or 'none'} observation={state.attempt_observation or 'none'} "
                      f"retry={retry}: {state.next_step}")
        if state.parent:
            output.append(f"  PARENT: {state.parent} — child completion does not complete this goal")
        if state.evidence:
            output.append(f"  EVIDENCE: {state.evidence} sha256={state.evidence_sha256}")
        if state.producer:
            output.append(f"  PREREQUISITE PRODUCER: {state.producer}")
        if state.alternative:
            output.append(f"  ADMISSIBLE WORK: {state.alternative} — final acceptance remains waiting")
        if state.helpers:
            output.append("  OFFERED HELPERS: " + ",".join(state.helpers))
        if state.reason:
            output.append(f"  WAIT: {state.reason}")
    return output


def add_task(home: Path, identity: str, owner: str, next_step: str, reason: str, evidence: Path,
             *, parent: str | None = None, commit_guard=None) -> FeedEntry:
    from .seed import _lock

    _event(identity)
    validate_slug(owner)
    if not next_step.strip() or not reason.strip():
        raise ValueError("task requires a concrete next step and reason")
    path, digest = _evidence(evidence)
    with _lock(home):
        plans = registry(Feed(home).entries())
        if identity in plans:
            raise ValueError("task already exists; use task reopen for a terminal task")
        if parent and (parent not in plans or plans[parent].status in {"done", "dropped"}):
            raise ValueError("parent task is not open")
        return _append(home, owner, TaskState(identity, owner, 0, managed=True, parent=parent,
                       next_step=next_step.strip(), progress=reason.strip(), evidence=path,
                       evidence_sha256=digest), "task-add", commit_guard=commit_guard)


def reopen(home: Path, identity: str, owner: str, next_step: str, reason: str, evidence: Path) -> FeedEntry:
    from .seed import _lock

    if not next_step.strip() or not reason.strip():
        raise ValueError("reopen requires a next step and reason")
    path, digest = _evidence(evidence)
    with _lock(home):
        plans = registry(Feed(home).entries())
        old = plans.get(identity)
        if not old or old.status not in {"done", "dropped"}:
            raise ValueError("task is not terminal; record task step instead")
        if old.delivery:
            raise ValueError("integration readiness belongs to delivery check; use a new author candidate")
        if old.owner != validate_slug(owner):
            raise ValueError("task owner mismatch")
        if old.parent and (old.parent not in plans or plans[old.parent].status in {"done", "dropped"}):
            raise ValueError("reopen the parent goal before reopening its child")
        return _append(home, owner, replace(old, status="ready", managed=True, activity="open",
                       next_step=next_step.strip(), progress=reason.strip(), evidence=path, evidence_sha256=digest,
                       reason="", retry_event=None, retry_at=None, retry_task=None, retry_after=None,
                       attempt_wake=None, attempt_observation=None, helpers=(), alternative=None, producer=""), "task-reopen")


def finish(home: Path, identity: str, owner: str, result: str, evidence: Path, *, commit_guard=None) -> FeedEntry:
    from .seed import _lock

    if not result.strip():
        raise ValueError("finish requires a checked result")
    path, digest = _evidence(evidence)
    with _lock(home):
        entries = Feed(home).entries()
        old = _owned(entries, identity, owner)
        if old.delivery:
            raise ValueError("use delivery integrate to complete a fact-owned integration")
        if any(state.parent == identity for state in states(entries).values()):
            raise ValueError("unfinished child tasks prevent parent completion")
        return _append(home, owner, replace(old, status="done", managed=True, progress=result.strip(),
                       evidence=path, evidence_sha256=digest, retry_event=None, retry_at=None,
                       retry_task=None, reason="", helpers=(), alternative=None), "task-close", commit_guard=commit_guard)
