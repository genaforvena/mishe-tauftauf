"""Outcome publication identity and coverage shared by writer and observer."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import records
from .feed import parse_feed
from .outcome_publication import (
    body_lines,
    decode_evidence,
    decode_text,
    decode_timestamp,
    first_significant_line,
    has_outcome_prefix,
    parse_header,
    publication_prose,
    reference_digest,
    reference_lines,
    sole_final_reference,
)


def classify_outcomes(home: Path, clause_time: datetime) -> dict:
    """Return eligible events and explicit gaps; a digest is not event identity.

    Reserved first significant headers are candidates even when damaged. A
    standalone unresolved reference is a coverage gap until proven non-outcome.
    Known reference-only citations and ordinary inline citations are not events.

    The publication dialect is decoded by ``outcome_publication``; what stays
    here is the eligibility policy — which publications count as outcomes, and
    why a damaged one is a gap rather than an event.
    """
    home = home.resolve()
    events, incomplete = [], []
    tape = home / 'chat.log'
    if not tape.exists():
        return {'events': events, 'incomplete': incomplete, 'unavailable': 'chat tape unavailable'}
    try:
        entries = parse_feed(tape.read_bytes(), home=home)
    except (OSError, ValueError) as exc:
        return {'events': events, 'incomplete': incomplete, 'unavailable': f'chat tape unreadable: {exc}'}
    if not (home / 'records').is_dir():
        return {'events': events, 'incomplete': incomplete, 'unavailable': 'outcome store unavailable'}
    for entry in entries:
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
            incomplete.append({'sequence': entry.sequence, 'reason': f'invalid event time: {exc}'})
            continue
        try:
            if len(refs) != 1 or sole_final_reference(lines) is None:
                raise ValueError('publication requires one standalone final record reference')
            envelope = records.envelope(entry)
            if not reserved and envelope['kind'] != 'wall-outcome':
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
        except (OSError, ValueError, TypeError) as exc:
            incomplete.append({'sequence': entry.sequence, 'reason': str(exc)})
            continue
        digest = reference_digest(refs[0])
        events.append({'site': str(home), 'sequence': entry.sequence,
                       'timestamp': entry.timestamp, 'record_sha256': digest,
                       'role': payload['role'], 'evidence': evidence})
    return {'events': events, 'incomplete': incomplete, 'unavailable': None}
