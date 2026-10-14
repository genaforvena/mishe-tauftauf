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


def decode_stream_log(text: str) -> list[dict]:
    """The retained log's records, or the empty list for absent or unreadable text.

    One unreadable line hides only that line: the log is an audit trail whose
    value is the events it does hold, so a torn or hand-edited line is skipped
    rather than failing the whole read.
    """
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records

def nonblank(text: object) -> str | None:
    """The text itself when it reads as text with visible content, else None.

    Blank-checking is interpretation of external text, so it lives at this
    boundary; readers act on the plain answer and never call ``strip`` on a
    decoded value themselves.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    return text

# The producer's own bounded node-state verdict carries one mode enum. These
# three values mean the node is deliberately not delivering over HTTP: naming
# them is what tells a designed silence from a delivery failure. ON_SENDING and
# UNKNOWN are absent here on purpose — the first declares delivery is on, and
# the second declares nothing a reader may attribute a silence to.
NOT_SENDING_MODES = {
    "ON_COLLECT_ONLY": "collect-only",
    "ON_NOT_RUNNING": "producer-not-running",
    "OFF": "producer-off",
}


def decode_node_state(node_state: object) -> str | None:
    """The producer's declared reason for delivering nothing, or None.

    ``node_state`` is the additive per-node field the producer projects from its
    own host-side verdict, and its ``status`` is the producer's own judgement of
    freshness and shape: anything but ``ok`` is absent, stale or invalid, so it
    declares nothing here. ``None`` therefore means "the producer declares it is
    sending, or declares nothing at all" — a reader that cannot read a
    declaration keeps its own name for the absent channel instead of claiming a
    configured silence it cannot see. Only the documented mode enum is
    interpreted; every other field of the verdict is opaque content.
    """
    if not isinstance(node_state, dict) or node_state.get("status") != "ok":
        return None
    mode = node_state.get("mode")
    if not isinstance(mode, str):
        return None
    return NOT_SENDING_MODES.get(mode)
