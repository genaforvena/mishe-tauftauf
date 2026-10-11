"""Light-stream retention record: the approved adapter for the reader's own note.

To name a sustained source freeze the light reader must compare its current null
observation with its own previous one, and that comparison needs a small
persisted document. ``discovery`` carries frozen legacy parsing debt and the
frozen budget must not widen to pass a change, so decoding and encoding that
record is an interpretation of external text and lives here as a boundary; the
reader acts on a plain answer.
"""
from __future__ import annotations

import json


def decode_stream_record(text: str) -> dict | None:
    """The retained null observation, or None for absent or malformed text.

    A record that does not decode is not evidence of anything, so the caller
    keeps its single-read name rather than claiming a duration class.
    """
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def encode_stream_record(record: dict) -> str:
    """The retained observation as one line of sorted-key JSON."""
    return json.dumps(record, sort_keys=True) + "\n"
