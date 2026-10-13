#!/usr/bin/env python3
"""OMP event stream → canonical wire profile adapter (semantic mapper v2).

This adapter maps OMP's JSONL event stream to canonical wire profile records.
It is a LOSSY SEMANTIC MAPPER, not a format converter. Each mapping decision
is documented below with rationale.

Design decisions (from witness review DM 35673):
1. Operation identity: adapter-synthesized with provenance, NOT caller-known.
   Format: "omp-adapter:{session_id}:{sequence}".
2. Authority: declared as "omp-internal" — OMP events do not carry authority
   information; the adapter does not add it.
3. Binary data: re-encoded from JSON text to bytes.begin + binary frames.
4. Session metadata: dropped with documentation — no canonical equivalent.
   However, the session ID IS captured for operation identity provenance.
5. Schema mapping: each OMP event type maps to the closest canonical record,
   with explicit documentation of what is lost.

OMP event format (observed 2026-10-13):
- session: {"type":"session","id":"<uuid>","version":3,"timestamp":"...","cwd":"..."}
- message_start/end: {"type":"message_start","message":{"role":"user","content":[...]}}
- message_update: {"type":"message_update","message":{...}}
- turn_start/end: {"type":"turn_start"} / {"type":"turn_end"}
- agent_start/end: {"type":"agent_start"} / {"type":"agent_end"}

Canonical wire profile bounds (from canonical-wire-profile-20261003.md):
- 65,536 bytes/record (excluding LF)
- depth 16, 256 members/object, 256 array elements
- 16 KiB strings, 1,024 records/session
- Binary: bytes.begin + stream_id[16] || sequence[4] || payload_length[2] || payload
- Max chunk 16 KiB, max observation 1 MiB
"""

import json
import struct
import sys
import hashlib
from pathlib import Path

# Canonical wire profile bounds
MAX_CONTROL_BYTES = 65536
MAX_DEPTH = 16
MAX_MEMBERS = 256
MAX_ARRAY_ELEMENTS = 256
MAX_STRING_BYTES = 16 * 1024
MAX_RECORDS_PER_SESSION = 1024
MAX_BINARY_CHUNK = 16 * 1024
MAX_BINARY_TOTAL = 1 * 1024 * 1024

WRITER_ID = "omp-agent-loop"
AUTHORITY = "omp-internal"


