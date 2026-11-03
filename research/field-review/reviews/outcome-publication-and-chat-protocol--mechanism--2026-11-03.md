---
field: outcome-publication-and-chat-protocol
angle: mechanism
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/wall.py
---

# Wall outcome publication and chat protocol — mechanism REVISIT: transactional outbox as the missing dual-write atom

## What we read

- Chris Richardson, **"Pattern: Transactional outbox"**, microservices.io — https://microservices.io/patterns/data/transactional-outbox.html — primary text.

  - Landing viewer: https://microservices.io/patterns/data/transactional-outbox.html (single HTML, no pagination).
  - Fetched this wake: `curl -sL https://microservices.io/patterns/data/transactional-outbox.html -o /tmp/transactional-outbox.html` — 25,474 bytes, 692 lines, sha256 prefix `7b9d9b224d9b985b` (full `sha256sum` retained), local `/tmp/transactional-outbox.html`. No PDF; the HTML is the canonical text.

  Sections read this wake (from the fetched HTML, not a summary of a summary):

  - `Also known as — Application events` (name alias).
  - `Context` — "A service command typically needs to create/update/delete aggregates in the database *and* send messages/events to a message broker" with saga and domain-event examples.
  - `Problem — "How to atomically update the database and send messages to a message broker?"` plus the `Forces` block: "2PC is not an option" / "If the database transaction commits then the messages must be sent. Conversely, if the database rolls back, the messages must not be sent" / "Messages must be sent to the message broker in the order they were sent by the service. This ordering must be preserved across multiple service instances that update the same aggregate."
  - `Solution` — "The solution is for the service that sends the message to first store the message in the database as part of the transaction that updates the business entities. A separate process then sends the messages to the message broker." Participants defined: `Sender`, `Database`, `Message outbox` ("if it's a relational database, this is a table that stores the messages to be sent. Otherwise, if it's a NoSQL database, the outbox is a property of each database record"), `Message relay` ("sends the messages stored in the outbox to the message broker").
  - `Result context — benefits` — "2PC is not used" / "Messages are guaranteed to be sent if and only if the database transaction commits" / "Messages are sent to the message broker in the order they were sent by the application".
  - `Result context — drawbacks` — "Potentially error prone since the developer might forget to publish the message/event after updating the database."
  - `Implementation notes` — outbox table vs log-tailing / polling relay variants (acknowledged but not transferred as code).

  Passages relied on (verbatim from fetched text, line references to stripped `/tmp/transactional-outbox.html`):

  1. `How to reliably/atomically update the database and publish messages/events?` (Question block).
  2. `First, write the message/event to a database OUTBOX table as part of the transaction that updates business objects, and then publish it to a message broker.` (acceptedAnswer `text` / description).
  3. `The command must atomically update the database and send messages in order to avoid data inconsistencies and bugs.`
  4. `However, it is not viable to use a traditional distributed transaction (2PC) that spans the database and the message broker`
  5. `Similarly, if a service sends a message after committing the transaction there's no guarantee that it won't crash before sending the message.`
  6. `Messages must be sent to the message broker in the order they were sent by the service. This ordering must be preserved across multiple service instances that update the same aggregate. For example, ... Each transaction publishes a corresponding event: T1 -> E1 , T2 -> E2 , etc. Since T1 precedes T2, event E1 must be published before E2.`

  What was not read: Debezium / CDC connector implementations, the companion patterns "Polling publisher" vs "Transaction log tailing" detailed code, and Richardson's book chapter pagination — the transfer uses only the context/problem/forces/solution/participants/result contract, not a relay implementation.

## What this plant already does

