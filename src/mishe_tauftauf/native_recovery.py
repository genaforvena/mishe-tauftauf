"""Wake-level failure classification and bounded fresh-wake recovery.

A failed native wake leaves an attempt directory holding its own recorded
bytes: the durable effect ledger, the loop journal, the result summary and the
failure traceback. This module reads only those bytes and classifies the
attempt, so a caller can decide whether the busy wedge may be released for a
fresh attempt or whether the attempt must be reconciled before anything else
runs.

Verdicts:

  settled                the canonical settlement is confirmed; nothing is left
                         to recover.
  retryable-no-effect    no effect was admitted and the terminal upstream
                         failure was transient, so a fresh attempt cannot
                         duplicate an effect.
  requires-reconciliation at least one effect was dispatched or observed, a
                         capability call was proposed, or the loop completed
                         without a confirmed settlement; the attempt must be
                         reconciled before any retry.
  unknown                the evidence is missing, unreadable, malformed, still
                         being written, or names no transient failure. Absence
                         of a record is never read as absence of an effect.

Only a `retryable-no-effect` attempt may be recovered. A fresh attempt is a
new directory with a new journal: no effect is replayed and no earlier
outcome is reused. The number of fresh attempts per wake is bounded; the bound
limits attempts, it does not promise that the next attempt succeeds. The
fresh attempt's directory is chosen here and created by the resident that runs
the next wake, so this module never writes an attempt directory.

Evidence rules:

  - Effect-ledger records are decoded by the ledger's own record classes, so a
    misfiled, malformed or truncated record is a corrupt store and never a
    harmless row.
  - The ledger's own `.ledger.lock` is held while the attempt is classified,
    so a writer cannot admit an effect inside the decision. A lock that cannot
    be taken, or one that is busy again once the attempt has been read, means
    the evidence is not quiescent and cannot authorize recovery.
  - The whole evidence set is read before and after the decision and compared,
    so bytes that move while the attempt is classified leave the verdict
    UNKNOWN.
  - A recoverable attempt must carry the writer's own failure record, the
    traceback the failed wake wrote as it died. That record is the evidence
    that the attempt's writer ran to its end. It is read as part of the
    compared evidence and must be a complete traceback; a missing, empty,
    unreadable or incomplete record leaves the attempt UNKNOWN, because
    nothing in its bytes says the writer stopped.
  - Only a `retryable-no-effect` attempt authorizes a wake's recovery. An
    earlier attempt that settled, reconciled or stayed unknown blocks it.
  - Precondition: the caller classifies an attempt whose writer has exited.
    No snapshot can exclude a writer that appears after the last read, which
    is the same limit the durable effect ledger states for an absent record.
  - Transience is decided by the terminal failure, not by any earlier one: an
    attempt that ends in a deterministic rejection is not retryable.
  - A wake is recovered only when every one of its attempts authorizes it. An
    earlier attempt whose own evidence is unknown blocks the recovery.

This module reads and classifies. It never dispatches a provider call, never
edits an earlier attempt, and never settles a wake. Its writes are the removal
of a busy marker that names exactly the wake being released, and the
blocked-state record that names a wake which cannot proceed. The blocked state
is the plant's view of a wedge: the resident writes it when it holds a wake for
reconciliation, and the readiness gate reads it, so a native resident that
cannot start is observable without reading an attempt directory.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .inference_effects import DispatchClaim, EffectIntent, EffectOutcome

SETTLED = 'settled'
RETRYABLE = 'retryable-no-effect'
RECONCILE = 'requires-reconciliation'
UNKNOWN = 'unknown'

# Upstream statuses that name a transient condition rather than a request the
# plant got wrong. A deterministic rejection is not a retry candidate.
TRANSIENT_STATUS = frozenset({408, 429})
DEFAULT_RETRY_LIMIT = 3

# The canonical durable effect ledger: one journal per record type, decoded by
# the ledger's own record classes.
LEDGER_JOURNALS = (
    ('intents.jsonl', EffectIntent),
    ('starts.jsonl', DispatchClaim),
    ('outcomes.jsonl', EffectOutcome),
)
STORE_LOCK = '.ledger.lock'
# The failed wake's own traceback, written by the writer as it died.
FAILURE_RECORD = 'failure.txt'
TRACEBACK_HEADER = 'Traceback (most recent call last):'


class LiveWriter(Exception):
    """The attempt's store still has a writer inside it."""