def check_depth(obj, depth=1):
    if depth > MAX_DEPTH:
        return False
    if isinstance(obj, dict):
        return all(check_depth(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return all(check_depth(v, depth + 1) for v in obj)
    return True


def check_members(obj):
    if isinstance(obj, dict):
        if len(obj) > MAX_MEMBERS:
            return False
        return all(check_members(v) for v in obj.values())
    if isinstance(obj, list):
        if len(obj) > MAX_ARRAY_ELEMENTS:
            return False
        return all(check_members(v) for v in obj)
    return True


def check_string_bytes(obj):
    if isinstance(obj, str):
        if len(obj.encode("utf-8")) > MAX_STRING_BYTES:
            return False
        return True
    if isinstance(obj, dict):
        for k, v in obj.items():
            if len(k.encode("utf-8")) > MAX_STRING_BYTES:
                return False
            if not check_string_bytes(v):
                return False
    if isinstance(obj, list):
        return all(check_string_bytes(v) for v in obj)
    return True


def validate_record(obj):
    """Validate a canonical record against wire profile bounds."""
    if not check_depth(obj):
        raise ValueError(f"depth exceeds {MAX_DEPTH}")
    if not check_members(obj):
        raise ValueError(f"members exceed {MAX_MEMBERS} or array exceeds {MAX_ARRAY_ELEMENTS}")
    if not check_string_bytes(obj):
        raise ValueError(f"string exceeds {MAX_STRING_BYTES} bytes")
    encoded = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CONTROL_BYTES:
        raise ValueError(f"record exceeds {MAX_CONTROL_BYTES} bytes")
    return encoded


def make_operation_id(session_id, sequence):
    """Synthesize operation ID with clear provenance.

    OMP events do not carry operation IDs. The canonical wire profile requires
    "nonempty caller-known operation identity before any effect-capable dispatch."
    These IDs are adapter-synthesized, NOT caller-known. Provenance is explicit.
    """
    return f"omp-adapter:{session_id}:{sequence}"


def extract_message_content(event):
    """Extract role and content from OMP message events.

    OMP nests message data under event["message"]:
      {"type":"message_start","message":{"role":"user","content":[...]}}
    Content is an array of content blocks: [{"type":"text","text":"..."}].
    """
    msg = event.get("message", {})
    role = msg.get("role", "unknown")
    content = msg.get("content", [])
    # Extract text from content blocks for a cleaner canonical representation
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        text_content = "".join(text_parts)
    else:
        text_content = str(content)
    return role, text_content


def map_event(event, session_id, sequence):
    """Map one OMP event to a canonical record (or None to skip).

    Mapping table (OMP event → canonical record):
    - session → None (metadata dropped, but session_id captured in main())
    - thinking_level_changed → None (metadata, no canonical equivalent)
    - agent_start → None (metadata, no canonical equivalent)
    - turn_start → intent (request to start agent turn)
    - message_start (user) → intent (user message request)
    - message_start (assistant) → outcome (assistant message reply)
    - message_update → outcome (partial reply, indeterminate)
    - message_end → outcome (complete reply)
    - thinking_delta → outcome (partial reply, indeterminate)
    - thinking_start/thinking_end → None (metadata)
    - unknown → outcome (unsupported status)

    Semantic losses:
    - Thinking deltas are intermediate reasoning steps, not partial replies.
    - Tool calls are effect-capable dispatches, not partial replies.
    - Session metadata has no canonical equivalent.
    - Authority information is absent in OMP events.
    """
    etype = event.get("type", "")
    op_id = make_operation_id(session_id, sequence)

    if etype == "session":
        return None

    if etype == "turn_start":
        return {
            "type": "intent",
            "operation_id": op_id,
            "writer_id": WRITER_ID,
            "authority": AUTHORITY,
            "capability": "agent.turn",
            "request": {"action": "start", "turn": event.get("turn")},
            "timestamp": event.get("timestamp", ""),
        }

    if etype in ("thinking_level_changed",):
        return None

    if etype == "message_start":
        role, content = extract_message_content(event)
        if role == "user":
            return {
                "type": "intent",
                "operation_id": op_id,
                "writer_id": WRITER_ID,
                "authority": AUTHORITY,
                "capability": "agent.message",
                "request": {"role": role, "content": content},
                "timestamp": event.get("timestamp", ""),
            }
        else:
            return {
                "type": "outcome",
                "operation_id": op_id,
                "writer_id": WRITER_ID,
                "authority": AUTHORITY,
                "capability": "agent.message",
                "request": {"role": role, "content": content},
                "timestamp": event.get("timestamp", ""),
                "status": "indeterminate",
            }

    if etype == "message_update":
        role, content = extract_message_content(event)
        return {
            "type": "outcome",
            "operation_id": op_id,
            "writer_id": WRITER_ID,
            "authority": AUTHORITY,
            "capability": "agent.message",
            "request": {"role": role, "delta": content},
            "timestamp": event.get("timestamp", ""),
            "status": "indeterminate",
        }

    if etype == "message_end":
        role, content = extract_message_content(event)
        return {
            "type": "outcome",
            "operation_id": op_id,
            "writer_id": WRITER_ID,
            "authority": AUTHORITY,
            "capability": "agent.message",
            "request": {"role": role, "content": content},
            "timestamp": event.get("timestamp", ""),
            "status": "completed",
        }

    if etype == "thinking_delta":
        return {
            "type": "outcome",
            "operation_id": op_id,
            "writer_id": WRITER_ID,
            "authority": AUTHORITY,
            "capability": "agent.thinking",
            "request": {"delta": event.get("delta", "")},
            "timestamp": event.get("timestamp", ""),
            "status": "indeterminate",
        }

    if etype in ("thinking_start", "thinking_end", "agent_start"):
        return None

    return {
        "type": "outcome",
        "operation_id": op_id,
        "writer_id": WRITER_ID,
        "authority": AUTHORITY,
        "capability": "agent.unknown",
        "request": {"event_type": etype},
        "timestamp": event.get("timestamp", ""),
        "status": "unsupported",
    }


def encode_bytes_begin(stream_id_hex, total_length, chunk_max):
    """Encode a bytes.begin record for binary data transfer."""
    record = {
        "type": "bytes.begin",
        "stream": stream_id_hex,
        "total_length": total_length,
        "chunk_max": chunk_max,
    }
    return validate_record(record)


def encode_binary_frame(stream_id_bytes, sequence, payload):
    """Encode a binary frame: stream_id[16] || sequence[4] || payload_length[2] || payload."""
    if len(stream_id_bytes) != 16:
        raise ValueError("stream_id must be 16 bytes")
    if len(payload) > MAX_BINARY_CHUNK:
        raise ValueError(f"payload exceeds {MAX_BINARY_CHUNK} bytes")
    header = struct.pack(">16sIH", stream_id_bytes, sequence, len(payload))
    return header + payload


def reencode_binary(content_bytes, session_id):
    """Re-encode binary content as bytes.begin + binary frames.

    OMP encodes binary as JSON text. The canonical wire profile uses bytes.begin
    + fixed-header binary frames. This function bridges the gap.
    """
    if len(content_bytes) > MAX_BINARY_TOTAL:
        raise ValueError(f"binary content exceeds {MAX_BINARY_TOTAL} bytes")

    # Generate deterministic stream ID from session_id
    stream_id_bytes = hashlib.sha256(session_id.encode("utf-8")).digest()[:16]
    stream_id_hex = stream_id_bytes.hex()

    # Split into chunks
    chunks = []
    offset = 0
    seq = 0
    while offset < len(content_bytes):
        chunk = content_bytes[offset:offset + MAX_BINARY_CHUNK]
        frame = encode_binary_frame(stream_id_bytes, seq, chunk)
        chunks.append(frame)
        offset += MAX_BINARY_CHUNK
        seq += 1

    # Empty observation: just bytes.begin, no frames
    begin_record = encode_bytes_begin(stream_id_hex, len(content_bytes), MAX_BINARY_CHUNK)
    return [begin_record] + chunks


def main():
    session_id = "unknown"
    sequence = 0
    record_count = 0
    dropped_events = []
    binary_chunks = []

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
            continue

        # Track session ID — OMP session event uses "id" field
        if event.get("type") == "session":
            session_id = event.get("id", "unknown")

        # Map event
        record = map_event(event, session_id, sequence)
        sequence += 1

        if record is None:
            dropped_events.append(event.get("type", "unknown"))
            continue

        if record_count >= MAX_RECORDS_PER_SESSION:
            print(f"ERROR: session exceeds {MAX_RECORDS_PER_SESSION} records", file=sys.stderr)
            break

        try:
            encoded = validate_record(record)
        except ValueError as e:
            print(f"ERROR: bounds violation: {e}", file=sys.stderr)
            continue

        sys.stdout.buffer.write(encoded + b"\n")
        record_count += 1

    # Summary
    print(f"\n# Adapter summary:", file=sys.stderr)
    print(f"#   session_id: {session_id}", file=sys.stderr)
    print(f"#   records emitted: {record_count}", file=sys.stderr)
    print(f"#   events dropped: {len(dropped_events)}", file=sys.stderr)
    if dropped_events:
        from collections import Counter
        for etype, count in Counter(dropped_events).items():
            print(f"#     {etype}: {count}", file=sys.stderr)


if __name__ == "__main__":
    main()
