"""Outcome publication identity and coverage shared by writer and observer."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from . import records
from .feed import parse_feed

OUTCOME_KINDS = ('accepted', 'blocker-resolved', 'blocker-retired', 'hypothesis-changed')
_HEADER = re.compile(r'Wall outcome (' + '|'.join(OUTCOME_KINDS) + r') by ([a-z0-9-]+)\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def classify_outcomes(home: Path, clause_time: datetime) -> dict:
    """Return eligible events and explicit gaps; a digest is not event identity.

    Reserved first significant headers are candidates even when damaged. A
    standalone unresolved reference is a coverage gap until proven non-outcome.
    Known reference-only citations and ordinary inline citations are not events.
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
        lines = entry.body.splitlines()
        first = next((line for line in lines if line.strip()), '')
        reserved = first.lstrip().startswith('Wall outcome ')
        refs = [line for line in lines if line.lstrip().startswith('[record]')]
        if not reserved and not refs:
            continue
        try:
            timestamp = datetime.fromisoformat(entry.timestamp.replace('Z', '+00:00'))
            if timestamp < clause_time:
                continue
        except (ValueError, TypeError) as exc:
            incomplete.append({'sequence': entry.sequence, 'reason': f'invalid event time: {exc}'})
            continue
        try:
            if len(refs) != 1 or not lines or refs[0] != lines[-1]:
                raise ValueError('publication requires one standalone final record reference')
            envelope = records.envelope(entry)
            if not reserved and envelope['kind'] != 'wall-outcome':
                continue
            if not reserved and len(lines) == 1:
                continue
            if envelope['kind'] != 'wall-outcome':
                raise ValueError('reserved publication has wrong envelope kind')
            header = _HEADER.fullmatch(lines[0])
            if header is None:
                raise ValueError('malformed, displaced or replaced outcome header')
            payload = envelope['payload']
            if payload.get('role') != entry.source or payload.get('role') != header.group(2):
                raise ValueError('outcome source/header/payload role mismatch')
            if payload.get('kind') != header.group(1):
                raise ValueError('outcome header/payload kind mismatch')
            text = payload.get('text')
            if not isinstance(text, str) or not text.strip() or '\n'.join(lines[1:-1]) != text:
                raise ValueError('outcome reported text mismatch')
            evidence = payload.get('evidence')
            if (not isinstance(evidence, dict) or not isinstance(evidence.get('path'), str)
                    or not evidence['path'] or not isinstance(evidence.get('sha256'), str)
                    or _DIGEST.fullmatch(evidence['sha256']) is None):
                raise ValueError('invalid outcome evidence binding')
        except (OSError, ValueError, TypeError) as exc:
            incomplete.append({'sequence': entry.sequence, 'reason': str(exc)})
            continue
        digest = records.REFERENCE_RE.fullmatch(refs[0]).group(1)
        events.append({'site': str(home), 'sequence': entry.sequence,
                       'timestamp': entry.timestamp, 'record_sha256': digest,
                       'role': payload['role'], 'evidence': evidence})
    return {'events': events, 'incomplete': incomplete, 'unavailable': None}