@dataclass(frozen=True)
class Attempt:
    """What an attempt directory's own bytes say about the failed wake."""

    verdict: str
    reason: str
    evidence: tuple[str, ...] = ()
    settled: bool = False
    transient: bool = False
    dispatched: int = 0
    reserved: int = 0

    @property
    def recoverable(self) -> bool:
        """A fresh attempt is safe only for a provably effect-free transient failure."""
        return self.verdict == RETRYABLE and self.transient

    def to_record(self) -> dict:
        return {'verdict': self.verdict, 'reason': self.reason, 'evidence': list(self.evidence),
                'settled': self.settled, 'transient': self.transient,
                'dispatched': self.dispatched, 'reserved': self.reserved,
                'recoverable': self.recoverable}


def transient_status(status: object) -> bool:
    if not isinstance(status, int) or isinstance(status, bool):
        return False
    return status in TRANSIENT_STATUS or 500 <= status <= 599


def _decode_rows(data: bytes | None, name: str, decoder=None) -> tuple[list[dict], str | None]:
    """Decoded rows from a JSONL journal, or the reason it cannot be evidence.

    A journal that does not end with a newline holds a torn record; a row that
    is not an object, or that the canonical decoder rejects, is a malformed
    record. Both are corrupt evidence, never an empty one.
    """
    if not data:
        return [], None
    if not data.endswith(b'\n'):
        return [], f'truncated record in {name}'
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        return [], f'invalid UTF-8 in {name}: {exc}'
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            return [], f'malformed {name} line {number}: {exc}'
        if not isinstance(row, dict):
            return [], f'malformed {name} line {number}: not an object'
        if decoder is not None:
            try:
                decoder.from_record(row)
            except Exception as exc:
                return [], f'invalid {name} record line {number}: {exc}'
        rows.append(row)
    return rows, None


def _summary(data: bytes | None) -> tuple[dict, str | None]:
    """The wake's result summary, or the reason it cannot be used as evidence.

    An absent summary is not an error: a wake that died before writing it
    still has its own journal, and the journal is read next.
    """
    if data is None:
        return {}, None
    try:
        value = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        return {}, f'unreadable result summary: {exc}'
    if not isinstance(value, dict):
        return {}, 'result summary is not an object'
    return value, None


def _failure_record(data: bytes | None) -> tuple[bool, str | None]:
    """Whether the writer's own failure record is a complete traceback.

    The record is the evidence that the attempt's writer ran to its end, so a
    missing, empty, unreadable, incomplete or changing record leaves the
    attempt UNKNOWN rather than authorizing a retry.
    """
    if data is None:
        return False, f'the attempt carries no {FAILURE_RECORD} record of its writer ending'
    if not data.strip():
        return False, f'the {FAILURE_RECORD} record is empty'
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        return False, f'the {FAILURE_RECORD} record is unreadable: {exc}'
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or lines[0].strip() != TRACEBACK_HEADER:
        return False, f'the {FAILURE_RECORD} record is not a traceback'
    if len(lines) < 2:
        return False, f'the {FAILURE_RECORD} record names no exception'
    return True, None


def _upstream_failures(payload) -> list[dict]:
    """Upstream failure entries in a fetch ledger, given as a row or a list of rows."""
    entries = payload if isinstance(payload, list) else [payload]
    return [entry['upstream_failure'] for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get('upstream_failure'), dict)]


def _journal_ledgers(rows: list[dict]) -> list:
    """Fetch ledgers recorded in the loop journal itself.

    A failed turn is recorded as a `model_failure` row carrying the worker's
    budget row; a completed turn carries the same row in its response. The
    journal therefore holds the upstream failure even when the wake died before
    a result summary was written.
    """
    payloads = []
    for row in rows:
        kind = row.get('kind')
        if kind == 'model_failure' and row.get('fetch_ledger') is not None:
            payloads.append(row['fetch_ledger'])
        elif kind == 'model_output':
            response = row.get('response')
            if isinstance(response, dict) and response.get('fetch_ledger') is not None:
                payloads.append(response['fetch_ledger'])
    return payloads


