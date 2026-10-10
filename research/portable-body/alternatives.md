# Three portable-body alternatives — fully specified

Status: design specifications, not implemented or measured. Each alternative
gives exact syntax, complete execution rules, portability analysis, and the
same four-scenario walkthrough. The shared fixture trace
(`shared-fixture-trace-20261003.md`) provides the concrete wire examples.

## A1: Dispatcher-only (H1)

**Core idea:** The existing Python composition IS the body. Orchestration
executes in CPython; the portable side is a thin dispatch/receive boundary.

### Syntax

JSONL intent/start/outcome records. Each record is one JSON object + LF.
Operation identity is caller-assigned before effect dispatch.

```jsonl
{"type":"intent","operation_id":"o1","capability":"fs.read","request":{"path":"x"},"writer_id":"<id>","host_id":"<id>","timestamp":"<ts>"}
{"type":"outcome","operation_id":"o1","capability":"fs.read","request":{"path":"x"},"writer_id":"<id>","host_id":"<id>","timestamp":"<ts>","status":"completed","result":...}
```

### Execution rules

1. Caller assigns operation ID before any effect-capable dispatch.
2. Intent record written to durable journal (fsync) before effect.
3. Effect dispatched to host adapter (platform-specific).
4. Outcome record written after effect completes.
5. Lost reply: query by operation ID; journal shows intent-without-outcome.
6. Recovery: intent-without-outcome is UNKNOWN; no automatic retry.

### Portability

- Source: 113,370 B Python (measured at HEAD `afee8d6`)
- Zipapp: 115,467 B single executable
- Import RSS: 21,504 KiB
- Platform surface: POSIX-only (fcntl, signal, os.killpg)
- Windows adapter: required but not built
- Dependencies: Python 3.12+, stdlib only

### Four-scenario walkthrough

1. **Capability discovery:** caller emits `cap.list`; host replies with
   available capabilities. Snapshot is not authority.
2. **Transformation:** caller reads file, applies deterministic filter,
   sends result. All transforms execute in the broker/coordinator.
3. **Absent capability:** host replies `unsupported`; caller stops, preserves
   prior complete results.
4. **Connection loss:** caller loses reply, reconnects, queries by operation
   ID. Journal shows intent-without-outcome → UNKNOWN.

## A2: Minimal text/record interpreter (H2)

**Core idea:** A small evaluator over a canonical text format performs
deterministic filtering, branching, sequencing and trace updates. All world
access remains named host calls.

### Syntax

Line-based text commands. One command per line, LF-terminated.

```
DISCOVER
READ_FILE <path> <max_bytes>
WRITE_FILE <path> <content>
RECOVER <operation_id>
RECONCILE
```

Values are JSON-like: null, booleans, integers, strings, lists, maps with
string keys, or opaque bytes. No implicit host variables, clock, filesystem,
network or process access.

### Execution rules

1. Program is a UTF-8 document containing one ordered list of steps.
2. Each step consumes prior named results.
3. Local operators: equality, presence test, bounded list/map projection,
   integer comparison, conditional branch.
4. No user-defined recursion, floating point, dynamic module loading, or
   implicit retries.
5. Steps have a declared maximum count and value/byte budget.
6. Every operator has one deterministic result or structured error.
7. The evaluator emits a trace before/after each step.
8. All effects are named `observe` or explicitly granted `act` calls.

### Portability

- Custom C interpreter: <10 KB (estimated, not measured)
- Alternative: awk (~2 KB) for bounded transforms
- No shell required after launch
- One runner binary per OS/ABI
- RAM bounded by explicit values and streams

### Four-scenario walkthrough

1. **Capability discovery:** `DISCOVER` command; host replies with available
   capabilities as a JSON value.
2. **Transformation:** `READ_FILE` returns bytes; local filter step transforms;
   result is named and available to subsequent steps.
3. **Absent capability:** host returns `unsupported` error; evaluator branches
   to error handling step.
4. **Connection loss:** evaluator state is lost; reconnect and issue
   `RECOVER <operation_id>`; journal shows intent-without-outcome → UNKNOWN.

## A3: Existing machine (H3) — shell

**Core idea:** Shell scripts using coreutils. Orchestration in bash/sh.
Journal is an append-only text file.

### Syntax

Shell commands with key=value journal records.

```bash
# Intent
echo "op=o1 cap=fs.read args={\"path\":\"x\"} status=intent" >> journal.log

# Effect
cat "x" > /tmp/result

# Outcome
echo "op=o1 status=ok result=$(wc -c < /tmp/result)" >> journal.log
```

### Execution rules

1. Each operation gets a unique ID (timestamp + counter or UUID).
2. Intent appended to journal before effect.
3. Effect executed via shell command.
4. Outcome appended after effect completes.
5. Lost reply: grep journal for operation ID; intent-without-outcome.
6. Recovery: intent-without-outcome is UNKNOWN; no automatic retry.

### Portability

- ~100 lines shell, ~3 KB
- Requires POSIX shell + coreutils
- busybox-compatible
- Journal: append-only text file, no locking (single-writer assumed)

### Four-scenario walkthrough

1. **Capability discovery:** source a capabilities file; grep for available
   capabilities.
2. **Transformation:** `cat file | awk '{...}'` for filtering.
3. **Absent capability:** grep returns no match; script exits with error.
4. **Connection loss:** grep journal for operation ID; intent-without-outcome
   → UNKNOWN.

## Comparison table

| Property | A1 (Dispatcher) | A2 (Interpreter) | A3 (Shell) |
|---|---|---|---|
| Orchestration | CPython | Custom C / awk | bash/sh |
| Size | 113 KB source | <10 KB C / ~2 KB awk | ~3 KB shell |
| Portability floor | Python 3.12+ | C compiler or awk | POSIX shell + coreutils |
| Operation identity | JSONL record | Line-based command | key=value journal |
| Recovery | Query by op ID | RECOVER command | grep journal |
| Testability | High (Python asserts) | Medium (custom parser) | Low (shell quoting) |
| Windows adapter | Required | Required | Required |
| Offline transforms | Yes (in broker) | Yes (local evaluator) | Limited (shell) |
| Deterministic replay | Yes (pure Python) | Yes (pure functions) | Yes (pure shell) |

## Shared fixture trace mapping

All three alternatives consume the same fixture
(`shared-fixture-trace-20261003.md`):

- Session `s7`, read operation `o9`, send operation `o10`
- Fixture: `alpha\n\nbeta\n` (12 bytes) → filter → `alpha\nbeta\n` (11 bytes)
- Streams: read=0x01 (12 B), send=0x02 (11 B)

Each alternative produces the same observable branch outcomes; only
executor/state ownership differs.
