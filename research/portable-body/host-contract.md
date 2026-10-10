# Portable Body — Host Contract

**Status:** preliminary hypothesis, not accepted research
**Created:** 2026-10-10 (wake 284)
**Owner:** body-research

## Purpose

Separate **mandatory** mechanisms (any portable-body alternative must
provide them) from **optional** features (can be added later). Each
mandatory mechanism is justified by required behavior or a concrete
failure case. The contract is alternative-agnostic: A1 (dispatcher),
A2 (text interpreter), and A3 (shell) must all satisfy it.

The contract specifies **what** must hold, not **how** each alternative
implements it. Exact journal encoding, command syntax, transport, and
capability names are left to each alternative.

## Mandatory Mechanisms

### M1: Operation identity assigned before effect

**Required behavior:** The caller receives an operation ID before the
effect executes. The ID is the correlation key for all later queries.

**Failure case without it:** If the ID is assigned after execution (or
only returned in the response), a lost response leaves the caller with an
anonymous operation. They cannot query it, cannot reconcile it, and
cannot distinguish "never executed" from "executed but response lost".
This is the charter's "lost response containing host IDs" scenario.

**How each alternative satisfies it:**
- A1: `allocate()` generates UUID4, writes intent record, returns ID.
- A2: interpreter generates ID, writes intent record, returns ID in output.
- A3: `allocate()` generates ID, writes intent record, returns ID.

### M2: Durable journal (intent / start / outcome records)

**Required behavior:** Every operation produces a durable record of what
was intended, what started, and what completed. The journal survives
process restart.

**Failure case without it:** A crash between effect and response leaves
no trace. The caller cannot determine whether the effect happened. The
D3 oracle (wake 65146) shows scenarios B/C (crash after effect, before
receipt) are indistinguishable from "never sent" without a journal.