def _terminal_failure(summary_ledger, rows: list[dict]) -> dict | None:
    """The failure that ended the wake.

    The result summary holds the wake's terminal failure. When the wake died
    before writing it, the last failure recorded in the journal is the
    terminal one. An earlier transient failure does not decide the verdict.
    """
    failures = _upstream_failures(summary_ledger)
    if failures:
        return failures[-1]
    for payload in reversed(_journal_ledgers(rows)):
        failures = _upstream_failures(payload)
        if failures:
            return failures[-1]
    return None


def _transient(failure: dict | None) -> tuple[bool, str | None]:
    if not isinstance(failure, dict):
        return False, None
    kind = failure.get('kind')
    if kind == 'network':
        return True, f'network: {failure.get("message")}'
    if kind == 'status' and transient_status(failure.get('status')):
        return True, f'status {failure.get("status")}'
    return False, None


def _read_evidence(attempt: Path) -> tuple[dict, str | None]:
    """The attempt's own bytes: summary, journal and the three ledger journals."""
    files: dict[str, bytes | None] = {}
    try:
        summary = attempt / 'result.json'
        files['result.json'] = summary.read_bytes() if summary.is_file() else None
        failure = attempt / FAILURE_RECORD
        files[FAILURE_RECORD] = failure.read_bytes() if failure.is_file() else None
        journal = attempt / 'journal.jsonl'
        if not journal.is_file():
            return files, f'missing loop journal: {journal.name}'
        files['journal.jsonl'] = journal.read_bytes()
        directory = attempt / 'effects'
        if not directory.is_dir():
            return files, f'missing effect ledger: {directory}'
        for name, _ in LEDGER_JOURNALS:
            path = directory / name
            if not path.exists():
                return files, f'missing effect ledger file: {name}'
            files[f'effects/{name}'] = path.read_bytes()
    except OSError as exc:
        return files, f'unreadable attempt evidence: {exc}'
    return files, None


@contextmanager
def _store_guard(directory: Path):
    """Hold the canonical store lock while the attempt is classified.

    The lock file is the ledger's own `.ledger.lock`; a writer holds it while
    it appends a record. Holding it here means a writer cannot admit an effect
    inside the decision, and a lock that cannot be taken means a writer is
    still inside the attempt, so the evidence is not quiescent. The lock file
    is never created here: an absent lock file means no writer ever used this
    store, so there is nothing to hold.
    """
    path = directory / STORE_LOCK
    if not path.exists():
        yield None
        return
    try:
        handle = path.open('r+', encoding='utf-8')
    except OSError as exc:
        raise LiveWriter(f'cannot open the store lock {path.name}: {exc}') from exc
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LiveWriter(f'another writer holds {path.name}') from exc
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _writer_active(directory: Path) -> bool:
    """Whether a writer holds the store lock right now."""
    path = directory / STORE_LOCK
    if not path.exists():
        return False
    try:
        handle = path.open('r+', encoding='utf-8')
    except OSError:
        return True
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def _journal_effect_rows(rows: list[dict]) -> int:
    """Rows that name a proposed or executed capability call."""
    kinds = ('proposal', 'tool_result')
    return sum(1 for row in rows if row.get('kind') in kinds)


def _evidence(attempt: Path) -> tuple[str, ...]:
    names = []
    for name in ('result.json', 'journal.jsonl', 'failure.txt', 'effects'):
        if (attempt / name).exists():
            names.append(str(attempt / name))
    return tuple(names)


def writer_active(attempt) -> bool:
    """Whether a writer holds the attempt's store lock right now."""
    return _writer_active(Path(attempt) / 'effects')


