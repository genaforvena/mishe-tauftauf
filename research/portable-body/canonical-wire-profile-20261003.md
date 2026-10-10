# Canonical Wire Profile — Portable Body Text Boundary

**Status:** preliminary hypothesis, not accepted research
**Created:** 2026-10-03 (wake 315)
**Updated:** 2026-10-10 (wake 576) — added optional `host_id` to intent records
**Owner:** body-research

## Purpose

Define the text boundary between the portable core and the host precisely
enough for conformance testing. This is the canonical profile that all
alternatives must satisfy.

## Scope

- **Control records**: JSONL + LF, strict UTF-8, bounds
- **Observation bytes**: binary frames + transfer rules (opaque to the core)
- **Text boundary**: all communication is text (charter mandate)

## Control Records

### Format

Each control record is one JSON object on one line (JSONL). Lines are
terminated by LF (`\n`, 0x0A). No CR, no BOM.

### Record types

Three record types: `intent`, `start`, `outcome`.

#### Intent record

Written before execution. Contains the operation ID, writer ID, optional
host ID, capability, request, and timestamp.

```json
{"type":"intent","operation_id":"op-001","writer_id":"body-001",
 "host_id":"host-mesh-01","capability":"file.write",
 "request":{"path":"/tmp/target.txt","content":"hello"},
 "timestamp":"2026-10-10T12:00:00Z"}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Must be `"intent"` |
| `operation_id` | string | yes | Unique operation identifier (non-empty) |
| `writer_id` | string | yes | Identifier of the writer (non-empty) |
| `host_id` | string | no | Host identifier (1–64 chars, DNS-like label) |
| `capability` | string | yes | Capability name (non-empty) |
| `request` | object | yes | Request parameters (may be empty) |
| `timestamp` | string | yes | ISO 8601 timestamp |

**`host_id` validation:**
- Optional (may be absent)
- If present, must be a non-empty string (1–64 chars)
- Must match `^[a-zA-Z0-9][a-zA-Z0-9._-]*$` (DNS-like label)

#### Start record

Written when execution begins. Contains the operation ID, host ID, PID, and
timestamp.

```json
{"type":"start","operation_id":"op-001","host_id":"host-mesh-01",
 "pid":12345,"timestamp":"2026-10-10T12:00:01Z"}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Must be `"start"` |
| `operation_id` | string | yes | Matches intent record |
| `host_id` | string | yes | Host identifier |
| `pid` | integer | yes | Process ID |
| `timestamp` | string | yes | ISO 8601 timestamp |

#### Outcome record

Written after execution completes. Contains the operation ID, status, result,
and timestamp.

```json
{"type":"outcome","operation_id":"op-001","status":"completed",
 "result":{"bytes_written":5},"timestamp":"2026-10-10T12:00:01Z"}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Must be `"outcome"` |
| `operation_id` | string | yes | Matches intent/start records |
| `status` | string | yes | `completed`, `failed`, `unsupported`, `indeterminate` |
| `result` | object | yes | Result data (may be empty) |
| `timestamp` | string | yes | ISO 8601 timestamp |

### Bounds

- **Record size**: ≤ 65,536 bytes (excluding LF terminator)
- **Session**: ≤ 1,024 records
- **String values**: ≤ 16,384 bytes
- **Object depth**: ≤ 16 levels
- **Object members**: ≤ 256 per object
- **Array elements**: ≤ 256 per array

### Rejection rules

A record is rejected (and the session terminated) if:
1. Not valid JSON
2. Not a JSON object
3. Missing required fields
4. Field has wrong type
5. `type` is not one of `intent`, `start`, `outcome`
6. `operation_id` is empty or not a string
7. `host_id` is present but invalid (empty, too long, or bad characters)
8. Record exceeds size bounds
9. Session exceeds record count

## Observation Bytes (Binary)

Binary observations are opaque to the core. They are transferred as framed
bytes within the text boundary.

### Frame format

Each frame is a 22-byte header followed by payload bytes.

```
Offset  Size  Description
0       16    Stream ID (32 lowercase hex chars, UTF-8)
16      2     Chunk index (big-endian uint16, 0-based)
18      2     Total chunks (big-endian uint16, 1-based)
20      2     Payload length (big-endian uint16, 0–16384)
22       N     Payload bytes (opaque)
```

### Transfer rules

- **Chunk size**: ≤ 16,384 bytes per chunk
- **Total observation**: ≤ 1 MiB (1,048,576 bytes)
- **Chunk count**: 1–65,535
- **Stream ID**: 32 lowercase hex characters (0-9, a-f)
- **Contiguity**: chunks must arrive in order (index 0, 1, 2, ...)
- **Completeness**: all chunks must be received before the observation is complete
- **Duplicate chunks**: rejected (same index received twice)
- **Out-of-order chunks**: rejected (index > expected next index)

### Rejection rules

A frame is rejected if:
1. Header is not exactly 22 bytes
2. Stream ID is not 32 lowercase hex chars
3. Chunk index ≥ total chunks
4. Total chunks = 0 or > 65,535
5. Payload length > 16,384
6. Payload length ≠ actual payload bytes
7. Chunk index < expected next index (out of order)
8. Chunk index = expected next index but already received (duplicate)
9. Total observation exceeds 1 MiB

## Session lifecycle

1. **Open**: session begins, journal is opened for append
2. **Allocate**: intent record written, operation ID returned
3. **Dispatch**: start record written, effect executed, outcome record written
4. **Recover**: query journal for operation status
5. **Reconcile**: scan journal for incomplete operations
6. **Close**: session ends, journal is closed

## Conformance

A conforming implementation must:
1. Accept all valid records (per this profile)
2. Reject all invalid records (per rejection rules)
3. Handle binary observations (per frame format and transfer rules)
4. Maintain session bounds (per bounds section)
5. Support recovery and reconcile (per session lifecycle)

## Change log

- **2026-10-03**: Initial version (wake 315)
- **2026-10-10**: Added optional `host_id` to intent records (wake 576)
