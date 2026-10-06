"""Durable action/effect boundary for the inference loop.

Owns the effect half of the loop only: allocate a stable operation ID before
dispatch, persist the dispatch intent before acting, then persist the observed
outcome. Context assembly, provider calls, capability selection and the
continue/complete decision live in `inference_loop.py`, not here.

Recovery scope is local filesystem and process crash only. A crash between a
committed intent and its outcome leaves an operation `unknown` for a fresh
process to reconcile; it is never silently retried. This module does not claim
power-loss durability, exactly-once semantics, or general concurrent safety.
`os.fsync` is an actual code-level operation here, not a mount choice; flush-only
would not be crash-durable. A torn or malformed journal line is preserved and
fails closed rather than being treated as an absent record. Corruption
diagnostics remain importable on the supported Python 3.10 baseline.

The store is three append-only JSONL journals — intents, dispatch starts,
outcomes — plus one stable lock file; no new durability mechanism. One writer at
a time holds the store lock across the whole read/check/start/execute/outcome
sequence.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

_EFFECT_STATUSES = ("completed", "partial", "unknown")
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_RESERVATION_MARKER = "reserved"


def _reservation_digest(arguments: Mapping[str, Any], *, authority: str,
                        capability: str, capability_version: str,
                        native_call: Mapping[str, Any] | None,
                        budget: Mapping[str, Any] | None = None) -> str:
    """The digest of a reservation, which binds content without a provider call.

    The request carries the marker instead of the provider's call, because a
    reservation is made before any call id exists. The digest still binds the
    real capability, arguments, authority and version, so a reservation and a
    later dispatch of the same id to different content still conflict as
    `ChangedContentReuse`.
    """
    return request_digest({"reservation": _RESERVATION_MARKER}, authority,
                          arguments=arguments, capability=capability,
                          budget=budget, native_call=native_call)


class ChangedContentReuse(RuntimeError):
    """Same operation ID was requested with different bound content or authority.

    Raised before any write, so a caller learns the collision without a new
    effect being possible.
    """


class AlreadyExecuted(RuntimeError):
    """An outcome record already exists for this operation ID.

    Raised by `allocate` when a caller-chosen ID collides with a finished
    operation. `dispatch` on a completed or started operation instead returns the
    recorded outcome, so an idempotent caller need not distinguish a recovered
    store from a fresh one.
    """


class ConcurrentClaim(RuntimeError):
    """A writer disputed an operation ID another writer had allocated.

    Reported as an `unknown` outcome with `failure="concurrent-claim"` rather than
    raised, so a caller learns the durable state of the operation instead of an
    exception. Provided for callers that want that failure as a type.
    """


class StoreUnavailable(RuntimeError):
    """Another writer holds the store, or the store is not a private regular file.

    Distinct from a concurrent *claim*: the store lock is exclusive, so a peer
    that cannot take it never reads or writes at all.
    """


class JournalCorrupt(RuntimeError):
    """A journal line is malformed, truncated or duplicated.

    Raised by readers as soon as the damage is seen, so no further dispatch is
    based on a half-readable store. The bytes are left in place as evidence.
    """


@dataclass(frozen=True)
class EffectIntent:
    """A dispatch allocated before it acts. Durable from the moment it exists."""
    operation_id: str
    capability: str
    capability_version: str
    arguments: Mapping[str, Any]
    request_digest: str
    authority: str
    writer_id: str
    budget: Mapping[str, Any] | None
    native_call: Mapping[str, Any] | None = None
    reservation: bool = False

    def to_record(self) -> dict[str, Any]:
        record = {"record_type": "intent", "operation_id": self.operation_id,
                  "capability": self.capability,
                  "capability_version": self.capability_version,
                  "arguments": _snapshot(self.arguments),
                  "request_digest": self.request_digest,
                  "authority": self.authority,
                  "writer_id": self.writer_id,
                  "budget": _snapshot(self.budget) if self.budget is not None else None,
                  "native_call": _snapshot(self.native_call)
                  if self.native_call is not None else None}
        if self.reservation:
            record["reservation"] = True
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "EffectIntent":
        _require(record, "intent", ("operation_id", "capability", "capability_version",
                                    "arguments", "request_digest", "authority",
                                    "writer_id"))
        return cls(operation_id=record["operation_id"], capability=record["capability"],
                   capability_version=record["capability_version"],
                   arguments=_mapping(record.get("arguments"), "arguments"),
                   request_digest=record["request_digest"],
                   authority=record["authority"], writer_id=record["writer_id"],
                   budget=_mapping(record.get("budget"), "budget"),
                   native_call=_mapping(record.get("native_call"), "native_call"),
                   reservation=bool(record.get("reservation")))


@dataclass(frozen=True)
class EffectOutcome:
    """What the world holds for one operation ID, as a fresh process would read it."""
    operation_id: str
    status: Literal["completed", "partial", "unknown"]
    result: Any = None
    failure: str | None = None
    reconciliation_ref: str | None = None
    executions: int = 0
    native_call: Mapping[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        return {"record_type": "outcome", "operation_id": self.operation_id,
                "status": self.status, "result": self.result,
                "failure": self.failure, "executions": self.executions,
                "reconciliation_ref": self.reconciliation_ref,
                "native_call": _snapshot(self.native_call)
                if self.native_call is not None else None}

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "EffectOutcome":
        _require(record, "outcome", ("operation_id", "status"))
        status = record["status"]
        if status not in _EFFECT_STATUSES:
            raise JournalCorrupt(f"unexpected effect status {status!r}")
        return cls(operation_id=record["operation_id"], status=status,
                   result=record.get("result"), failure=record.get("failure"),
                   reconciliation_ref=record.get("reconciliation_ref"),
                   executions=int(record.get("executions") or 0),
                   native_call=_mapping(record.get("native_call"), "native_call"))


@dataclass(frozen=True)
class DispatchClaim:
    """The start record: this writer began executing this operation at this point.

    Its presence proves an execution started; without a following outcome it
    proves no more than that. Absence of any record proves not-started only for a
    known healthy store.
    """
    operation_id: str
    writer_id: str
    started_at: float

    def to_record(self) -> dict[str, Any]:
        return {"record_type": "start", "operation_id": self.operation_id,
                "writer_id": self.writer_id, "started_at": self.started_at}

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DispatchClaim":
        _require(record, "start", ("operation_id", "writer_id"))
        return cls(operation_id=record["operation_id"], writer_id=record["writer_id"],
                   started_at=float(record.get("started_at") or 0.0))


def _snapshot(value: Any) -> Any:
    """Return a copy whose later mutation cannot change what was dispatched.

    Nested containers are copied so a caller mutating a dict after `allocate`
    cannot alter the bound content. Keys are sorted so a digest is stable across
    processes.
    """
    if isinstance(value, Mapping):
        return {k: _snapshot(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_snapshot(v) for v in value]
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise JournalCorrupt(f"{name} record is not an object")
    return dict(value)


def _require(record: Mapping[str, Any], record_type: str,
             keys: Sequence[str]) -> None:
    if record.get("record_type") != record_type:
        raise JournalCorrupt(
            f"expected record_type {record_type!r}, got {record.get('record_type')!r}")
    missing = [key for key in keys if key not in record]
    if missing:
        raise JournalCorrupt(f"{record_type} record missing {missing}")


def _canonicalizable(value: Any) -> Any:
    """Reject noncanonical and nonfinite values rather than normalize them lossily."""
    if isinstance(value, Mapping):
        return {k: _canonicalizable(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonicalizable(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("nonfinite float is not canonical")
        return value
    if isinstance(value, str):
        return value
    raise ValueError(f"{type(value).__name__} is not a canonical value")


def canonical_request(request: Mapping[str, Any]) -> str:
    """One serialization for the request, so digests are comparable across processes."""
    return json.dumps(_canonicalizable(request), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def request_digest(request: Mapping[str, Any], authority: str,
                   *, arguments: Mapping[str, Any] | None = None,
                   capability: str = "",
                   budget: Mapping[str, Any] | None = None,
                   native_call: Mapping[str, Any] | None = None) -> str:
    """Bind an operation to the exact content, arguments, authority and call identity.

    Authority is part of the digest so the same arguments under a different
    authority are a different effect, not a reuse. The original call identity is
    part of it so a provider-issued call id cannot be detached from its effect.
    """
    digest = hashlib.sha256()
    digest.update(b"mishe-effect-request-v1\n")
    digest.update(canonical_request(request).encode("utf-8"))
    digest.update(b"\n")
    digest.update(capability.encode("utf-8"))
    digest.update(b"\n")
    digest.update(authority.encode("utf-8"))
    if arguments is not None:
        digest.update(b"\n")
        digest.update(canonical_request(arguments).encode("utf-8"))
    if budget is not None:
        digest.update(b"\n")
        digest.update(canonical_request(budget).encode("utf-8"))
    if native_call is not None:
        digest.update(b"\n")
        digest.update(canonical_request(native_call).encode("utf-8"))
    return digest.hexdigest()


class EffectLedger:
    """Append-only ledger of dispatch intents, starts and observed outcomes.

    One writer at a time holds the store lock across the whole
    read/check/start/execute/outcome sequence, so distinct ledger instances in
    one process and distinct processes cannot both act. `writer_id` is
    provenance: it identifies who wrote a record, and does not bar a successor
    from reconciling, because a successor still cannot replay a started
    operation.
    """

    def __init__(self, store_dir: Path, *, writer_id: str) -> None:
        if not writer_id:
            raise ValueError("writer_id is required")
        self.store_dir = Path(store_dir)
        self.writer_id = writer_id
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.intents_path = self.store_dir / "intents.jsonl"
        self.starts_path = self.store_dir / "starts.jsonl"
        self.outcomes_path = self.store_dir / "outcomes.jsonl"
        self.lock_path = self.store_dir / ".ledger.lock"
        self._lock_depth = 0
        for path in (self.intents_path, self.starts_path, self.outcomes_path):
            if not path.exists():
                path.touch()

    # -- journal primitives ------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Exclusive store lock covering reads, checks, starts, execution, outcomes.

        Nonblocking so a concurrent claim fails explicitly instead of deadlocking
        two peers that both believe they own the operation. The kernel releases
        the lock at process exit, so a crashed writer does not pin the store.
        """
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            reentrant = self._lock_depth > 0
            if not reentrant:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise StoreUnavailable(
                        f"another writer holds {self.lock_path}") from exc
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
                if not reentrant:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _append(self, path: Path, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def _read(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        data = path.read_bytes()
        if not data:
            return rows
        if not data.endswith(b"\n"):
            # A record that lost its terminator means the store was truncated
            # mid-write. No claim may be read from a partial line. The split is
            # on the real newline byte, not the JSON "\\n" escape that a
            # canonical record may legitimately contain inside a value.
            fragment = data[data.rfind(b"\n") + 1:]
            raise JournalCorrupt(
                f"truncated line in {path}: "
                f"{fragment!r}")
        text = data.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line_text = line.strip()
            if not line_text:
                continue
            try:
                record = json.loads(line_text)
            except ValueError as exc:
                raise JournalCorrupt(f"unreadable line in {path}: {exc}") from exc
            if not isinstance(record, dict):
                raise JournalCorrupt(f"non-object line in {path}")
            rows.append(record)
        return rows

    def _read_intents(self) -> dict[str, EffectIntent]:
        by_id: dict[str, EffectIntent] = {}
        for record in self._read(self.intents_path):
            intent = EffectIntent.from_record(record)
            if intent.operation_id in by_id:
                raise JournalCorrupt(f"duplicate intent for {intent.operation_id}")
            by_id[intent.operation_id] = intent
        return by_id

    def _read_starts(self) -> dict[str, DispatchClaim]:
        by_id: dict[str, DispatchClaim] = {}
        for record in self._read(self.starts_path):
            claim = DispatchClaim.from_record(record)
            if claim.operation_id in by_id:
                raise JournalCorrupt(f"duplicate start for {claim.operation_id}")
            by_id[claim.operation_id] = claim
        return by_id

    def _read_outcomes(self) -> dict[str, EffectOutcome]:
        by_id: dict[str, EffectOutcome] = {}
        counts: dict[str, int] = {}
        for record in self._read(self.outcomes_path):
            outcome = EffectOutcome.from_record(record)
            counts[outcome.operation_id] = counts.get(outcome.operation_id, 0) + 1
            by_id[outcome.operation_id] = outcome
        for operation_id, count in counts.items():
            by_id[operation_id] = replace(by_id[operation_id], executions=count)
        return by_id

    # -- public surface ----------------------------------------------------

    def allocate(self, capability: str, capability_version: str,
                 arguments: Mapping[str, Any], *, authority: str,
                 request: Mapping[str, Any], operation_id: str,
                 native_call: Mapping[str, Any] | None = None,
                 budget: Mapping[str, Any] | None = None,
                 digest: str | None = None,
                 reservation: bool = False) -> EffectIntent:
        """Bind an operation ID to content and authority before any effect.

        `operation_id` is the caller's durable identity for this operation,
        allocated by the loop before dispatch and reused by recovery. A same-ID
        request with different content, authority or call identity raises
        `ChangedContentReuse` before any write; a same-ID request with the same
        binding returns the existing intent, never permission to dispatch twice.

        `native_call` carries the provider's call opaque: its `id` must survive
        byte-for-byte into the next native tool result, so the ledger stores it
        beside the operation it belongs to.

        `digest` overrides the content binding when the caller has already
        computed it; `reserve` uses this so a reservation and the dispatch that
        later claims the same id bind identical content. `reservation` marks an
        intent bound with no provider call and no effect, which `drop_reserved`
        may retire.
        """
        if not capability or not capability_version:
            raise ValueError("capability and capability_version are required")
        if not authority:
            raise ValueError("authority is required")
        if not _OPERATION_ID_RE.match(operation_id):
            raise ValueError("operation_id must match [A-Za-z0-9_.:-]+")
        normalized = _snapshot(arguments)
        if not isinstance(normalized, Mapping):
            raise ValueError("arguments must be an object")
        snapshot = _snapshot(request)
        call = _snapshot(native_call) if native_call is not None else None
        if digest is None:
            digest = request_digest(snapshot, authority, arguments=normalized,
                                    capability=capability, budget=budget,
                                    native_call=call)
        with self._locked():
            intents = self._read_intents()
            existing = intents.get(operation_id)
            if existing is not None:
                if operation_id in self._read_outcomes():
                    raise AlreadyExecuted(
                        f"operation_id {operation_id} already has an outcome record")
                if existing.request_digest != digest:
                    raise ChangedContentReuse(
                        f"operation_id {operation_id} is bound to different content")
                return existing
            if operation_id in self._read_outcomes():
                raise AlreadyExecuted(
                    f"operation_id {operation_id} already has an outcome record")
            intent = EffectIntent(operation_id=operation_id, capability=capability,
                                  capability_version=capability_version,
                                  arguments=normalized, request_digest=digest,
                                  authority=authority, writer_id=self.writer_id,
                                  budget=budget, native_call=call,
                                  reservation=bool(reservation))
            self._append(self.intents_path, intent.to_record())
            return intent

    def bind_dispatch(self, capability: str, capability_version: str,
                      arguments: Mapping[str, Any], *, authority: str,
                      obligation: Mapping[str, Any], call: Mapping[str, Any],
                      operation_id: str,
                      budget: Mapping[str, Any] | None) -> EffectIntent:
        """Bind the dispatch request, upgrading a matching reservation if any.

        A reserved id is bound to capability, arguments, authority and version
        with no provider call. When the arriving dispatch binds the same values
        the reservation is upgraded in place: its intent is rewritten to carry
        the dispatch request and the provider call, clearing its reservation
        marker, so `dispatch` then binds it as the recorded intent and starts it
        once. An id that already has an outcome raises `AlreadyExecuted`, and a
        dispatch that binds different values is `ChangedContentReuse` before any
        effect, exactly as a repeated dispatch is.

        The caller's request snapshot is taken here so a mutated caller object
        cannot change what the digest commits to.
        """
        snapshot_request = {"obligation": _snapshot(obligation), "call": _snapshot(call)}
        with self._locked():
            recorded = self._read_intents().get(operation_id)
            digest = request_digest(snapshot_request, authority,
                                    arguments=arguments, capability=capability,
                                    budget=budget, native_call=snapshot_request["call"])
            if recorded is None:
                return self.allocate(capability, capability_version, arguments,
                                     authority=authority, request=snapshot_request,
                                     operation_id=operation_id,
                                     native_call=snapshot_request["call"],
                                     budget=budget)
            if operation_id in self._read_outcomes():
                raise AlreadyExecuted(
                    f"operation_id {operation_id} already has an outcome record")
            if recorded.reservation:
                expected = _reservation_digest(
                    arguments, authority=authority, capability=capability,
                    capability_version=capability_version,
                    native_call=None, budget=budget)
                if recorded.request_digest != expected:
                    raise ChangedContentReuse(
                        f"operation_id {operation_id} is bound to different content")
                upgraded = replace(recorded, request_digest=digest,
                                   native_call=snapshot_request["call"],
                                   reservation=False)
                self._replace_intent(upgraded)
                return upgraded
            if recorded.request_digest != digest:
                raise ChangedContentReuse(
                    f"operation_id {operation_id} is bound to different content")
            return recorded

    def dispatch(self, intent: EffectIntent,
                 executor: Callable[[dict[str, Any]], Any]) -> EffectOutcome:
        """Execute one bound intent through the caller's authority boundary.

        The intent record must already be durable. A completed operation is not
        re-executed and a started one without an outcome is refused: an effect
        that lost its receipt is UNKNOWN to a successor, not permission to try
        again.

        The executor closure checks current cancellation and authority
        immediately before actual capability execution, after this method has
        persisted the start record; persisted authority is a binding, not a
        fresh grant, and no model/tool-result text can enlarge it.
        """
        with self._locked():
            outcomes = self._read_outcomes()
            existing = outcomes.get(intent.operation_id)
            if existing is not None:
                return existing
            recorded = self._read_intents().get(intent.operation_id)
            if recorded is None:
                raise ValueError(
                    f"intent {intent.operation_id} is not recorded in this store")
            if recorded.writer_id != self.writer_id or not _same_binding(recorded, intent):
                return EffectOutcome(operation_id=intent.operation_id, status="unknown",
                                     failure="concurrent-claim",
                                     reconciliation_ref=self._intent_ref(intent.operation_id))
            if intent.operation_id in self._read_starts():
                # An execution began and no outcome survived. The effect is
                # UNKNOWN, never a reason to try again.
                return EffectOutcome(operation_id=intent.operation_id, status="unknown",
                                     failure="started-without-outcome",
                                     reconciliation_ref=self._intent_ref(intent.operation_id))
            self._append(self.starts_path, DispatchClaim(
                operation_id=intent.operation_id, writer_id=self.writer_id,
                started_at=time.time()).to_record())
            call = {"operation_id": intent.operation_id,
                    "capability": intent.capability,
                    "capability_version": intent.capability_version,
                    "arguments": dict(intent.arguments),
                    "authority": intent.authority,
                    "request_digest": intent.request_digest}
            if intent.native_call is not None:
                # The provider's call shape reaches the executor unchanged
                # alongside the durable identity, so a provider-issued call id
                # cannot be detached from its effect. The caller's bound
                # arguments and authority win over any same-named provider
                # field, because they are what the digest committed to.
                for field in ("type", "id", "name"):
                    if field in intent.native_call and field not in call:
                        call[field] = intent.native_call[field]
                call["native_call"] = dict(intent.native_call)
            try:
                result = executor(call)
            except (StoreUnavailable, JournalCorrupt, ChangedContentReuse):
                raise
            except ValueError as exc:
                # A caller serialization error is not a capability effect. It
                # leaves no outcome, but it must reach the caller rather than
                # be reported as though the capability had failed.
                raise StoreUnavailable(
                    f"the recorded result of {intent.operation_id} is not canonical: "
                    f"{exc}") from exc
            except Exception:
                # An executor exception does not establish that no effect
                # happened, so no outcome is recorded: the start record is the
                # durable witness and a successor reads started-without-outcome.
                return EffectOutcome(
                    operation_id=intent.operation_id, status="unknown",
                    failure="capability-failed",
                    reconciliation_ref=self._intent_ref(intent.operation_id))
            outcome = EffectOutcome(operation_id=intent.operation_id,
                                    status="completed", result=result,
                                    executions=1, native_call=intent.native_call)
            self._append(self.outcomes_path, outcome.to_record())
            return outcome

    def reconcile(self, operation_id: str) -> EffectOutcome:
        """Read the durable state of an operation, never repeat its dispatch.

        An existing outcome is returned as recorded. An intent with no outcome is
        `unknown` and names the exact intent record so the caller decides. This
        never executes; a plain record read is not evidence that an unknown
        effect did not occur.
        """
        with self._locked():
            return self._reconcile_locked(operation_id)

    def _reconcile_locked(self, operation_id: str) -> EffectOutcome:
        outcome = self._read_outcomes().get(operation_id)
        if outcome is not None:
            return outcome
        intent = self._read_intents().get(operation_id)
        if intent is None:
            return EffectOutcome(operation_id=operation_id, status="unknown",
                                 failure="no-intent")
        started = operation_id in self._read_starts()
        return replace(self._unknown_intent(intent),
                       failure="started-without-outcome" if started
                       else "unreconciled-intent")

    def status(self, operation_id: str) -> EffectOutcome | None:
        """The outcome a fresh process would read, or None if never allocated.

        `None` is the not-started state: only states that survived a process
        boundary are recorded. Missing, corrupt or replaced stores are not proof
        of no effect.
        """
        with self._locked():
            outcome = self._read_outcomes().get(operation_id)
            if outcome is not None:
                return outcome
            intent = self._read_intents().get(operation_id)
            if intent is None:
                return None
            started = operation_id in self._read_starts()
        return replace(self._unknown_intent(intent), failure=(
            "started-without-outcome" if started else "unreconciled-intent"))

    def _unknown_intent(self, intent: EffectIntent) -> EffectOutcome:
        return EffectOutcome(operation_id=intent.operation_id, status="unknown",
                             failure="unreconciled-intent",
                             reconciliation_ref=self._intent_ref(intent.operation_id),
                             native_call=intent.native_call)

    def open_intents(self) -> Sequence[EffectIntent]:
        """Every intent a fresh process still has to reconcile.

        A completed outcome closes the intent. A partial or unknown one does not:
        the effect may be durable but uncommitted, so the intent stays open for
        the caller to decide whether to reconcile or repair.
        """
        with self._locked():
            outcomes = self._read_outcomes()
            return [intent for operation_id, intent in sorted(self._read_intents().items())
                    if outcomes.get(operation_id,
                                    EffectOutcome(operation_id, "unknown")).status
                    != "completed"]

    def reserve(self, capability: str, capability_version: str,
                arguments: Mapping[str, Any], *, authority: str,
                operation_id: str, native_call: Mapping[str, Any] | None = None,
                budget: Mapping[str, Any] | None = None) -> EffectIntent:
        """Bind an operation identity to content and authority with no effect.

        `allocate` is the same write made by the dispatch path; this entry point
        only names the caller-visible intent. A caller that must fix an
        operation id before the provider has issued a call id (an upstream
        contract, a reserved report identity) can make that identity durable
        here, then either dispatch it through `dispatch` or retire it with
        `drop_reserved`.

        Same rules as `allocate`: a same-id request with a different binding
        raises `ChangedContentReuse` before any write, and an id that already has
        an outcome raises `AlreadyExecuted`. A reservation is an intent with no
        start record, so every reader still reads `unreconciled-intent`.
        """
        return self._reserve(capability, capability_version, arguments,
                             authority=authority, operation_id=operation_id,
                             native_call=native_call, budget=budget)

    def _reserve(self, capability: str, capability_version: str,
                 arguments: Mapping[str, Any], *, authority: str,
                 operation_id: str, native_call: Mapping[str, Any] | None,
                 budget: Mapping[str, Any] | None) -> EffectIntent:
        normalized = _snapshot(arguments)
        if not isinstance(normalized, Mapping):
            raise ValueError("arguments must be an object")
        call = _snapshot(native_call) if native_call is not None else None
        snapshot_budget = _snapshot(budget) if budget is not None else None
        digest = _reservation_digest(normalized, authority=authority,
                                     capability=capability,
                                     capability_version=capability_version,
                                     native_call=None, budget=snapshot_budget)
        intent = self.allocate(capability, capability_version, normalized,
                               authority=authority, request={"reservation":
                                                             _RESERVATION_MARKER},
                               operation_id=operation_id, native_call=call,
                               budget=snapshot_budget, digest=digest,
                               reservation=True)
        return intent

    def drop_reserved(self, operation_id: str) -> bool:
        """Retire a reservation that was never dispatched.

        Returns True when an intent was removed, False when the id had no
        unreconciled reservation (never allocated, already dropped) and raises
        `JournalCorrupt` when the store is unreadable. A started or completed
        operation is not a reservation: the start record and the outcome stay,
        because removing them would erase the durable witness of an effect that
        may have happened. Use this only for an identity that was provably never
        dispatched.
        """
        with self._locked():
            if operation_id in self._read_starts():
                return False
            intents = self._read_intents()
            intent = intents.get(operation_id)
            if intent is None or not intent.reservation:
                return False
            if operation_id in self._read_outcomes():
                return False
            self._rewrite_without(self.intents_path, operation_id)
            return True

    def _rewrite_without(self, path: Path, operation_id: str) -> None:
        """Rewrite one journal without a retired id, under the store lock.

        A retired reservation is the only record this store ever removes, and it
        is safe only because no start or outcome can reference it. The rewrite
        is written to a sibling file and `os.replace`d so a crash leaves either
        the old journal or the new one, never a partial mix.
        """
        rows = [row for row in self._read(path) if row.get("operation_id") != operation_id]
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False,
                                        separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def _replace_intent(self, intent: EffectIntent) -> None:
        """Rewrite one intent record under the store lock.

        The only caller is the reservation upgrade, which happens before any
        start record and before any effect, so the rewrite cannot orphan an
        outcome. A crash leaves the old intent or the new one, never a mix; the
        old binding still names the same operation, authority and arguments.
        """
        rows = [row if row.get("operation_id") != intent.operation_id
                else intent.to_record() for row in self._read(self.intents_path)]
        tmp = self.intents_path.with_name(self.intents_path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False,
                                        separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.intents_path)

    # -- helpers -----------------------------------------------------------

    def _intent_ref(self, operation_id: str) -> str:
        return f"{self.intents_path}::{operation_id}"


def _same_binding(recorded: EffectIntent, intent: EffectIntent) -> bool:
    """The durable record and the intent being dispatched must be one operation."""
    return (recorded.capability == intent.capability
            and recorded.capability_version == intent.capability_version
            and recorded.arguments == intent.arguments
            and recorded.authority == intent.authority
            and recorded.request_digest == intent.request_digest)

def effect_outcome_for(outcome: EffectOutcome) -> dict[str, Any]:
    """The loop's `dispatch` reply: an explicit status the loop never retries.

    The ledger records `completed` or `unknown` for a dispatch; `unknown` is the
    only answer this boundary can give for an ambiguous effect, so the loop
    stops rather than retrying.
    """
    return {"status": outcome.status, "result": snapshot_json(outcome.result),
            "operation_id": outcome.operation_id, "failure": outcome.failure,
            "reconciliation_ref": outcome.reconciliation_ref,
            "executions": outcome.executions}


def snapshot_json(value: Any) -> Any:
    """Canonical, crash-safe copy of a capability result for journal and reply.

    Non-finite floats and noncanonical objects have no stable serialization, so
    they are refused rather than lossily normalized into a durable record.
    """
    return json.loads(json.dumps(_canonicalizable(value), ensure_ascii=False,
                                 allow_nan=False))


class EffectBoundary:
    """`drive_native`'s `dispatch` callback bound to one original store.

    `drive_native` calls `dispatch(call)` with the provider's call shape; this
    class is the deterministic mapping from that shape to the durable operation
    a fresh process can reconcile. It holds the caller's obligation identity so
    the digest binds the exact source event, and snapshots the request so a
    mutated caller object cannot change what was committed.

    The caller supplies the capability executor at construction. It receives the
    ledger's call — durable identity plus the provider's type/id/name and the
    full native call — and returns the capability's observed result. This class
    never calls a provider or a capability itself. A noncanonical or
    unserializable result raises before any outcome is written, so a caller
    error cannot leave a durable record of content the store cannot re-read.

    `operation_id` is generated from the provider's call id when the caller does
    not supply one, so a provider-issued id names its own effect. Reuse of that
    id with changed content, arguments or authority raises `ChangedContentReuse`
    before any effect. A same-id second dispatch returns the recorded outcome or
    an explicit `unknown`, never a second execution.

    A caller who must name an operation before the provider has issued a call id
    (a reserved report identity, an id fixed by an upstream contract) uses
    `reserve` instead: it binds the same durable identity to exact capability,
    arguments and authority with no start record and no execution. A reserved
    intent is `unreconciled-intent` to every reader, including the writer that
    reserved it, until `__call__` dispatches it or the caller explicitly drops
    it. `drop_reserved` retires a reservation that was never dispatched; it
    never clears a start or an outcome, and it is the only way a reserved id
    becomes reusable without `ChangedContentReuse`.
    """

    def __init__(self, store_dir: Path, *, writer_id: str, obligation: Mapping[str, Any],
                 execute: Callable[[dict[str, Any]], Any], authority: str,
                 capability_version: str = "v1",
                 budget: Mapping[str, Any] | None = None,
                 operation_prefix: str = "effect") -> None:
        if not authority:
            raise ValueError("authority is required")
        self.ledger = EffectLedger(store_dir, writer_id=writer_id)
        self.execute = execute
        self.obligation = _snapshot(obligation)
        self.authority = authority
        self.capability_version = capability_version
        self.budget = _snapshot(budget) if budget is not None else None
        self.operation_prefix = operation_prefix

    def _operation_id(self, call: Mapping[str, Any]) -> str:
        caller_id = call.get("operation_id")
        if isinstance(caller_id, str) and caller_id:
            if not _OPERATION_ID_RE.match(caller_id):
                raise ValueError(
                    f"operation_id {caller_id!r} must match [A-Za-z0-9_.:-]+")
            return caller_id
        native_id = call.get("id")
        if not isinstance(native_id, str) or not native_id:
            raise ValueError("a native call id is required to name the operation")
        # A provider id may carry characters the journal cannot address. Plain
        # substitution would collapse distinct ids into one name, so a short
        # digest of the original makes the derived id collision-free while the
        # readable prefix keeps it greppable in the store. The intent's
        # native_call record still carries the provider id verbatim.
        safe = re.sub(r"[^A-Za-z0-9_.:-]", "-", native_id[:64]).strip("-") or "call"
        digest = hashlib.sha256(native_id.encode("utf-8")).hexdigest()[:12]
        return f"{self.operation_prefix}-{safe}-{digest}"

    def __call__(self, call: Mapping[str, Any]) -> dict[str, Any]:
        """Bind, start and record one capability call, then reply for the loop.

        Never retries: a second call with the same id returns the durable state
        or an explicit `unknown`, and the capability runs at most once per id.
        """
        native_call = _snapshot(call)
        if not isinstance(native_call, Mapping):
            raise ValueError("native call must be an object")
        operation_id = self._operation_id(native_call)
        capability = native_call.get("name")
        if not isinstance(capability, str) or not capability:
            raise ValueError("a native call name is required")
        arguments = native_call.get("arguments")
        if not isinstance(arguments, Mapping):
            raise ValueError("native call arguments must be an object")
        bound = self.ledger.bind_dispatch(capability, self.capability_version,
                                          _snapshot(arguments),
                                          authority=self.authority,
                                          obligation=self.obligation,
                                          call=native_call,
                                          operation_id=operation_id,
                                          budget=self.budget)
        return effect_outcome_for(self.ledger.dispatch(bound, self._runner))

    def _runner(self, ledger_call: dict[str, Any]) -> Any:
        # The caller rechecks current authority against its own state; the
        # persisted authority is a binding, not a fresh grant.
        return snapshot_json(self.execute(ledger_call))

    def reconcile(self, operation_id: str) -> dict[str, Any]:
        """Read durable state for one operation without executing anything."""
        return effect_outcome_for(self.ledger.reconcile(operation_id))

    def status(self, operation_id: str) -> dict[str, Any] | None:
        """The durable state a fresh process reads, or None if never allocated."""
        outcome = self.ledger.status(operation_id)
        return None if outcome is None else effect_outcome_for(outcome)

    def open_intents(self) -> Sequence[EffectIntent]:
        return self.ledger.open_intents()

    def reserve(self, operation_id: str, *, capability: str,
                arguments: Mapping[str, Any],
                native_call: Mapping[str, Any] | None = None) -> EffectIntent:
        """Bind an operation id before any effect, with no start record.

        The capability is not executed and no start record is written: the id is
        durable as an `unreconciled-intent` bound by this boundary authority
        and capability version. Use this when the identity must exist before a
        provider call id does, then either dispatch the same id through
        `__call__` or retire it with `drop_reserved`.
        """
        return self.ledger.reserve(capability, self.capability_version,
                                   _snapshot(arguments), authority=self.authority,
                                   operation_id=operation_id,
                                   native_call=_snapshot(native_call)
                                   if native_call is not None else None,
                                   budget=self.budget)

    def drop_reserved(self, operation_id: str) -> bool:
        """Retire a reservation this boundary made and never dispatched."""
        return self.ledger.drop_reserved(operation_id)

    def recover(self, call: Mapping[str, Any]) -> dict[str, Any] | None:
        """Reply for a call whose receipt may have been lost, without executing.

        `None` means the operation was never allocated, so the caller has no
        durable state to reconcile and may allocate it. Any other reply is the
        durable state and must not be retried: a `started-without-outcome`
        failure is an unknown effect, not permission to run the capability again.
        """
        return self.status(self._operation_id(call))