def classify_attempt(attempt) -> Attempt:
    """Classify one failed attempt from its own recorded bytes."""
    attempt = Path(attempt)
    if not attempt.is_dir():
        return Attempt(UNKNOWN, f'attempt directory missing: {attempt}')
    try:
        with _store_guard(attempt / 'effects'):
            files, error = _read_evidence(attempt)
            if error:
                return Attempt(UNKNOWN, error, evidence=_evidence(attempt))
            summary, error = _summary(files['result.json'])
            if error:
                return Attempt(UNKNOWN, error, evidence=_evidence(attempt))
            rows, error = _decode_rows(files['journal.jsonl'], 'journal.jsonl')
            if error:
                return Attempt(UNKNOWN, error, evidence=_evidence(attempt))
            counts = {}
            for name, decoder in LEDGER_JOURNALS:
                ledger_rows, error = _decode_rows(files[f'effects/{name}'], name, decoder)
                if error:
                    return Attempt(UNKNOWN, error, evidence=_evidence(attempt))
                counts[name] = len(ledger_rows)
            later, error = _read_evidence(attempt)
            if error:
                return Attempt(UNKNOWN, error, evidence=_evidence(attempt))
            if later != files:
                return Attempt(UNKNOWN,
                               'the attempt evidence changed while it was classified',
                               evidence=_evidence(attempt))
    except LiveWriter as exc:
        return Attempt(UNKNOWN, str(exc), evidence=_evidence(attempt))
    if _writer_active(attempt / 'effects'):
        return Attempt(UNKNOWN, 'a writer entered the store while the attempt was classified',
                       evidence=_evidence(attempt))
    final, error = _read_evidence(attempt)
    if error:
        return Attempt(UNKNOWN, error, evidence=_evidence(attempt))
    if final != files:
        return Attempt(UNKNOWN, 'the attempt evidence changed while it was classified',
                       evidence=_evidence(attempt))
    dispatched = counts['starts.jsonl'] + counts['outcomes.jsonl']
    reserved = counts['intents.jsonl']
    proposed = _journal_effect_rows(rows)
    failure = _terminal_failure(summary.get('fetch_ledger'), rows)
    transient, reason = _transient(failure)
    settled = summary.get('settled') is True
    status = summary.get('status')
    evidence = _evidence(attempt)
    if settled:
        return Attempt(SETTLED, 'canonical settlement confirmed', evidence,
                       settled=True, transient=transient,
                       dispatched=dispatched, reserved=reserved)
    if dispatched or proposed:
        return Attempt(RECONCILE,
                       f'{dispatched} dispatched effect(s) and {proposed} proposal/tool_result row(s) recorded',
                       evidence, settled=settled, transient=transient,
                       dispatched=dispatched, reserved=reserved)
    if status == 'complete':
        return Attempt(RECONCILE, 'the loop completed but the settlement was not confirmed',
                       evidence, settled=False, transient=transient,
                       dispatched=dispatched, reserved=reserved)
    if not transient:
        return Attempt(UNKNOWN,
                       f'no effect recorded, but the terminal failure was not transient '
                       f'({reason or "none recorded"})',
                       evidence, settled=settled, transient=False,
                       dispatched=dispatched, reserved=reserved)
    recorded, why = _failure_record(files[FAILURE_RECORD])
    if not recorded:
        return Attempt(UNKNOWN, f'no effect admitted, but {why}',
                       evidence, settled=settled, transient=True,
                       dispatched=dispatched, reserved=reserved)
    return Attempt(RETRYABLE, f'no effect admitted; terminal upstream failure {reason}',
                   evidence, settled=settled, transient=True,
                   dispatched=dispatched, reserved=reserved)


def attempt_summary(attempt) -> tuple[dict, str | None]:
    """A compact decoded summary of one attempt, and why it is unusable.

    The summary names the attempt's own outcome and the capabilities it
    dispatched, so a later attempt of the same wake can reconcile them instead
    of repeating them. It is decoded here, at the boundary, so no consumer
    reads the effect ledger itself.
    """
    attempt = Path(attempt)
    try:
        with _store_guard(attempt / 'effects'):
            files, error = _read_evidence(attempt)
            if error:
                return {}, error
            summary, error = _summary(files['result.json'])
            if error:
                return {}, error
            rows, error = _decode_rows(files['journal.jsonl'], 'journal.jsonl')
            if error:
                return {}, error
            decoded = {}
            for name, decoder in LEDGER_JOURNALS:
                rows, error = _decode_rows(files[f'effects/{name}'], name, decoder)
                if error:
                    return {}, error
                decoded[name] = rows
    except LiveWriter as exc:
        return {}, str(exc)
    capabilities: list[str] = []
    for row in decoded['intents.jsonl']:
        name = row.get('capability')
        if name and name not in capabilities:
            capabilities.append(name)
    return {'wake': summary.get('wake'), 'settled': summary.get('settled'),
            'status': summary.get('status'), 'turns': summary.get('turns'),
            'calls': summary.get('calls'), 'capabilities': capabilities}, None