**How each alternative satisfies it:**
- A1: JSONL journal, three record types (intent, start, outcome).
- A2: text journal, three record types (intent, start, outcome).
- A3: text journal, two record types (intent, outcome). **Gap:** A3 has
  no explicit start record; it detects intent-without-outcome, which is a
  weaker signal (cannot distinguish "never started" from "started but
  crashed before outcome"). Acceptable for minimal shell body; A1/A2
  provide the stronger signal.

### M3: Recovery query (recover by operation ID)

**Required behavior:** Given an operation ID, return `completed`,
`in_flight`, or `unknown`.

**Failure case without it:** A caller with a lost response has no way to
learn the outcome. They must either re-execute (risking duplicate
effects) or give up (losing the result). The charter requires "honest
unknown" — the body must be able to say "I don't know" rather than
guessing.

**How each alternative satisfies it:**
- A1: `recover(operation_id)` reads journal, returns status.
- A2: `RECOVER op-id` command, returns status.
- A3: `recover()` shell function, returns status.

### M4: Reconcile on startup (detect interrupted operations)

**Required behavior:** On startup, scan the journal for operations with
intent/start but no outcome, and mark them `indeterminate`.

**Failure case without it:** Interrupted operations remain silently
`in_flight` forever. The caller doesn't know whether to retry. The
connection-loss scenario requires this: after reconnect, the body must
surface "these operations may or may not have completed."

**How each alternative satisfies it:**
- A1: `reconcile()` scans journal, writes synthesized outcomes.
- A2: `RECONCILE` command, writes synthesized outcomes.
- A3: `reconcile()` shell function, writes synthesized outcomes.

### M5: Capability discovery

**Required behavior:** The body reports what the host can do (e.g.,
`file.read`, `file.write`, `process.spawn`).

**Failure case without it:** A caller attempts an unsupported operation
and gets an opaque failure. Discovery lets the caller adapt before
attempting. The charter's "absent capability" scenario requires the body
to know what the host supports.

**How each alternative satisfies it:**
- A1: `host_adapter.discover()` probes Python version, OS, modules.
- A2: `DISCOVER` command probes host (`uname`, `/proc`, `command -v`).
- A3: `discover()` probes `uname`, `/proc/meminfo`, `command -v`.

### M6: Honest absent-capability failure

**Required behavior:** When a capability is absent, the body returns an
explicit `unsupported` outcome — never silent skip, never crash.

**Failure case without it:** Silent skip means the caller believes the
transformation happened when it didn't. This violates the charter's
"honest unknown" requirement and could cause data loss (caller thinks
file was written, but it wasn't).

**How each alternative satisfies it:**
- A1: capability registry check before dispatch; writes `unsupported`
  outcome.
- A2: parser finds no such operation → `ERR unknown_operation`.
- A3: shell command fails → `ERR write_failed` with exit code.

### M7: Text boundary (communication)

**Required behavior:** The body communicates with the host via text —
text commands in, text observations out.

**Failure case without it:** A binary boundary requires serialization
libraries on both sides, increasing host requirements. Text is the most
portable boundary (charter: "Communication (text boundary between
portable core and host)"). Binary observations are encoded within text
(see obligation #5, canonical text).

**How each alternative satisfies it:**
- A1: JSONL records (text).
- A2: line-based text commands and `OK`/`ERR` output.
- A3: shell commands and text output.

### M8: Host authority (host determines effects)

**Required behavior:** The host decides whether an effect is permitted.
The body does not assume a caller-supplied "read" label means
side-effect-free.

**Failure case without it:** If the body trusts the caller's label, a
"read" of a special device (e.g., `/proc/sysrq-trigger`) could cause
effects the caller didn't intend. Authority policy belongs to the host,
not the caller.

**How each alternative satisfies it:**
- A1: dispatcher executes via host calls; host OS enforces permissions.
- A2: interpreter executes via host calls; host OS enforces permissions.
- A3: shell executes via host calls; host OS enforces permissions.

## Derived properties (consequences of M1–M4)

These are not separate mechanisms but emerge from the mandatory set:

- **Connection loss behavior:** M2 (journal) + M3 (recover) + M4
  (reconcile) together answer "what happened when the connection was
  lost?" The body detects interrupted operations and reports them as
  `indeterminate`.
- **Process restart survival:** M2 (durable journal) ensures state
  survives restart. M3 + M4 make that state queryable.
- **Honest unknown:** M3 (recover) returns `unknown` for operations with
  no journal record. M6 returns `unsupported` for absent capabilities.

## What the contract does NOT specify

Left to each alternative (avoid over-constraining the minimal body):

- **Journal encoding** — JSONL (A1) vs `key=value` text (A2/A3).
- **Command syntax** — JSONL records (A2) vs shell functions (A3).
- **Transport** — stdin/stdout, file, or socket.
- **Capability names** — each alternative chooses its vocabulary; must be
  discoverable (M5).
- **Timestamp format** — useful for debugging, not required for recovery
  (operation ID is the correlation key).

## Optional Features (not required for minimal body)

| Feature | Why optional |
|---------|--------------|
| Exactly-once execution | Charter explicitly says "no implied exactly-once execution". At-least-once with idempotent retry is the practical model. |
| Automatic retry | The body does not retry automatically; the caller decides based on recover/reconcile results. Auto-retry risks duplicate effects. |
| Binary observations | Text boundary is mandatory; binary can be base64-encoded within text if needed (obligation #5). |
| Encryption / authentication | Host authority policy, but not required for minimal local body. |
| Multi-host federation | Out of scope; the body is single-host. |
| Timestamps in journal | Useful for debugging but not required for recovery (operation ID is the correlation key). |
| Rich capability metadata | Discovery can return simple capability names; versions/limits can be added later. |
| Concurrent operations | The minimal body handles one operation at a time; concurrency can be added if needed. |

## Charter mandate mapping

- Separate mandatory from optional ✅ (this document)
- Justify each mandatory mechanism ✅ (M1–M8, each with required behavior + failure case)
- Do not turn a tiny seed into a large service ✅ (optional features explicitly deferred)

## Known gap: A3 start-record weakness

A3 (shell) writes intent and outcome but no explicit start record. This
means A3 detects **intent-without-outcome** (weaker) rather than
**start-without-outcome** (stronger, A1/A2). The practical consequence:
A3 cannot distinguish "shell killed before execution began" from "shell
killed during execution". For a minimal shell body this is acceptable;
A1/A2 provide the stronger signal. If A3 is chosen as the winner, a start
record should be added.

## Next steps

1. **Canonical text** (obligation #5) — define the text boundary precisely
   enough for conformance. Handle binary observations.
2. **Deterministic replay** (obligation #6) — compare alternatives on
   identical recorded inputs.
3. **Bounded prototype** — build the smallest alternative that satisfies
   this contract.
