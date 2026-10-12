#!/usr/bin/env python3
"""Conformance check for canonical-wire-profile-20261003.md against shared-fixture-trace-20261003.md.

Validates:
1. Control record framing (UTF-8 JSON + LF), including receiver-level rules:
   EOF-before-LF is incomplete (not parsed), a malformed record is consumed
   through its LF with exactly one error and no suffix reinterpretation, and
   MAX_RECORDS_PER_SESSION is enforced behaviorally
2. Rejection rules (BOM, CR, duplicate keys at any depth, invalid UTF-8,
   invalid JSON, non-object top level, fraction/exponent integer tokens, ...)
3. Size/depth/int64 limits, including boundary acceptance at both int64 ends
4. Binary frame layout (stream_id[16] || sequence[4] || payload_length[2] || payload)
5. Deterministic replay (filter transformation on identical inputs)

Exit 0 = all checks pass. Exit 1 = at least one failure.
"""

import json
import struct
import sys
from pathlib import Path

# --- Constants from canonical-wire-profile-20261003.md ---

MAX_CONTROL_BYTES = 65536  # excluding LF
MAX_DEPTH = 16
MAX_MEMBERS = 256
MAX_ARRAY_ELEMENTS = 256
MAX_STRING_BYTES = 16 * 1024  # 16 KiB
MAX_RECORDS_PER_SESSION = 1024
MAX_OBSERVATION_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_CHUNK_BYTES = 16 * 1024  # 16 KiB

# Binary frame header: stream_id[16] || sequence[4] || payload_length[2]
FRAME_HEADER = struct.Struct(">16sIH")  # 16 + 4 + 2 = 22 bytes
assert FRAME_HEADER.size == 22

# --- Control record validation ---