def fresh_attempt(directory, wake: int, *, limit: int = DEFAULT_RETRY_LIMIT) -> Path | None:
    """The next unused fresh-attempt directory for this wake, or None.

    A fresh attempt never reuses an earlier journal, so no earlier outcome is
    replayed. The operator's own `-recovery-<n>` naming is separate and is
    never chosen here.
    """
    if type(limit) is not int or limit <= 0:
        raise ValueError('positive fresh-attempt limit required')
    for number in range(1, limit + 1):
        candidate = Path(directory) / f'{wake}-retry-{number}'
        if not candidate.exists():
            return candidate
    return None


def release_busy(busy: Path, wake: int) -> bool:
    """Release the busy wedge for exactly this wake.

    The marker is claimed by an atomic rename and read at its claimed name, so
    a marker that was replaced in the meantime is read as the replacement it
    is and put back where it was. Another wake's marker is never removed.
    """
    busy = Path(busy)
    claimed = busy.with_name(f'{busy.name}.claimed-{wake}-{os.getpid()}')
    try:
        os.rename(busy, claimed)
    except OSError:
        return False
    try:
        data = claimed.read_bytes()
    except OSError:
        data = b''
    recorded = data.decode('utf-8', 'replace').strip()
    if recorded != str(wake):
        # Another wake's marker: put it back, but never over a marker that was
        # published in the meantime. A hard link refuses an existing
        # destination, so a concurrent marker survives.
        try:
            os.link(claimed, busy)
        except OSError:
            pass
        else:
            try:
                claimed.unlink()
            except OSError:
                pass
        return False
    try:
        claimed.unlink()
    except OSError:
        pass
    return True


@dataclass(frozen=True)
class Disposition:
    """What the plant may do with a wake that already has an attempt."""

    action: str
    attempt: Path | None
    verdict: str
    reason: str
    evidence: tuple[str, ...] = ()
    settled: bool = False
    transient: bool = False

    @property
    def recoverable(self) -> bool:
        """A fresh attempt is authorized only by a provably effect-free failure."""
        return self.action == 'fresh-attempt'

    def to_record(self) -> dict:
        return {'action': self.action, 'attempt': str(self.attempt) if self.attempt else None,
                'verdict': self.verdict, 'reason': self.reason, 'evidence': list(self.evidence),
                'settled': self.settled, 'transient': self.transient,
                'recoverable': self.recoverable}


