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
_NOT_PRODUCED_STATUSES = ("partial",)
# The boundary's only durable writers are `dispatch` and `reserve`, and neither
# can emit `partial`: an effect is recorded once, as `completed`, or left with no
# outcome at all, which reads back as `unknown`. The status stays in the alphabet
# because `read_native_journal` replays outcomes a caller wrote, and a caller that
# reports a partial effect must still parse rather than corrupt the journal. It is
# reserved for the caller, not an unimplemented branch of this boundary.
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
                          capability_version=capability_version,
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


class CallIdConflict(JournalCorrupt):
    """Two intents hold one provider call id, so no record names the operation.

    The recovery and unclaimed-dispatch routes resolve an operation id by the
    provider call id alone, because `drive_native` forwards the provider's call
    untouched and a provider does not echo the caller's id. That lookup needs
    exactly one record per call id: `allocate` keeps the store to one by
    rejecting a second intent under a call id another intent already holds, and
    the resolve path itself never allocates. If a store still holds two, neither
    is picked and the lookup fails closed instead.
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
    status: Literal["completed", "partial", "unknown"]  # see _NOT_PRODUCED_STATUSES
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


_RECORD_TYPES = {"intent": EffectIntent, "start": DispatchClaim,
                 "outcome": EffectOutcome}
_JOURNAL_TYPES = {"intents.jsonl": "intent", "starts.jsonl": "start",
                  "outcomes.jsonl": "outcome"}


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


def _call_id(native_call: Mapping[str, Any] | None) -> str | None:
    """The provider's call id, the key that joins a retry to its bound operation."""
    if not isinstance(native_call, Mapping):
        return None
    call_id = native_call.get("id")
    return call_id if isinstance(call_id, str) and call_id else None


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
                   capability: str = "", capability_version: str = "",
                   budget: Mapping[str, Any] | None = None,
                   native_call: Mapping[str, Any] | None = None) -> str:
    """Bind an operation to the exact content, arguments, authority and call identity.

    Authority is part of the digest so the same arguments under a different
    authority are a different effect, not a reuse. The original call identity is
    part of it so a provider-issued call id cannot be detached from its effect.
    `capability_version` is part of it so an id reserved or dispatched against
    one version of a capability cannot be silently replayed against another:
    the reservation's version is bound, not merely recorded.
    """
    digest = hashlib.sha256()
    digest.update(b"mishe-effect-request-v1\n")
    digest.update(canonical_request(request).encode("utf-8"))
    digest.update(b"\n")
    digest.update(capability.encode("utf-8"))
    digest.update(b"\n")
    digest.update(capability_version.encode("utf-8"))
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

    Journals are parsed incrementally. Every read is validated against the
    file's current inode, size and mtime: an append reuses the already-parsed
    prefix and parses only the new lines, and an inode change (a rewrite by
    `drop_reserved` or the reservation upgrade, which replace the file) or any
    mtime change drops the cache, so a read after a rewrite re-parses the whole
    file and cannot observe a stale prefix.
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
        self._parsed: dict[str, tuple[Any, ...]] = {}
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
        encoded = json.dumps(record, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8") + b"\n"
        with path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # An append only writes after the last parsed byte offset, so the parsed
        # prefix stays valid and the cache can grow by this one record. The
        # stored inode, size and mtime are refreshed from the closed file, so a
        # later read sees the append instead of treating the file as changed and
        # reparsing it. An inode change (a rewrite via os.replace) is not
        # possible through this method; `_journal` detects one from elsewhere.
        entry = self._parsed.get(str(path))
        if entry is None or entry[1] != os.stat(path).st_size - len(encoded):
            return
        record_type = _JOURNAL_TYPES[path.name]
        parsed = dict(entry[3])
        typed = _RECORD_TYPES[record_type].from_record(record)
        if typed.operation_id in parsed:
            raise JournalCorrupt(
                f"duplicate {record_type} for {typed.operation_id}")
        parsed[typed.operation_id] = typed
        stat = path.stat()
        self._parsed[str(path)] = (stat.st_ino, stat.st_size, stat.st_mtime_ns,
                                   parsed, stat.st_mtime_ns, 1)

    def _read(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        data = path.read_bytes()
        if not data:
            return rows
        if not data.endswith(b"\n"):
            # A record that lost its terminator means the store was truncated
            # mid-write. No claim may be read from a partial line. The split is
            # on the real newline byte, not the JSON escape that a
            # canonical record may legitimately contain inside a value.
            fragment = data[data.rfind(b"\n") + 1:]
            raise JournalCorrupt(
                f"truncated line in {path}: "
                f"{fragment!r}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JournalCorrupt(f"invalid UTF-8 in {path}: {exc}") from exc
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

    def _parse_lines(self, text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            line_text = line.strip()
            if not line_text:
                continue
            try:
                record = json.loads(line_text)
            except ValueError as exc:
                raise JournalCorrupt(f"unreadable line: {exc}") from exc
            if not isinstance(record, dict):
                raise JournalCorrupt("non-object line")
            rows.append(record)
        return rows

    def _journal(self, path: Path, record_type: str) -> dict[str, Any]:
        """One parsed journal, appending only the lines added since the last read.

        Journals grow by append, so a read reuses the parsed prefix and parses
        only the tail. Reuse is gated on the file's inode, size and mtime: a
        rewrite via `os.replace` (retiring a reservation or upgrading a
        reservation into a dispatch) changes the inode, and any other writer
        changes the mtime; either drops the cache to a full reparse, so a read
        can never observe a stale prefix. A line missing its final newline is a
        truncated write and fails closed as before.
        """
        key = str(path)
        stat = path.stat()
        cached = self._parsed.get(key)
        data = path.read_bytes()
        if not data:
            self._parsed[key] = (stat.st_ino, 0, stat.st_mtime_ns, {}, stat.st_mtime_ns, 0)
            return {}
        records: dict[str, Any] = {}
        offset = 0
        if cached is not None and cached[0] == stat.st_ino and cached[2] == stat.st_mtime_ns \
                and cached[1] <= len(data) and cached[4] == stat.st_mtime_ns:
            records = dict(cached[3])
            offset = cached[1]
        tail = data[offset:]
        if tail and not tail.endswith(b"\n"):
            fragment = tail[tail.rfind(b"\n") + 1:]
            raise JournalCorrupt(f"truncated line in {path}: {fragment!r}")
        if tail:
            try:
                text = tail.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise JournalCorrupt(f"invalid UTF-8 in {path}: {exc}") from exc
            for row in self._parse_lines(text):
                typed = _RECORD_TYPES[record_type].from_record(row)
                if typed.operation_id in records:
                    raise JournalCorrupt(
                        f"duplicate {record_type} for {typed.operation_id}")
                records[typed.operation_id] = typed
        self._parsed[key] = (stat.st_ino, len(data), stat.st_mtime_ns, records,
                             stat.st_mtime_ns, 0)
        return records

    def _read_intents(self) -> dict[str, EffectIntent]:
        return self._journal(self.intents_path, "intent")

    def _read_starts(self) -> dict[str, DispatchClaim]:
        return self._journal(self.starts_path, "start")

    def _read_outcomes(self) -> dict[str, EffectOutcome]:
        # Each stored outcome already carries the executions count it was
        # written with, so the journal value is authoritative.
        return self._journal(self.outcomes_path, "outcome")

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
        allocated by the loop before dispatch and reused by recovery. For an ID
        with no recorded outcome, different content, authority, call identity or
        capability version raises `ChangedContentReuse` before any write; the
        same binding returns the existing intent, never permission to dispatch
        twice. An ID with a recorded outcome raises `AlreadyExecuted` before
        comparing pending content; use `reconcile` to read the retained outcome.

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
                                    capability=capability,
                                    capability_version=capability_version,
                                    budget=budget, native_call=call)
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
            conflict = self._conflicts_with(call, exclude=operation_id)
            if conflict is not None:
                raise CallIdConflict(
                    f"provider call id {_call_id(call)!r} is already bound to "
                    f"operation {conflict}; one provider call names one operation")
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
        pending dispatch that binds different values — including a different
        `capability_version` — is `ChangedContentReuse` before any effect.

        The caller's request snapshot is taken here so a mutated caller object
        cannot change what the digest commits to.
        """
        snapshot_request = {"obligation": _snapshot(obligation), "call": _snapshot(call)}
        with self._locked():
            recorded = self._read_intents().get(operation_id)
            digest = request_digest(snapshot_request, authority,
                                    arguments=arguments, capability=capability,
                                    capability_version=capability_version,
                                    budget=budget,
                                    native_call=snapshot_request["call"])
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
                conflict = self._conflicts_with(snapshot_request["call"],
                                                exclude=operation_id)
                if conflict is not None:
                    raise CallIdConflict(
                        f"provider call id "
                        f"{_call_id(snapshot_request['call'])!r} is already "
                        f"bound to operation {conflict}; one provider call "
                        f"names one operation")
                upgraded = replace(recorded, request_digest=digest,
                                   native_call=snapshot_request["call"],
                                   writer_id=self.writer_id,
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
            except (AlreadyExecuted, ConcurrentClaim):
                # These name durable store state, not a capability outcome: an
                # operation that already has an outcome and an id another writer
                # holds are decisions this store made before any effect. The
                # caller needs the store's verdict rather than the reply the
                # generic branch below gives for an effect that may have
                # happened but lost its receipt.
                raise
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

        `None` is the not-started state, as a value rather than a status string:
        only states that survived a process boundary are recorded, so absence is
        the only way this boundary can express it. `read_native_journal` reports
        a not-started call as a status string instead, from the caller's own
        accounting. Missing, corrupt or replaced stores are not proof of no
        effect.
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

        A completed outcome closes the intent. An unknown one does not: the
        effect may have happened while no outcome survived, so the intent stays
        open for the caller to decide whether to reconcile or repair. (`partial`
        parses, but this boundary never writes it — see
        `_NOT_PRODUCED_STATUSES`.)
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
        # The rewrite swapped the inode, so the parsed prefix cannot be reused:
        # the next read reparses the whole file.
        self._parsed.pop(str(path), None)

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
        # The rewrite swapped the inode, so the parsed prefix cannot be reused:
        # the next read reparses the whole file.
        self._parsed.pop(str(self.intents_path), None)

    # -- helpers -----------------------------------------------------------

    def _intent_ref(self, operation_id: str) -> str:
        return f"{self.intents_path}::{operation_id}"

    def _conflicts_with(self, native_call: Mapping[str, Any] | None, *,
                        exclude: str) -> str | None:
        """The operation id already bound to this provider call, if any.

        Called under the store lock by `allocate` only, so the answer is still
        current when the append runs. `exclude` skips the operation being
        allocated, which matters for a `bind_dispatch` rebind of a reservation
        whose own intent already carries this call id.

        Both journals are scanned. `dispatch` writes the provider call id onto
        the outcome as well as the intent, so an intent alone is not the whole
        binding: a store this boundary did not write can hold an outcome that
        names a provider call whose intent record is gone. Scanning the intents
        only would find nothing and let a second operation execute for a call
        that already produced an effect. An intent that has an outcome is not a
        double answer — the two journals agree on the same id — but two hits
        that disagree are a broken store, so they are reported as a conflict
        rather than chosen between.
        """
        call_id = _call_id(native_call)
        if call_id is None:
            return None
        hits = {operation_id for operation_id, intent in self._read_intents().items()
                if operation_id != exclude and _call_id(intent.native_call) == call_id}
        hits.update(operation_id for operation_id, outcome
                    in self._read_outcomes().items()
                    if operation_id != exclude and _call_id(outcome.native_call) == call_id)
        if len(hits) > 1:
            raise CallIdConflict(
                f"provider call id {call_id!r} is bound to operations "
                f"{sorted(hits)}; one provider call names one operation")
        return next(iter(hits), None)


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
    not supply one, so a provider-issued id names its own effect. Changed content,
    arguments or authority for a pending id raises `ChangedContentReuse` before
    any effect. An id with a recorded outcome raises `AlreadyExecuted` on
    dispatch. Read the retained outcome through `reconcile`; missing or
    incomplete history is `unknown`, never permission for a second execution.

    A caller who must name an operation before the provider has issued a call id
    (a reserved report identity, an id fixed by an upstream contract) uses
    `reserve` instead: it binds the same durable identity to exact capability,
    arguments and authority with no start record and no execution. A reserved
    intent is `unreconciled-intent` to every reader, including the writer that
    reserved it, until `__call__` dispatches it or the caller explicitly drops
    it. `drop_reserved` retires a reservation that was never dispatched; it
    never clears a start or an outcome, and it is the only way a reserved id
    becomes reusable without `ChangedContentReuse`. A caller reserving under
    one writer may dispatch the reservation under another: the upgrade rebinds
    the intent to the dispatching writer, so `dispatch`'s one-writer check sees
    the same writer that is about to hold the lock.
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

    def _resolve_operation_id(self, call: Mapping[str, Any]) -> str:
        """The id this provider call is bound to, or the id a dispatch would take.

        A caller-named operation reaches the loop only through `claim_for`, so a
        call can be bound under an id that `call` itself does not carry: the
        provider call id is in `native_call`, the caller's id is in the record.
        Recovery by the provider call alone has to find that record, or it reads
        a derived id that was never allocated and reports 'no durable state',
        which the caller reads as permission to dispatch — and the capability
        runs a second time while the first start record still names another id.

        The caller's explicit `operation_id` always wins, as in `_operation_id`.
        One provider call id names at most one operation: `allocate` refuses a
        second intent under a call id another intent already holds, so this
        lookup has one answer or none. A store that holds two has a broken
        invariant and neither record is named — `CallIdConflict`, not a guess.
        """
        caller_id = call.get("operation_id")
        if isinstance(caller_id, str) and caller_id:
            if not _OPERATION_ID_RE.match(caller_id):
                raise ValueError(
                    f"operation_id {caller_id!r} must match [A-Za-z0-9_.:-]+")
            return caller_id
        with self.ledger._locked():
            call_id = _call_id(call)
            if call_id is not None:
                # `allocate` keeps one intent per provider call id, so this
                # lookup names the one record that holds it. Two intents under
                # one id means the invariant broke, and choosing either would
                # name an operation for a provider call that binds both: fail
                # closed and let the caller see the store it has to repair.
                intents = [operation_id for operation_id, intent
                           in self.ledger._read_intents().items()
                           if _call_id(intent.native_call) == call_id]
                if len(intents) > 1:
                    raise CallIdConflict(
                        f"provider call id {call_id!r} is bound to operations "
                        f"{sorted(intents)}; resolve cannot name one of them")
                outcomes = [operation_id for operation_id, outcome
                            in self.ledger._read_outcomes().items()
                            if _call_id(outcome.native_call) == call_id]
                if len(outcomes) > 1:
                    raise CallIdConflict(
                        f"provider call id {call_id!r} has outcomes "
                        f"{sorted(outcomes)}; resolve cannot name one of them")
                if intents:
                    return intents[0]
                if outcomes:
                    return outcomes[0]
        return self._operation_id(call)

    def __call__(self, call: Mapping[str, Any], *,
                 claim_for: str | None = None) -> dict[str, Any]:
        """Bind, start and record one capability call, then reply for the loop.

        Never retries: a completed id raises `AlreadyExecuted` during binding.
        A matching pending binding cannot repeat a started effect; use
        `reconcile` for the retained outcome or explicit `unknown`.

        `claim_for` names an existing reservation this call fulfils: the
        operation runs under the caller's id rather than one derived from the
        provider's call. The reservation's bound capability, arguments,
        authority and capability version must all match, or the call is
        `ChangedContentReuse` before any effect — exactly as a mismatched
        dispatch of a reserved id is. `drive_native` passes the provider's call
        untouched, so this is the only route by which a caller-named id reaches
        the loop.
        """
        native_call = _snapshot(call)
        if not isinstance(native_call, Mapping):
            raise ValueError("native call must be an object")
        if claim_for is not None:
            if not _OPERATION_ID_RE.match(claim_for):
                raise ValueError(
                    f"claim_for {claim_for!r} must match [A-Za-z0-9_.:-]+")
            if not self._has_intent(claim_for):
                raise ValueError(
                    f"claim_for {claim_for!r} names no operation in this store")
            operation_id = claim_for
        else:
            # A caller-named operation that died after its start record is bound
            # to the provider call under the caller's id, so the dispatch route
            # must resolve the same id the recovery route does. Deriving from
            # the provider call alone would allocate a second, derived operation
            # and execute the capability again under it.
            operation_id = self._resolve_operation_id(native_call)
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

    def _has_intent(self, operation_id: str) -> bool:
        with self.ledger._locked():
            return operation_id in self.ledger._read_intents()

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

        The id is resolved rather than derived: a caller-named operation reaches
        the loop only through `claim_for`, so the durable record that belongs to
        this provider call can carry an id the call itself does not. Deriving
        from the provider call alone would read an id that was never allocated,
        answer `None`, and let a successor execute the capability a second time
        under a derived id while the first start record still names another.
        """
        return self.status(self._resolve_operation_id(call))

