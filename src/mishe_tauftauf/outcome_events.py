"""Outcome publication identity and coverage shared by writer and observer."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import records
from .feed import iter_feed_file
from .outcome_publication import (
    body_lines,
    decode_evidence,
    decode_supersedes,
    decode_text,
    decode_timestamp,
    eligible_evidence_path,
    first_significant_line,
    has_outcome_prefix,
    parse_header,
    publication_prose,
    reference_digest,
    reference_lines,
    sole_final_reference,
)



def parse_feed(tape: Path, *, home: Path | None = None):
    """Read the tape as a stream of entries: this module's one tape-scan seam.

    The classifier reads the tape through this name, so a reader can prove that
    a supplied snapshot reuses the classifier instead of scanning the tape a
    second time: replacing this function turns any scan into a visible
    failure. The tape is streamed rather than read whole, so the peak does not
    grow with the tape.
    """
    return iter_feed_file(tape, home=home)


def binding_relevance(lines: list[str]) -> str:
    """Whether a damaged candidate can still carry an unknown evidence binding.

    A reserved header or an immutable reference marks a publication attempt, so
    a damaged one may hide a bound evidence path and must fail closed. A
    message that only starts with the reserved prefix, carrying neither a
    header nor a reference, binds no evidence path at all: it stays a
    reported coverage gap, attributed to its author, but it cannot block
    another role's publication.
    """
    if reference_lines(lines):
        return 'unknown'
    if parse_header(first_significant_line(lines)) is not None:
        return 'unknown'
    return 'none'


def blocking_gaps(coverage: dict) -> list:
    """The gaps that still hide a bound evidence path, so the writer fails closed.

    A gap with ``binding`` ``none`` binds nothing and cannot block a
    publication; every other gap may hide a bound path, so the bound set
    cannot be derived and the writer must refuse. One definition, so the
    writer and the reader of the plant's state cannot disagree.
    """
    return [row for row in coverage["incomplete"] if row.get("binding", "unknown") != "none"]


def non_blocking_gaps(coverage: dict) -> list:
    """The gaps that bind nothing, so they cannot hide a bound evidence path.

    The complement of :func:`blocking_gaps`, so the reader of a coverage gap
    and the writer that fails closed on one cannot disagree about what a gap
    does. Such a gap stays in the coverage report — it is a real gap in the
    tape — but it cannot make the bound set undeterminable, so it is not a
    reason for a reading to be UNKNOWN.
    """
    blocking = {row["sequence"] for row in blocking_gaps(coverage)}
    return [row for row in coverage.get("incomplete") or []
            if row["sequence"] not in blocking]


def classify_outcomes(home: Path, clause_time: datetime, *, entries=None) -> dict:
    """Return eligible events and explicit gaps; a digest is not event identity.

    Reserved first significant headers are candidates even when damaged. A
    standalone unresolved reference is a coverage gap until proven non-outcome.
    Known reference-only citations and ordinary inline citations are not events.

    Every gap carries its ``binding`` relevance: ``unknown`` when the damaged
    entry may still hide a bound evidence path, ``none`` when it binds nothing
    and therefore cannot block another role's publication.

    The publication dialect is decoded by ``outcome_publication``; what stays
    here is the eligibility policy — which publications count as outcomes, and
    why a damaged one is a gap rather than an event.

    Supplied entries reuse a caller's tape snapshot without rereading it.
    """
    home = home.resolve()
    events, incomplete = [], []
    if entries is None:
        tape = home / 'chat.log'
        if not tape.exists():
            return {'events': events, 'incomplete': incomplete, 'unavailable': 'chat tape unavailable'}
        # The tape is read as a stream, so the classification never holds it.
        # The whole-tape read kept every entry in memory at once, and the
        # evidence-binding gate's peak grew with the tape because of it.
        entries = parse_feed(tape, home=home)
    if not (home / 'records').is_dir():
        return {'events': events, 'incomplete': incomplete, 'unavailable': 'outcome store unavailable'}
    import os as _os
    directory = None
    try:
        directory = records._directory(home)
    except OSError as exc:
        return {'events': events, 'incomplete': incomplete, 'unavailable': f'outcome store unreadable: {exc}'}
    try:
        stream = iter(entries)
        while True:
            try:
                entry = next(stream)
            except StopIteration:
                break
            except (OSError, ValueError) as exc:
                # A tape that faults is not a shorter tape: the reading is
                # abandoned whole, exactly as the whole-tape read abandoned it
                # before it had classified anything.
                return {'events': [], 'incomplete': [], 'unavailable': f'chat tape unreadable: {exc}'}
            lines = body_lines(entry.body)
            first = first_significant_line(lines)
            reserved = has_outcome_prefix(first)
            refs = reference_lines(lines)
            if not reserved and not refs:
                continue
            try:
                timestamp = decode_timestamp(entry.timestamp)
                if timestamp < clause_time:
                    continue
            except (ValueError, TypeError) as exc:
                incomplete.append({'sequence': entry.sequence, 'reason': f'invalid event time: {exc}',
                                   'binding': 'unknown'})
                continue
            binding = binding_relevance(lines)
            try:
                if len(refs) != 1 or sole_final_reference(lines) is None:
                    raise ValueError('publication requires one standalone final record reference')
                # Bounded fast-path: the record is written with sorted keys so the
                # kind is the first field. Peek the head (64 bytes) to discard
                # non-outcome citations without reading the whole record.
                # A head that cannot be read or that names no kind falls through
                # to the full envelope verification so the gap stays UNKNOWN with
                # the correct binding, exactly as before.
                digest = reference_digest(refs[0])
                head = records.record_head_dir(directory, digest)
                kind = records.record_kind(head) if head is not None else None
                if kind is not None and not reserved and kind != 'wall-outcome':
                    continue
                envelope = records.envelope(entry)
                if kind is None and not reserved and envelope['kind'] != 'wall-outcome':
                    continue
                if not reserved and len(lines) == 1:
                    continue
                if envelope['kind'] != 'wall-outcome':
                    raise ValueError('reserved publication has wrong envelope kind')
                header = parse_header(lines[0])
                if header is None:
                    raise ValueError('malformed, displaced or replaced outcome header')
                payload = envelope['payload']
                if payload.get('role') != entry.source or payload.get('role') != header[1]:
                    raise ValueError('outcome source/header/payload role mismatch')
                if payload.get('kind') != header[0]:
                    raise ValueError('outcome header/payload kind mismatch')
                text = payload.get('text')
                if decode_text(text) is None or publication_prose(lines) != text:
                    raise ValueError('outcome reported text mismatch')
                evidence = decode_evidence(payload.get('evidence'))
                if evidence is None:
                    raise ValueError('invalid outcome evidence binding')
                supersedes = decode_supersedes(payload.get('supersedes'))
            except (OSError, ValueError, TypeError) as exc:
                incomplete.append({'sequence': entry.sequence, 'reason': str(exc), 'binding': binding})
                continue
            digest = reference_digest(refs[0])
            events.append({'site': str(home), 'sequence': entry.sequence,
                           'timestamp': entry.timestamp, 'record_sha256': digest,
                           'role': payload['role'], 'kind': payload['kind'], 'evidence': evidence,
                           'supersedes': supersedes})
    finally:
        if directory is not None:
            _os.close(directory)
    return {'events': events, 'incomplete': incomplete, 'unavailable': None}