def attempt_disposition(attempts, wake: int, *, limit: int = DEFAULT_RETRY_LIMIT) -> Disposition:
    """Choose the bounded next action for one wake from its own attempt bytes.

    A wake with no attempt may start one. A wake whose earlier attempt is a
    provably effect-free transient failure may take the next unused fresh
    attempt, which is a new directory with a new journal, so no effect and no
    earlier outcome is replayed. Every other wake is held for reconciliation:
    an attempt that dispatched an effect, proposed a capability call, settled,
    or whose own evidence is unknown never authorizes another attempt. A wake
    whose attempt is still being written is left alone, and a wake whose
    settlement is already confirmed needs no attempt at all.
    """
    directory = Path(attempts)
    paths = _wake_directories(directory, wake)
    for path in paths:
        if writer_active(path):
            return Disposition(action='live', attempt=None, verdict=UNKNOWN,
                               reason=f'an attempt of this wake is still being written: {path.name}')
    reports = [classify_attempt(path) for path in paths]
    if not reports:
        return Disposition(action='start', attempt=directory / str(wake), verdict=UNKNOWN,
                           reason=f'wake {wake} has no attempt yet')
    settled = [report for report in reports if report.settled]
    if settled:
        return Disposition(action='settled', attempt=None, verdict=SETTLED,
                           reason=f'wake {wake} is already settled; no attempt is needed',
                           evidence=settled[0].evidence, settled=True)
    blocking = [report for report in reports if not report.recoverable]
    if blocking:
        held = blocking[0]
        return Disposition(action='reconcile', attempt=None, verdict=held.verdict,
                           reason=held.reason, evidence=held.evidence, settled=held.settled,
                           transient=held.transient)
    fresh = fresh_attempt(directory, wake, limit=limit)
    if fresh is None:
        return Disposition(action='reconcile', attempt=None, verdict=RECONCILE,
                           reason=(f'wake {wake} used every bounded fresh attempt '
                                   f'({limit}); reconcile before any further attempt'))
    return Disposition(action='fresh-attempt', attempt=fresh, verdict=RETRYABLE,
                       reason=(f'every attempt of wake {wake} is effect-free and transient; '
                               f'a fresh attempt may start'),
                       evidence=reports[-1].evidence, settled=False, transient=True)


# The plant-visible record of a wake that cannot proceed. The resident writes
# it when it holds a wake for reconciliation; the readiness gate reads it, so a
# wedged native resident is observable without reading an attempt directory.
BLOCKED_STATE = '{role}-native.blocked.json'


def blocked_state_path(home, role: str) -> Path:
    """The path of one role's blocked-state record."""
    return Path(home) / 'journals' / BLOCKED_STATE.format(role=role)