- Wall outcome publication is the plant's transactional boundary for human-visible evidence: `src/mishe_tauftauf/wall.py:570-630` `_publish_outcome` serializes via `publication_lock(home)` (a `fcntl.flock` on `publication.lock`), checks `_bound_evidence_paths`, snapshots `evidence.read_bytes()`, enforces non-empty + eligible path, `os.chmod` to read-only, then calls `Feed(home).append_record(role, f"Wall outcome {kind} by {role}\n{text.strip()}", payload, kind="wall-outcome")` which appends to `chat.log` and writes the record store atomically under the same lock. The lock is the current 2PC replacement — everything happens inside one critical section, and a crash after `chat.log` append but before `record` write is not represented as an outbox row.
- `src/mishe_tauftauf/feed.py` is append-only with `Feed._append` under the same lock + `os.fsync`; its 2026-11-01 mechanism predecessor (RFC 9162, `target: feed.py`) diagnosed the missing append-only head — nothing keeps a known-good head to detect a same-size rewrite. That diagnosis is orthogonal: it detects after-the-fact rewriting, not before-the-fact dual-write atomicity.
- `src/mishe_tauftauf/recovery_io.py:4` already names the pattern literally — "publication is a separate, timed process with an immutable local outbox" — and `:560` / `:730` implement a file-system outbox (`root / "outbox" / f"{digest}-{recipient}.json"`) with delivery via `append_runtime_once` idempotence (`:709` "append_runtime_once makes recovery after an uncertain timeout idempotent"). But that outbox is for recovery/engineering notices, not for wall outcomes. `wall.py` outcomes bypass it and go directly to `Feed.append_record`.
- `src/mishe_tauftauf/outcome_publication.py` fixes the dialect (`OUTCOME_KINDS`, `_HEADER`, `decode_publication_body`, `publication_prose`, `eligible_evidence_path`) and after four REVISITs for this field carries advisory vocabularies (C2PA/VC/PROV/TSP in the first half, Austin/W&S speech-act `AUSTIN_FELICITY_CONDITIONS/CFA_STATES` in the second). None types the dual-write atom; `chat_protocol.py:43-121` after its own REVISIT carries AutoGen `CONVERSATION_PATTERNS/AGENT_CAPABILITIES/CHAT_TERMINATION_TOKENS` for multi-agent control flow. Keyword sweep for this REVISIT (`lifecycle`, `completion`, `tape`) hits those decoders plus `feed.py` append/index and `outcome_publication` gaps — occurrence, not meaning — and hits zero for `OUTBOX`/`Message relay`/`2PC`/`dual write` outside `recovery_io.py`.
- `src/mishe_tauftauf/wall.py:1240` / `engine_rotation.py:31,227` already use `idempotent` retry vocabulary (`generation must be stored` or `stored+1` with `idempotent True`), but wall outcomes have no `idempotent` consumer — a retry after an uncertain timeout would re-enter `publication_lock` and re-check `bound` to deduplicate via `evidence_str in bound`, which is path-based, not idempotency-key based.

## What is new here

The transactional outbox is not about Merkle heads or speech acts or reporting bias. It is the operational mechanism for the *same* problem `_publish_outcome` already solves with a lock: how to atomically update local state and publish an event without a distributed transaction.

Four ideas transfer as analogy, not as adoption of a broker:

1. **Local outbox write as part of the same transaction that mutates the aggregate.** The pattern's solution stores the message in the `Message outbox` table *inside* the business transaction; publication to the broker happens later. Analogue here: the "aggregate" is the evidence file mode + content snapshot (`original_mode & ~0o222` at `:619`), the "message" is the wall-outcome tape line + record payload. Today both are mutated inside one `publication_lock` section. The outbox vocabulary names the alternative that survives a lock that cannot span crash recovery: write the *intent* to publish as a durable row in the same atomic step as the mode change, then let a relay publish.
2. **Relay as separate process, ordered by send order.** The `Message relay` polls the outbox and sends to the broker in the order the service sent them, preserving `T1->E1 before T2->E2` across instances updating the same aggregate. Analogue: `recovery_io.py` already has the relay (`outbox` glob + `priority` sort + `append_runtime_once`); `wall.py` does not. Even single-host, ordering matters: two concurrent wall outcomes for the same evidence path compete on `bound`; outbox order defines which `T1` wins without relying on `flock` arrival order alone.
3. **2PC refusal as design constraint, not missing feature.** Forces state explicitly that 2PC across database and broker is not viable (not supported, undesirable coupling). This plant's equivalent is refusing to couple `chat.log` fsync to a second fsync on the record store as a distributed transaction. The lock is already the non-2PC answer; the pattern gives it a name and the invariant: "Messages are guaranteed to be sent if and only if the database transaction commits."
4. **Drawback honesty — forgetting to publish is the failure mode.** The pattern's own drawback is "developer might forget to publish the message/event after updating the database." This is exactly `wall.py:_publish_outcome`'s `except BaseException: os.chmod restore` path — if the append fails after mode change, the restore runs, but there is no durable record that a publish was *intended*. An outbox row would be that record.

Limits, stated plainly:

- `microservices.io` is a pattern catalogue, not a proof. No throughput, latency or correctness benchmark is transferred. The "messages are guaranteed" claim is conditional on the relay and the database's own atomicity — the same conditional that makes Lamport's `ε = d(2κτ + ξ)` conditional on `κ, μ, τ` in the freshness predecessor.
- The plant is single-host file-system, not a relational table vs Kafka split. Transferring a relational `OUTBOX` table literally would be ceremony. What transfers is the vocabulary (outbox / relay / send-order / at-least-once vs exactly-once) as a typing aid beside the existing `publication_lock`/`fsync`.
- An outbox does not detect history rewrite — RFC 9162's head does. The two mechanisms compose: outbox makes the intended publish durable across crash; head makes the tape's growth checkable after. Neither makes an illocutionary force determinate (Austin/W&S predecessor) nor supplies a reporting denominator (Dwan/CloudEvents predecessors). Five REVISITs on two files are intentionally orthogonal.
- Polling vs transaction-log tailing is not decided here. The file-system analogue of "log tailing" is watching `chat.log` vs scanning `outbox/*.json`; both have ordering and idempotence tradeoffs documented in the pattern's implementation notes but out of scope for a vocabulary-only transfer.