def validate_control_record(record_bytes: bytes, label: str) -> dict:
    """Validate one control record per canonical-wire-profile. Returns parsed object.

    Raises ConformanceError on any violation.
    """
    errors = []

    # Check for BOM
    if record_bytes.startswith(b'\xef\xbb\xbf'):
        errors.append("UTF-8 BOM detected")

    # Check for raw CR anywhere
    if b'\x0d' in record_bytes:
        errors.append("raw CR (0x0d) detected")

    # Check byte length (excluding LF)
    if len(record_bytes) > MAX_CONTROL_BYTES:
        errors.append(f"record exceeds {MAX_CONTROL_BYTES} bytes: {len(record_bytes)}")

    # Decode strict UTF-8
    try:
        text = record_bytes.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ConformanceError(f"{label}: invalid UTF-8: {e}")

    # Parse JSON
    # Parse JSON with duplicate key detection
    def _no_duplicates(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError(f"duplicate key '{k}'")
            d[k] = v
        return d

    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError as e:
        raise ConformanceError(f"{label}: invalid JSON: {e}")
    except ValueError as e:
        raise ConformanceError(f"{label}: {e}")

    # Profile: a control record is exactly one JSON object. A top-level array,
    # string, number, boolean or null is not a control record.
    if not isinstance(obj, dict):
        raise ConformanceError(
            f"{label}: control record must be a JSON object, got {type(obj).__name__}")

    # Check integer ranges and types
    def check_types(o, depth=0, path=""):
        if depth > MAX_DEPTH:
            errors.append(f"exceeds max depth {MAX_DEPTH} at {path}")
            return
        if isinstance(o, dict):
            if len(o) > MAX_MEMBERS:
                errors.append(f"exceeds max members {MAX_MEMBERS} at {path}")
            for k, v in o.items():
                check_types(v, depth + 1, f"{path}.{k}")
        elif isinstance(o, list):
            if len(o) > MAX_ARRAY_ELEMENTS:
                errors.append(f"exceeds max array elements {MAX_ARRAY_ELEMENTS} at {path}")
            for i, v in enumerate(o):
                check_types(v, depth + 1, f"{path}[{i}]")
        elif isinstance(o, str):
            try:
                encoded_len = len(o.encode('utf-8'))
            except UnicodeEncodeError:
                errors.append(f"unpaired surrogate in string at {path}")
                return
            if encoded_len > MAX_STRING_BYTES:
                errors.append(f"exceeds max string bytes {MAX_STRING_BYTES} at {path}")
        elif isinstance(o, bool):
            pass  # valid
        elif isinstance(o, int):
            if o < -(2**63) or o > 2**63 - 1:
                errors.append(f"integer out of signed 64-bit range at {path}: {o}")
        elif o is None:
            pass
        else:
            errors.append(f"invalid type {type(o).__name__} at {path}")

    check_types(obj, depth=1)

    if errors:
        raise ConformanceError(f"{label}: {'; '.join(errors)}")

    return obj


def validate_a3_schema(obj: dict, label: str) -> None:
    """Validate A3 adapter output schema per canonical wire profile.

    Required fields: type, operation_id, writer_id, capability, request, timestamp
    Record type: intent or outcome
    For outcome records: status must be completed/failed/unsupported/indeterminate
    """
    errors = []

    required_fields = {'type', 'operation_id', 'writer_id', 'capability', 'request', 'timestamp'}
    missing = required_fields - set(obj.keys())
    if missing:
        errors.append(f"missing required fields: {sorted(missing)}")

    record_type = obj.get('type')
    if record_type not in ('intent', 'outcome'):
        errors.append(f"invalid type: {record_type!r} (must be 'intent' or 'outcome')")

    if 'operation_id' in obj and (not isinstance(obj['operation_id'], str) or not obj['operation_id']):
        errors.append("operation_id must be non-empty string")
    if 'writer_id' in obj and (not isinstance(obj['writer_id'], str) or not obj['writer_id']):
        errors.append("writer_id must be non-empty string")
    if 'timestamp' in obj and not isinstance(obj['timestamp'], str):
        errors.append("timestamp must be string")

    if record_type == 'outcome':
        status = obj.get('status')
        valid_statuses = {'completed', 'failed', 'unsupported', 'indeterminate'}
        if status not in valid_statuses:
            errors.append(f"invalid status: {status!r} (must be one of {sorted(valid_statuses)})")

    if errors:
        raise ConformanceError(f"{label}: {'; '.join(errors)}")

class ConformanceError(Exception):
    pass


# --- Receiver framing (byte-stream level) ---

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def read_control_records(data: bytes, label: str = "session") -> tuple[list[dict], list[dict]]:
    """Split a control-record byte stream per canonical-wire-profile.

    Returns (records, errors). Each error is a dict with keys
    ``kind`` (``record`` | ``incomplete`` | ``session``), ``offset``
    (byte offset of the record's LF, or of the unterminated tail) and
    ``message``.

    Framing rules implemented (profile, "Control record bytes"):
      * A record is exactly one JSON object followed by one LF. Bytes after
        the last LF with no terminating LF are an INCOMPLETE record: not
        parsed, exactly one error, and never combined with the next read.
      * A malformed record is consumed through its LF and yields exactly one
        record error. Suffix bytes are never reinterpreted as an extra record
        or an extra error: splitting happens only on LF, never mid-record.
      * A bare LF is a zero-byte record, which is not a JSON object, so it is
        a record error. The profile states no empty-line exemption; the replay
        filter (``apply_filter``) is a transformation, not this receiver.
      * At most MAX_RECORDS_PER_SESSION records per session: the (N+1)-th
        received record is refused with a session error. A malformed record
        was still received, so it counts against the session budget.
    """
    records: list[dict] = []
    errors: list[dict] = []
    received = 0
    offset = 0
    total = len(data)
    while offset < total:
        lf = data.find(b'\x0a', offset)
        if lf == -1:
            errors.append({
                "kind": "incomplete", "offset": offset,
                "message": f"{label}: EOF before LF at offset {offset} "
                           f"({total - offset} byte(s) retained, not parsed)",
            })
            break
        segment = data[offset:lf]
        received += 1
        if received > MAX_RECORDS_PER_SESSION:
            errors.append({
                "kind": "session", "offset": lf,
                "message": f"{label}: record {received} exceeds "
                           f"MAX_RECORDS_PER_SESSION={MAX_RECORDS_PER_SESSION}",
            })
            offset = lf + 1
            continue
        try:
            records.append(validate_control_record(segment, f"{label}#{received}"))
        except ConformanceError as e:
            errors.append({"kind": "record", "offset": lf, "message": str(e)})
        offset = lf + 1
    return records, errors


# --- Binary frame validation ---

def validate_binary_frame(frame_bytes: bytes, stream_id: str, expected_sequence: int,
                          expected_payload: bytes, label: str) -> None:
    """Validate one binary frame per canonical-wire-profile.

    Layout: stream_id[16] || sequence[4] || payload_length[2] || payload[payload_length]
    """
    errors = []

    if len(frame_bytes) < FRAME_HEADER.size:
        raise ConformanceError(f"{label}: frame too short ({len(frame_bytes)} < {FRAME_HEADER.size})")

    sid, seq, plen = FRAME_HEADER.unpack_from(frame_bytes, 0)

    # Stream ID check
    expected_sid = bytes.fromhex(stream_id)
    if sid != expected_sid:
        errors.append(f"stream_id mismatch: got {sid.hex()}, expected {expected_sid.hex()}")

    # Sequence check
    if seq != expected_sequence:
        errors.append(f"sequence mismatch: got {seq}, expected {expected_sequence}")

    # Payload length check
    actual_payload = frame_bytes[FRAME_HEADER.size:]
    if plen != len(actual_payload):
        errors.append(f"payload_length mismatch: header says {plen}, actual {len(actual_payload)}")

    # Content check
    if actual_payload != expected_payload:
        errors.append(f"payload mismatch: got {actual_payload.hex()}, expected {expected_payload.hex()}")

    if errors:
        raise ConformanceError(f"{label}: {'; '.join(errors)}")


# --- Deterministic replay (filter) ---

def apply_filter(data: bytes) -> bytes:
    """Remove empty LF-terminated records, preserve all other octets and ordering.

    An empty record is a bare LF (0x0a) — i.e., an empty line.
    """
    lines = data.split(b'\n')
    # Reconstruct: remove empty lines, keep non-empty lines with their LF
    result = b''
    for i, line in enumerate(lines):
        if i < len(lines) - 1:
            # Not the last element; this line was terminated by LF
            if line:  # non-empty line
                result += line + b'\n'
            # else: empty line — skip (remove)
        else:
            # Last element: no trailing LF in original split
            if line:
                result += line
    return result


# --- Wire examples from shared-fixture-trace-20261003.md ---

def build_wire_examples() -> list[tuple[str, bytes]]:
    """Build the control records from the shared fixture trace."""
    examples = []

    # Step 1: capability list request
    examples.append(("r1-cap.list", b'{"v":1,"session":"s7","id":"r1","kind":"cap.list"}\n'))

    # Step 2: capability list response
    examples.append(("r1-result", b'{"v":1,"session":"s7","id":"r1","kind":"result","status":"ok","caps":[{"name":"fs.read","version":1},{"name":"mesh.send","version":1}]}\n'))

    # Step 3: read request
    examples.append(("r2-read", b'{"v":1,"session":"s7","id":"r2","kind":"cap.call","op":"o9","cap":"fs.read","args":{"root":"fixture","path":"sample.txt","max":4096}}\n'))

    # Step 4: bytes.begin for read stream
    examples.append(("r2-bytes.begin", b'{"v":1,"session":"s7","id":"r2","kind":"bytes.begin","op":"o9","stream":"00000000000000000000000000000001","total_length":12,"chunk_max":16384}\n'))

    # Step 5: result for read
    examples.append(("r2-result", b'{"v":1,"session":"s7","id":"r2","kind":"result","status":"ok","op":"o9","value":{"bytes":"b17","length":12}}\n'))

    # Step 7-8: send request
    examples.append(("r3-send", b'{"v":1,"session":"s7","id":"r3","kind":"cap.call","op":"o10","cap":"mesh.send","args":{"to":"peer-a","bytes":"b18"}}\n'))

    # Step 8: bytes.begin for send stream
    examples.append(("r3-bytes.begin", b'{"v":1,"session":"s7","id":"r3","kind":"bytes.begin","op":"o10","stream":"00000000000000000000000000000002","total_length":11,"chunk_max":16384}\n'))

    # Step 9: status query
    examples.append(("r4-op.status", b'{"v":1,"session":"s7","id":"r4","kind":"op.status","op":"o10"}\n'))

    # Step 10: status result (completed)
    examples.append(("r4-result-completed", b'{"v":1,"session":"s7","id":"r4","kind":"result","status":"completed","op":"o10","host_op":"h55"}\n'))

    return examples


def build_binary_frames() -> list[tuple[str, str, int, bytes]]:
    """Build the binary frames from the shared fixture trace."""
    frames = []

    # Read frame: stream 1, sequence 0, payload = "alpha\n\nbeta\n" (12 bytes)
    read_payload = b'alpha\n\nbeta\n'
    read_frame = (
        bytes.fromhex('00000000000000000000000000000001') +
        struct.pack('>I', 0) +
        struct.pack('>H', len(read_payload)) +
        read_payload
    )
    frames.append(("read-frame-1", "00000000000000000000000000000001", 0, read_payload))

    # Send frame: stream 2, sequence 0, payload = "alpha\nbeta\n" (11 bytes)
    send_payload = b'alpha\nbeta\n'
    send_frame = (
        bytes.fromhex('00000000000000000000000000000002') +
        struct.pack('>I', 0) +
        struct.pack('>H', len(send_payload)) +
        send_payload
    )
    frames.append(("send-frame-1", "00000000000000000000000000000002", 0, send_payload))

    return frames

# --- Transfer-level validation ---

def validate_bytes_begin(obj: dict, label: str) -> None:
    """Validate a bytes.begin control record per canonical-wire-profile.

    Checks: stream ID is 32 lowercase hex digits, chunk_max in 1..=16384,
    total_length <= 1 MiB.
    """
    errors = []

    stream = obj.get("stream")
    if not isinstance(stream, str):
        errors.append(f"{label}: stream must be a string, got {type(stream).__name__}")
    else:
        if len(stream) != 32:
            errors.append(f"{label}: stream ID must be 32 hex digits, got {len(stream)}")
        if not all(c in '0123456789abcdef' for c in stream):
            errors.append(f"{label}: stream ID must be lowercase hex, got '{stream}'")

    chunk_max = obj.get("chunk_max")
    if not isinstance(chunk_max, int):
        errors.append(f"{label}: chunk_max must be an integer, got {type(chunk_max).__name__}")
    elif chunk_max < 1 or chunk_max > 16384:
        errors.append(f"{label}: chunk_max must be in 1..=16384, got {chunk_max}")

    total_length = obj.get("total_length")
    if not isinstance(total_length, int):
        errors.append(f"{label}: total_length must be an integer, got {type(total_length).__name__}")
    elif total_length < 0:
        errors.append(f"{label}: total_length must be non-negative, got {total_length}")
    elif total_length > MAX_OBSERVATION_BYTES:
        errors.append(f"{label}: total_length exceeds {MAX_OBSERVATION_BYTES} bytes: {total_length}")

    if errors:
        raise ConformanceError(f"{label}: {'; '.join(errors)}")


def validate_transfer(frames: list[tuple[int, bytes]], total_length: int,
                      chunk_max: int, label: str) -> None:
    """Validate a sequence of binary frames for transfer completeness.

    Checks: contiguous sequence starting at 0, no duplicates, no gaps,
    zero-length chunks rejected, non-final chunks equal chunk_max,
    final chunk may be shorter, total bytes match total_length,
    total bytes <= 1 MiB, each chunk <= 16 KiB.
    """
    errors = []

    if total_length > MAX_OBSERVATION_BYTES:
        errors.append(f"{label}: total_length exceeds {MAX_OBSERVATION_BYTES} bytes: {total_length}")

    if not frames:
        if total_length == 0:
            return  # empty observation is valid
        errors.append(f"{label}: no frames but total_length={total_length}")
        raise ConformanceError(f"{label}: {'; '.join(errors)}")

    # Check chunk size cap
    for seq, payload in frames:
        if len(payload) > MAX_CHUNK_BYTES:
            errors.append(f"{label}: chunk {seq} exceeds {MAX_CHUNK_BYTES} bytes: {len(payload)}")

    # Check contiguity: sequences must be 0, 1, 2, ..., n-1
    sequences = [seq for seq, _ in frames]
    expected = list(range(len(frames)))
    if sequences != expected:
        errors.append(f"{label}: non-contiguous sequence: got {sequences}, expected {expected}")

    # Check for duplicates (already covered by contiguity, but explicit)
    if len(set(sequences)) != len(sequences):
        errors.append(f"{label}: duplicate sequence numbers")

    # Check zero-length chunks
    for seq, payload in frames:
        if len(payload) == 0:
            errors.append(f"{label}: zero-length chunk at sequence {seq}")

    # Check chunk_max compliance: non-final chunks must equal chunk_max
    for i, (seq, payload) in enumerate(frames[:-1]):
        if len(payload) != chunk_max:
            errors.append(f"{label}: non-final chunk {seq} has {len(payload)} bytes, expected chunk_max={chunk_max}")

    # Check total bytes match
    actual_total = sum(len(payload) for _, payload in frames)
    if actual_total != total_length:
        errors.append(f"{label}: total bytes mismatch: frames sum to {actual_total}, declared {total_length}")

    if errors:
        raise ConformanceError(f"{label}: {'; '.join(errors)}")


# --- Main conformance run ---

def main() -> int:
    passed = 0
    failed = 0
    errors = []

    print("=" * 60)
    print("Canonical Wire Profile Conformance Check")
    print("Profile: canonical-wire-profile-20261003.md")
    print("Fixture: shared-fixture-trace-20261003.md")
    print("=" * 60)

    # 1. Validate all control records
    print("\n--- Control Record Validation ---")
    for label, record_bytes in build_wire_examples():
        try:
            obj = validate_control_record(record_bytes[:-1], label)  # strip LF for validation
            print(f"  PASS {label}: {record_bytes.decode('utf-8').strip()}")
            passed += 1
        except ConformanceError as e:
            print(f"  FAIL {label}: {e}")
            failed += 1
            errors.append(str(e))

    # 1b. A3 adapter schema validation
    print("\n--- A3 Adapter Schema Validation ---")

    # Valid intent record
    valid_intent = {
        "type": "intent",
        "operation_id": "op-00000001",
        "capability": "fs.write",
        "request": {"path": "/tmp/test.txt", "content": "hello"},
        "writer_id": "body-research",
        "host_id": "local",
        "timestamp": "2026-10-10T20:00:00Z"
    }
    try:
        validate_a3_schema(valid_intent, "valid-intent")
        print("  PASS valid-intent: all required fields present")
        passed += 1
    except ConformanceError as e:
        print(f"  FAIL valid-intent: {e}")
        failed += 1
        errors.append(str(e))

    # Valid outcome record
    valid_outcome = {
        "type": "outcome",
        "operation_id": "op-00000001",
        "capability": "fs.write",
        "request": {"path": "/tmp/test.txt", "content": "hello"},
        "writer_id": "body-research",
        "host_id": "local",
        "timestamp": "2026-10-10T20:00:00Z",
        "status": "completed"
    }
    try:
        validate_a3_schema(valid_outcome, "valid-outcome")
        print("  PASS valid-outcome: all required fields + valid status")
        passed += 1
    except ConformanceError as e:
        print(f"  FAIL valid-outcome: {e}")
        failed += 1
        errors.append(str(e))

    # Invalid: missing required field
    invalid_missing = {
        "type": "intent",
        "operation_id": "op-00000001",
        "capability": "fs.write",
        "request": {"path": "/tmp/test.txt"},
        "writer_id": "body-research"
        # missing timestamp
    }
    try:
        validate_a3_schema(invalid_missing, "invalid-missing-field")
        print("  FAIL invalid-missing-field: should have rejected missing timestamp")
        failed += 1
        errors.append("missing field not rejected")
    except ConformanceError:
        print("  PASS invalid-missing-field: missing timestamp rejected")
        passed += 1

    # Invalid: wrong record type
    invalid_type = {
        "type": "start",
        "operation_id": "op-00000001",
        "capability": "fs.write",
        "request": {"path": "/tmp/test.txt"},
        "writer_id": "body-research",
        "host_id": "local",
        "timestamp": "2026-10-10T20:00:00Z"
    }
    try:
        validate_a3_schema(invalid_type, "invalid-type")
        print("  FAIL invalid-type: should have rejected type='start'")
        failed += 1
        errors.append("invalid type not rejected")
    except ConformanceError:
        print("  PASS invalid-type: type='start' rejected")
        passed += 1

    # Invalid: wrong status value
    invalid_status = {
        "type": "outcome",
        "operation_id": "op-00000001",
        "capability": "fs.write",
        "request": {"path": "/tmp/test.txt"},
        "writer_id": "body-research",
        "host_id": "local",
        "timestamp": "2026-10-10T20:00:00Z",
        "status": "ok"
    }
    try:
        validate_a3_schema(invalid_status, "invalid-status")
        print("  FAIL invalid-status: should have rejected status='ok'")
        failed += 1
        errors.append("invalid status not rejected")
    except ConformanceError:
        print("  PASS invalid-status: status='ok' rejected")
        passed += 1

    # Invalid: empty operation_id
    invalid_opid = {
        "type": "intent",
        "operation_id": "",
        "capability": "fs.write",
        "request": {"path": "/tmp/test.txt"},
        "writer_id": "body-research",
        "host_id": "local",
        "timestamp": "2026-10-10T20:00:00Z"
    }
    try:
        validate_a3_schema(invalid_opid, "invalid-empty-opid")
        print("  FAIL invalid-empty-opid: should have rejected empty operation_id")
        failed += 1
        errors.append("empty operation_id not rejected")
    except ConformanceError:
        print("  PASS invalid-empty-opid: empty operation_id rejected")
        passed += 1

    # 2. Validate binary frames
    print("\n--- Binary Frame Validation ---")
    for label, stream_id, seq, payload in build_binary_frames():
        # Reconstruct the full frame bytes
        frame_bytes = (
            bytes.fromhex(stream_id) +
            struct.pack('>I', seq) +
            struct.pack('>H', len(payload)) +
            payload
        )
        try:
            validate_binary_frame(frame_bytes, stream_id, seq, payload, label)
            print(f"  PASS {label}: stream={stream_id}, seq={seq}, payload_len={len(payload)}")
            passed += 1
        except ConformanceError as e:
            print(f"  FAIL {label}: {e}")
            failed += 1
            errors.append(str(e))

    # 3. Deterministic replay: filter transformation
    print("\n--- Deterministic Replay (Filter) ---")
    fixture_input = b'alpha\n\nbeta\n'
    expected_output = b'alpha\nbeta\n'
    actual_output = apply_filter(fixture_input)
    if actual_output == expected_output:
        print(f"  PASS filter: {len(fixture_input)} bytes → {len(actual_output)} bytes")
        print(f"    input:  {fixture_input.hex()}")
        print(f"    output: {actual_output.hex()}")
        passed += 1
    else:
        print(f"  FAIL filter: expected {expected_output.hex()}, got {actual_output.hex()}")
        failed += 1
        errors.append("filter mismatch")

    # 4. Rejection rule tests (negative tests)
    print("\n--- Rejection Rule Tests ---")

    # 4a. BOM rejection
    try:
        validate_control_record(b'\xef\xbb\xbf{"v":1}', "bom-test")
        print("  FAIL bom-test: should have rejected BOM")
        failed += 1
        errors.append("BOM not rejected")
    except ConformanceError:
        print("  PASS bom-test: BOM rejected")
        passed += 1

    # 4b. Raw CR rejection
    try:
        validate_control_record(b'{"v":1,"x":"a\rb"}', "cr-test")
        print("  FAIL cr-test: should have rejected raw CR")
        failed += 1
        errors.append("raw CR not rejected")
    except ConformanceError:
        print("  PASS cr-test: raw CR rejected")
        passed += 1

    # 4c. Duplicate key rejection
    try:
        validate_control_record(b'{"v":1,"v":2}', "dup-key-test")
        print("  FAIL dup-key-test: should have rejected duplicate key")
        failed += 1
        errors.append("duplicate key not rejected")
    except ConformanceError:
        print("  PASS dup-key-test: duplicate key rejected")
        passed += 1

    # 4d. Invalid UTF-8 rejection
    try:
        validate_control_record(b'{"v":1,"x":"\xff\xfe"}', "bad-utf8-test")
        print("  FAIL bad-utf8-test: should have rejected invalid UTF-8")
        failed += 1
        errors.append("invalid UTF-8 not rejected")
    except ConformanceError:
        print("  PASS bad-utf8-test: invalid UTF-8 rejected")
        passed += 1

    # 4e. Integer out of range rejection
    try:
        validate_control_record(b'{"v":99999999999999999999999999}', "big-int-test")
        print("  FAIL big-int-test: should have rejected out-of-range integer")
        failed += 1
        errors.append("out-of-range integer not rejected")
    except ConformanceError:
        print("  PASS big-int-test: out-of-range integer rejected")
        passed += 1

    # 4f. Oversize record rejection
    try:
        validate_control_record(b'{"v":"' + b'x' * (MAX_CONTROL_BYTES + 1) + b'"}', "oversize-test")
        print("  FAIL oversize-test: should have rejected oversize record")
        failed += 1
        errors.append("oversize record not rejected")
    except ConformanceError:
        print("  PASS oversize-test: oversize record rejected")
        passed += 1

    # 4g. Depth limit rejection
    deep_obj = b'{"a":' * (MAX_DEPTH + 1) + b'1' + b'}' * (MAX_DEPTH + 1)
    try:
        validate_control_record(deep_obj, "depth-test")
        print("  FAIL depth-test: should have rejected excessive depth")
        failed += 1
        errors.append("excessive depth not rejected")
    except ConformanceError:
        print("  PASS depth-test: excessive depth rejected")
        passed += 1

    # 4h. Member limit rejection (257 members)
    member_obj = b'{' + b','.join(b'"k%d":1' % i for i in range(MAX_MEMBERS + 1)) + b'}'
    try:
        validate_control_record(member_obj, "member-limit-test")
        print("  FAIL member-limit-test: should have rejected 257 members")
        failed += 1
        errors.append("member limit not rejected")
    except ConformanceError:
        print("  PASS member-limit-test: 257 members rejected")
        passed += 1

    # 4i. Array limit rejection (257 elements)
    array_obj = b'{"v":[' + b','.join(b'1' for _ in range(MAX_ARRAY_ELEMENTS + 1)) + b']}'
    try:
        validate_control_record(array_obj, "array-limit-test")
        print("  FAIL array-limit-test: should have rejected 257 elements")
        failed += 1
        errors.append("array limit not rejected")
    except ConformanceError:
        print("  PASS array-limit-test: 257 elements rejected")
        passed += 1

    # 4j. String limit rejection (16 KiB + 1)
    string_obj = b'{"v":"' + b'x' * (MAX_STRING_BYTES + 1) + b'"}'
    try:
        validate_control_record(string_obj, "string-limit-test")
        print("  FAIL string-limit-test: should have rejected 16 KiB+1 string")
        failed += 1
        errors.append("string limit not rejected")
    except ConformanceError:
        print("  PASS string-limit-test: 16 KiB+1 string rejected")
        passed += 1

    # 4k. Non-finite rejection (NaN)
    try:
        validate_control_record(b'{"v":NaN}', "nan-test")
        print("  FAIL nan-test: should have rejected NaN")
        failed += 1
        errors.append("NaN not rejected")
    except ConformanceError:
        print("  PASS nan-test: NaN rejected")
        passed += 1

    # 4l. Unpaired surrogate rejection
    try:
        validate_control_record(b'{"v":"\\ud800"}', "surrogate-test")
        print("  FAIL surrogate-test: should have rejected unpaired surrogate")
        failed += 1
        errors.append("unpaired surrogate not rejected")
    except ConformanceError:
        print("  PASS surrogate-test: unpaired surrogate rejected")
        passed += 1

    # 4m. Depth 17 empty object rejection (witness probe case)
    deep_empty = b'{"a":' * (MAX_DEPTH + 1) + b'{}' + b'}' * (MAX_DEPTH + 1)
    try:
        validate_control_record(deep_empty, "depth-empty-test")
        print("  FAIL depth-empty-test: should have rejected depth-17 empty object")
        failed += 1
        errors.append("depth-17 empty object not rejected")
    except ConformanceError:
        print("  PASS depth-empty-test: depth-17 empty object rejected")
        passed += 1

    # 5. Empty observation (no binary frames, completes after bytes.begin)
    print("\n--- Empty Observation ---")
    empty_begin = {"v": 1, "session": "s7", "id": "r5", "kind": "bytes.begin",
                   "op": "o11", "stream": "00000000000000000000000000000003",
                   "total_length": 0, "chunk_max": 16384}
    try:
        validate_bytes_begin(empty_begin, "empty-observation")
        validate_transfer([], 0, 16384, "empty-observation")
        print("  PASS empty-observation: total_length=0, no frames needed")
        passed += 1
    except ConformanceError as e:
        print(f"  FAIL empty-observation: {e}")
        failed += 1
        errors.append(str(e))

    # 6. bytes.begin validation tests
    print("\n--- bytes.begin Validation ---")

    # 6a. Valid bytes.begin
    valid_begin = {"v": 1, "session": "s7", "id": "r2", "kind": "bytes.begin",
                   "op": "o9", "stream": "00000000000000000000000000000001",
                   "total_length": 12, "chunk_max": 16384}
    try:
        validate_bytes_begin(valid_begin, "valid-bytes-begin")
        print("  PASS valid-bytes-begin: valid bytes.begin accepted")
        passed += 1
    except ConformanceError as e:
        print(f"  FAIL valid-bytes-begin: {e}")
        failed += 1
        errors.append(str(e))

    # 6b. Stream ID too short
    short_sid = {"stream": "0000000000000000000000000000001", "total_length": 12, "chunk_max": 16384}
    try:
        validate_bytes_begin(short_sid, "short-sid-test")
        print("  FAIL short-sid-test: should have rejected 31-char stream ID")
        failed += 1
        errors.append("short stream ID not rejected")
    except ConformanceError:
        print("  PASS short-sid-test: 31-char stream ID rejected")
        passed += 1

    # 6c. Stream ID uppercase hex
    upper_sid = {"stream": "0000000000000000000000000000000A", "total_length": 12, "chunk_max": 16384}
    try:
        validate_bytes_begin(upper_sid, "upper-sid-test")
        print("  FAIL upper-sid-test: should have rejected uppercase hex")
        failed += 1
        errors.append("uppercase hex stream ID not rejected")
    except ConformanceError:
        print("  PASS upper-sid-test: uppercase hex stream ID rejected")
        passed += 1

    # 6d. chunk_max = 0
    zero_chunk = {"stream": "00000000000000000000000000000001", "total_length": 12, "chunk_max": 0}
    try:
        validate_bytes_begin(zero_chunk, "zero-chunk-test")
        print("  FAIL zero-chunk-test: should have rejected chunk_max=0")
        failed += 1
        errors.append("chunk_max=0 not rejected")
    except ConformanceError:
        print("  PASS zero-chunk-test: chunk_max=0 rejected")
        passed += 1

    # 6e. chunk_max = 16385
    big_chunk = {"stream": "00000000000000000000000000000001", "total_length": 12, "chunk_max": 16385}
    try:
        validate_bytes_begin(big_chunk, "big-chunk-test")
        print("  FAIL big-chunk-test: should have rejected chunk_max=16385")
        failed += 1
        errors.append("chunk_max=16385 not rejected")
    except ConformanceError:
        print("  PASS big-chunk-test: chunk_max=16385 rejected")
        passed += 1

    # 6f. total_length > 1 MiB
    big_total = {"stream": "00000000000000000000000000000001",
                 "total_length": MAX_OBSERVATION_BYTES + 1, "chunk_max": 16384}
    try:
        validate_bytes_begin(big_total, "big-total-test")
        print("  FAIL big-total-test: should have rejected total_length > 1 MiB")
        failed += 1
        errors.append("total_length > 1 MiB not rejected")
    except ConformanceError:
        print("  PASS big-total-test: total_length > 1 MiB rejected")
        passed += 1

    # 7. Transfer-level semantics tests
    print("\n--- Transfer-Level Semantics ---")

    # 7a. Valid single-chunk transfer
    try:
        validate_transfer([(0, b"hello")], 5, 16384, "valid-transfer")
        print("  PASS valid-transfer: single chunk accepted")
        passed += 1
    except ConformanceError as e:
        print(f"  FAIL valid-transfer: {e}")
        failed += 1
        errors.append(str(e))

    # 7b. Non-contiguous sequence (gap)
    try:
        validate_transfer([(0, b"hello"), (2, b"world")], 10, 16384, "gap-test")
        print("  FAIL gap-test: should have rejected non-contiguous sequence")
        failed += 1
        errors.append("non-contiguous sequence not rejected")
    except ConformanceError:
        print("  PASS gap-test: non-contiguous sequence rejected")
        passed += 1

    # 7c. Duplicate sequence
    try:
        validate_transfer([(0, b"hello"), (0, b"world")], 10, 16384, "dup-seq-test")
        print("  FAIL dup-seq-test: should have rejected duplicate sequence")
        failed += 1
        errors.append("duplicate sequence not rejected")
    except ConformanceError:
        print("  PASS dup-seq-test: duplicate sequence rejected")
        passed += 1

    # 7d. Zero-length chunk
    try:
        validate_transfer([(0, b"")], 0, 16384, "zero-chunk-transfer-test")
        print("  FAIL zero-chunk-transfer-test: should have rejected zero-length chunk")
        failed += 1
        errors.append("zero-length chunk not rejected")
    except ConformanceError:
        print("  PASS zero-chunk-transfer-test: zero-length chunk rejected")
        passed += 1

    # 7e. Total bytes mismatch
    try:
        validate_transfer([(0, b"hello")], 10, 16384, "total-mismatch-test")
        print("  FAIL total-mismatch-test: should have rejected total bytes mismatch")
        failed += 1
        errors.append("total bytes mismatch not rejected")
    except ConformanceError:
        print("  PASS total-mismatch-test: total bytes mismatch rejected")
        passed += 1

    # 7f. Non-final chunk not equal to chunk_max
    try:
        validate_transfer([(0, b"hi"), (1, b"world")], 7, 5, "chunk-max-test")
        print("  FAIL chunk-max-test: should have rejected non-final chunk != chunk_max")
        failed += 1
        errors.append("non-final chunk != chunk_max not rejected")
    except ConformanceError:
        print("  PASS chunk-max-test: non-final chunk != chunk_max rejected")
        passed += 1

    # 7g. Chunk exceeds 16 KiB cap
    try:
        validate_transfer([(0, b"x" * (MAX_CHUNK_BYTES + 1))], MAX_CHUNK_BYTES + 1, 16384, "chunk-cap-test")
        print("  FAIL chunk-cap-test: should have rejected chunk > 16 KiB")
        failed += 1
        errors.append("chunk > 16 KiB not rejected")
    except ConformanceError:
        print("  PASS chunk-cap-test: chunk > 16 KiB rejected")
        passed += 1

    # 7h. No frames but total_length > 0
    try:
        validate_transfer([], 10, 16384, "no-frames-test")
        print("  FAIL no-frames-test: should have rejected no frames with total_length > 0")
        failed += 1
        errors.append("no frames with total_length > 0 not rejected")
    except ConformanceError:
        print("  PASS no-frames-test: no frames with total_length > 0 rejected")
        passed += 1

    # 8. Session cap test
    print("\n--- Session Cap ---")
    # The profile caps control records at 1,024 per session.
    # We verify the constant is defined and the limit is correct.
    if MAX_RECORDS_PER_SESSION == 1024:
        print(f"  PASS session-cap: MAX_RECORDS_PER_SESSION = {MAX_RECORDS_PER_SESSION}")
        passed += 1
    else:
        print(f"  FAIL session-cap: MAX_RECORDS_PER_SESSION = {MAX_RECORDS_PER_SESSION}, expected 1024")
        failed += 1
        errors.append("session cap constant incorrect")

    # 9. Fixture hex parsing (independent verification)
    print("\n--- Fixture Hex Parsing ---")
    # Parse the fixture trace hex literals directly and compare against validator output
    fixture_read_hex = "00000000000000000000000000000001" + "00000000" + "000c" + "616c7068610a0a626574610a"
    fixture_read_bytes = bytes.fromhex(fixture_read_hex)
    try:
        validate_binary_frame(fixture_read_bytes, "00000000000000000000000000000001", 0,
                              b'alpha\n\nbeta\n', "fixture-read-hex")
        print("  PASS fixture-read-hex: parsed fixture hex matches validator")
        passed += 1
    except ConformanceError as e:
        print(f"  FAIL fixture-read-hex: {e}")
        failed += 1
        errors.append(str(e))

    fixture_send_hex = "00000000000000000000000000000002" + "00000000" + "000b" + "616c7068610a626574610a"
    fixture_send_bytes = bytes.fromhex(fixture_send_hex)
    try:
        validate_binary_frame(fixture_send_bytes, "00000000000000000000000000000002", 0,
                              b'alpha\nbeta\n', "fixture-send-hex")
        print("  PASS fixture-send-hex: parsed fixture hex matches validator")
        passed += 1
    except ConformanceError as e:
        print(f"  FAIL fixture-send-hex: {e}")
        failed += 1
        errors.append(str(e))
    # 10. Receiver framing (byte-stream level) tests
    print("\n--- Receiver Framing ---")

    # 10a. Non-object top level is not a control record
    try:
        validate_control_record(b'[1,2]', "nonobject-test")
        print("  FAIL nonobject-test: should have rejected top-level array")
        failed += 1
        errors.append("top-level array not rejected")
    except ConformanceError:
        print("  PASS nonobject-test: top-level array rejected")
        passed += 1

    # 10b. EOF before LF is incomplete: not parsed, exactly one error
    recs, errs = read_control_records(b'{"v":1}\n{"v":2}\n{"v":3')
    if len(recs) == 2 and len(errs) == 1 and errs[0]["kind"] == "incomplete":
        print("  PASS framing-incomplete: 2 records + 1 incomplete error")
        passed += 1
    else:
        print(f"  FAIL framing-incomplete: {len(recs)} records, {len(errs)} errors")
        failed += 1
        errors.append("EOF-before-LF framing wrong")

    # 10c. Malformed record consumed through LF: one error, next record intact
    recs, errs = read_control_records(b'{"v":BAD}\n{"v":4}\n')
    if (len(recs) == 1 and recs[0] == {"v": 4}
            and len(errs) == 1 and errs[0]["kind"] == "record"):
        print("  PASS framing-malformed: 1 error, suffix record intact")
        passed += 1
    else:
        print(f"  FAIL framing-malformed: {len(recs)} records, {len(errs)} errors")
        failed += 1
        errors.append("malformed-record framing wrong")

    # 10d. Bare LF is a zero-byte record error, not an exemption
    recs, errs = read_control_records(b'\n')
    if len(recs) == 0 and len(errs) == 1 and errs[0]["kind"] == "record":
        print("  PASS framing-bare-lf: bare LF yields one record error")
        passed += 1
    else:
        print(f"  FAIL framing-bare-lf: {len(recs)} records, {len(errs)} errors")
        failed += 1
        errors.append("bare-LF framing wrong")

    # 10e. int64 boundary acceptance at both ends
    try:
        validate_control_record(b'{"v":9223372036854775807}', "i64max-test")
        validate_control_record(b'{"v":-9223372036854775808}', "i64min-test")
        print("  PASS framing-i64-bounds: both int64 ends accepted")
        passed += 1
    except ConformanceError as e:
        print(f"  FAIL framing-i64-bounds: {e}")
        failed += 1
        errors.append(str(e))

    # 10f. Session cap enforced behaviorally: 1025th record refused
    recs, errs = read_control_records(b'{"v":1}\n' * (MAX_RECORDS_PER_SESSION + 1))
    if (len(recs) == MAX_RECORDS_PER_SESSION and len(errs) == 1
            and errs[0]["kind"] == "session"):
        print(f"  PASS framing-session-cap: {MAX_RECORDS_PER_SESSION} kept, 1 session error")
        passed += 1
    else:
        print(f"  FAIL framing-session-cap: {len(recs)} records, {len(errs)} errors")
        failed += 1
        errors.append("session cap not enforced behaviorally")


    # Summary
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("Failures:")
        for e in errors:
            print(f"  - {e}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