def write_blocked_state(home, role: str, wake, *, verdict: str, reason: str,
                        evidence=(), attempt=None) -> Path:
    """Record that this role's wake cannot proceed, with the classified reason."""
    path = blocked_state_path(home, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {'version': 1, 'role': role, 'wake': wake, 'verdict': verdict, 'reason': reason,
              'evidence': list(evidence), 'attempt': str(attempt) if attempt is not None else None,
              'at': datetime.now(timezone.utc).isoformat()}
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def read_blocked_state(home, role: str) -> tuple[dict | None, str | None]:
    """The decoded blocked record for this role, and why it is unusable.

    An absent record is ``(None, None)``: the role is not blocked. A record
    that exists but cannot be decoded is ``(None, reason)``, so broken
    monitoring stays visible instead of reading as an absent failure.
    """
    path = blocked_state_path(home, role)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f'blocked state unreadable: {exc}'
    try:
        record = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        return None, f'blocked state malformed: {exc}'
    if not isinstance(record, dict):
        return None, 'blocked state is not a record'
    return record, None


def _restore_claimed(claimed: Path, path: Path) -> None:
    """Put a claimed record back where it was, when the path is free.

    A hard link refuses an existing destination, so a record published in the
    meantime keeps the path and the claimed copy stays as the evidence.
    """
    try:
        os.link(claimed, path)
    except OSError:
        return
    try:
        claimed.unlink()
    except OSError:
        pass


def clear_blocked_state(home, role: str, wake=None) -> bool:
    """Remove one role's blocked-state record; True when one was removed.

    When ``wake`` is given, only a record that names that wake is removed: a
    hold belongs to the wake it names, and another wake's hold must survive.
    The record is claimed by an atomic rename and read at its claimed name, so
    a record published in the meantime is never the one removed. A record that
    cannot be decoded names no wake: it is kept, because it is the only
    evidence that a hold exists, and repairing it is an authoritative act.
    """
    path = blocked_state_path(home, role)
    claimed = path.with_name(f'.{path.name}.claimed-{os.getpid()}')
    try:
        os.rename(path, claimed)
    except OSError:
        return False
    try:
        data = claimed.read_bytes()
    except OSError:
        data = b''
    try:
        value = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        value = None
    if not isinstance(value, dict):
        _restore_claimed(claimed, path)
        return False
    if wake is not None and value.get('wake') != wake:
        _restore_claimed(claimed, path)
        return False
    try:
        claimed.unlink()
    except OSError:
        _restore_claimed(claimed, path)
        return False
    return True


def _wake_directories(attempts: Path, wake: int) -> list[Path]:
    """The attempt directories of one wake, oldest first.

    The first attempt and its bounded fresh attempts are ordered by the
    numeric retry suffix, so `retry-10` follows `retry-9`. The operator's own
    `recovery-<n>` attempts are the same wake's attempts and are counted here,
    so an effect an operator's recovery attempt dispatched blocks a later
    attempt of that wake instead of going unseen.
    """
    if not attempts.is_dir():
        return []
    retry = f'{wake}-retry-'
    recovery = f'{wake}-recovery-'
    entries = []
    for path in attempts.iterdir():
        if not path.is_dir():
            continue
        name = path.name
        if name == str(wake):
            entries.append((0, 0, name, path))
        elif name.startswith(retry) and name[len(retry):].isdigit():
            entries.append((1, int(name[len(retry):]), name, path))
        elif name.startswith(recovery) and name[len(recovery):].isdigit():
            entries.append((2, int(name[len(recovery):]), name, path))
    entries.sort()
    return [path for _, _, _, path in entries]


def wake_attempts(attempts, wake: int) -> list[Path]:
    """The attempt directories of one wake, oldest first.

    The enumeration is the boundary's own: a consumer never re-derives which
    directories belong to a wake.
    """
    return _wake_directories(Path(attempts), wake)


def _report(attempts: Path, wake: int | None) -> list[Attempt]:
    if not attempts.is_dir():
        return []
    directories = sorted((path for path in attempts.iterdir() if path.is_dir()),
                         key=lambda path: (int(path.name.split('-', 1)[0])
                                           if path.name.split('-', 1)[0].isdigit() else 0,
                                           path.name))
    if wake is not None:
        directories = _wake_directories(attempts, wake)
    return [classify_attempt(path) for path in directories]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Classify failed native wake attempts.')
    parser.add_argument('--attempts', type=Path, required=True,
                        help='directory holding attempt directories')
    parser.add_argument('--wake', type=int, help='wake number to act on')
    parser.add_argument('--busy', type=Path, help='busy marker to release with --apply')
    parser.add_argument('--limit', type=int, default=DEFAULT_RETRY_LIMIT,
                        help='bound on fresh attempts per wake')
    parser.add_argument('--apply', action='store_true',
                        help='release the busy wedge for an effect-free transient attempt')
    args = parser.parse_args(argv)
    if args.apply and (args.busy is None or args.wake is None):
        parser.error('--apply needs --busy and --wake')
    attempts = _report(args.attempts, args.wake)
    records = [attempt.to_record() for attempt in attempts]
    report = {'attempts': str(args.attempts), 'wake': args.wake, 'results': records}
    if args.apply:
        if not attempts:
            report['applied'] = False
            report['reason'] = 'the named wake has no attempt directory'
            print(json.dumps(report, indent=2))
            return 1
        blocking = [attempt for attempt in attempts
                    if attempt.verdict != RETRYABLE]
        if blocking:
            report['applied'] = False
            report['reason'] = (f'recovery refused: an attempt of this wake is '
                                f'{blocking[0].verdict} ({blocking[0].reason})')
            print(json.dumps(report, indent=2))
            return 1
        latest = attempts[-1]
        if not latest.recoverable:
            report['applied'] = False
            report['reason'] = f'recovery refused: {latest.verdict} ({latest.reason})'
            print(json.dumps(report, indent=2))
            return 1
        candidate = fresh_attempt(args.attempts, args.wake, limit=args.limit)
        if candidate is None:
            report['applied'] = False
            report['reason'] = 'bounded fresh-wake recovery exhausted for this wake'
            print(json.dumps(report, indent=2))
            return 1
        if not release_busy(args.busy, args.wake):
            report['applied'] = False
            report['reason'] = f'busy marker {args.busy} does not name wake {args.wake}'
            print(json.dumps(report, indent=2))
            return 1
        report['applied'] = True
        report['fresh_attempt'] = str(candidate)
        print(json.dumps(report, indent=2))
        return 0
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