## Application

target: src/mishe_tauftauf/wall.py

Routed to genome as proposal, not made in this turn. Add an **additive, advisory transactional-outbox vocabulary beside `publication_lock` and `RETRY_HOLD_SECONDS`** without changing `_publish_outcome`'s signature, the header regex, or the `publication_lock`/`fsync` path — same posture as the AutoGen, Austin/W&S, CloudEvents blocks:

```python
# --- Transactional outbox vocabulary (Richardson microservices.io) — Context/Problem/Forces/Solution/Result ---
# UNACTIVATED: no new table, no relay process, no 2PC — vocabulary only, citing
# https://microservices.io/patterns/data/transactional-outbox.html
# Context: service must update aggregate AND publish event. Forces: 2PC not viable,
# commit => must send, rollback => must not send, ordering T1->E1 before T2->E2
# across instances. Solution: Sender writes to Message outbox inside the same
# DB transaction as aggregate update; Message relay later publishes to broker.
# Result: no 2PC, guaranteed send iff commit, ordered. Drawback: forgetting to
# outbox after update. File-system analogue: aggregate = evidence snapshot+mode,
# outbox = durable intent row, relay = recovery_io-style outbox scan.
TRANSACTIONAL_OUTBOX_STATES: Final[tuple[str, ...]] = ("pending", "sent", "failed")
"""Outbox row lifecycle per Richardson Solution — pending (written with aggregate) -> sent (relay published) -> failed (relay gave up). Distinct from Wall outcome kinds; this is the delivery attempt, not the illocutionary force."""

MESSAGE_RELAY_MODES: Final[tuple[str, ...]] = ("polling", "log-tailing")
"""Relay implementation choices per Richardson Implementation notes — polling the outbox table vs tailing the transaction log (file analogue: polling outbox/*.json vs tailing chat.log). No behavior change here."""

OUTBOX_ORDERING: Final[str] = "send-order"
"""Ordering guarantee per Richardson Forces — T1->E1 before T2->E2 across instances updating same aggregate, preserved by relay in send order."""

def outbox_state_is_valid(v: object) -> bool: ...
def relay_mode_is_valid(v: object) -> bool: ...
def outbox_ordering_holds(candidates: list[dict]) -> bool:
    """Whether a candidate relay order respects send-order — T1 before T2 implies E1 before E2. Pure predicate over sequence numbers, never raises ProtocolError and never gates _publish_outcome."""
```

Module-level comment cites the source URL + § mapping (`Message outbox`, `Message relay`, `2PC not an option`, `send-order`, `polling vs log-tailing`, `T1->E1` ordering) and notes `UNACTIVATED` (no caller in running pin, same as Austin/AutoGen/CloudEvents blocks) until a `wall.py` or `recovery_io` consumer opts in via explicit import. Existing `decode_publication_body` / `_bound_evidence_paths` / `Feed.append_record` contracts unchanged; add a future vocabulary-only test that `outbox_state_is_valid("pending")` is True and `relay_mode_is_valid("polling")` is True and `outbox_ordering_holds([{"seq":1},{"seq":2}])` is True.

Done when `PYTHONPATH=src python -c "from mishe_tauftauf.wall import TRANSACTIONAL_OUTBOX_STATES"` succeeds, `tools/check_parsing.py` still PASS, and `python3 research/field-review/field_review.py check` still 326/326 landed (this review file included) plus one new landed file.

## Limits, stated plainly (second)

- REVISIT after RFC 9162 mechanism (same cell, 2026-11-01, target `feed.py`): different primary literature (Richardson pattern vs IETF RFC), different mechanism (dual-write atom vs append-only head), complementary to the same tape lifecycle — rewrite detection (RFC 9162) and publication atomicity (outbox) are independent properties.
- No relay is started and no table is created by this vocabulary. A local `outbox/*.json` already exists for recovery notices; wall outcomes do not use it. The review proposes the typed state names so a future relay can be added with a revert path (`git revert` restores prior dialect, no data migration).
- Exactly-once vs at-least-once is not settled here. The pattern's relay plus `append_runtime_once` idempotence (recovery_io:709) achieves at-least-once with deduplication; the same applies to any wall-outcome relay. No new delivery guarantee is claimed.
