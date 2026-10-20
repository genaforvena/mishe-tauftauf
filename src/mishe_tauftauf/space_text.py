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
import math
import re


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

#: The producer contract of the shared space publication. A file that does
#: not carry this producer is not the space boundary, whatever shape it has.
SPACE_PRODUCER = {"name": "android-body-perception", "entry_point": "watch.py",
                  "mode": "space", "contract": 1}


def decode_space_boundary(text: str | None) -> dict | None:
    """Decode the shared space publication, or return None when it is unreadable."""
    try:
        value = json.loads(text) if text is not None else None
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    nodes = value.get("nodes")
    if isinstance(nodes, list) and any(
            not isinstance(node, dict)
            or not isinstance(node.get("node"), str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]*", node["node"]) is None
            for node in nodes):
        return None
    return value


CLOCK_DOMAIN_STATUSES = ("ok", "absent", "invalid", "stale")


def decode_clock_domain(clock_domain: object) -> dict | None:
    """The additive per-node clock-offset reading, or None for a malformed field.

    ``clock_domain`` is the versioned ``clock_domain`` node field the producer
    projects from its own ``read_clock_domain`` verdict. Shape validation lives
    here so a reader acts on a plain answer; consistency (nominal within the
    bounds) and freshness (age within ``validity_seconds``) are the reader's
    judgement, never repaired here. An inconsistent reading is preserved, not
    coerced: the reader sees the nominal as published and names the conflict.
    """
    if (not isinstance(clock_domain, dict) or type(clock_domain.get("version")) is not int
            or clock_domain["version"] != 1):
        return None
    status = clock_domain.get("status")
    if status not in CLOCK_DOMAIN_STATUSES:
        return None

    def finite(key: str) -> float | None:
        value = clock_domain.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            number = float(value)
        except OverflowError:
            return None
        return number if math.isfinite(number) else None

    offsets = {key: finite(key) for key in
               ("offset_nominal_s", "offset_min_s", "offset_max_s",
                "observed_at_epoch_s", "validity_seconds")}
    samples = clock_domain.get("samples")
    source = clock_domain.get("source")
    reason = clock_domain.get("reason")
    jump = clock_domain.get("jump")
    if (type(samples) is not int or samples < 0
            or (source is not None and not isinstance(source, str))
            or (reason is not None and not isinstance(reason, str))
            or (jump is not None and not isinstance(jump, dict))):
        return None
    return {"version": 1, "status": status,
            "offset_nominal_s": offsets["offset_nominal_s"],
            "offset_min_s": offsets["offset_min_s"],
            "offset_max_s": offsets["offset_max_s"],
            "observed_at_epoch_s": offsets["observed_at_epoch_s"],
            "validity_seconds": offsets["validity_seconds"],
            "samples": samples, "source": source, "reason": reason, "jump": jump}
