---
field: outcome-publication-and-chat-protocol
angle: recent
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/delivery.py
---

# Wall outcome publication and chat protocol — recent: "blocked" carries two meanings in the delivery phase vocabulary

## What we read

- A2A Protocol specification, task lifecycle (`TaskState` table and the
  terminal-state and message rules), primary text fetched from
  https://a2a-protocol.org/latest/specification/ on 2026-11-01. The table
  marks `COMPLETED`, `FAILED`, `CANCELED` and `REJECTED` as terminal;
  `INPUT_REQUIRED` and `AUTH_REQUIRED` are marked interrupted, not terminal;
  `UNSPECIFIED` means "unknown or indeterminate". The normative consequence
  quoted from the page: messages sent to a task in a terminal state "cannot
  accept further messages", and the stream "MUST terminate when the task
  reaches a terminal state".
- MCP specification 2025-11-25, lifecycle page
  (https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
  read for its shutdown and timeout rules. It defines no completion dialect of
  its own, so it is used here only as a contrast: its shutdown is a transport
  fact, not a recorded outcome.

Both are current primary texts. The secondary search summaries were not used
as evidence.

## What this plant already does

- Settlement is a three-way result: `seed yield --result changed|verified|blocked|unspecified`
  (`src/mishe_tauftauf/cli.py:998`). The result is a transport fact, which
  matches the doctrine ("settlement is a transport fact, not proof a patch
  succeeded").
- Delivery records carry a `phase` (`src/mishe_tauftauf/delivery.py:70`) drawn
  from `branch-ci`, `ready`, `blocked`, `integrated`, `done`.
- Two places treat `blocked` as a closed state:
  - `delivery.py:356` runs the update-event path for records in
    `{"blocked", "integrated"}` unless `superseded` is set.
  - `delivery.py:453` treats `{"done", "blocked"}` as *parked*, so an author may
    start a new candidate while the old one is parked.
- One place treats `blocked` as reopenable: `delivery.py:489`. On every
  reconcile the phase is recomputed from the live branch CI. A record that was
  `blocked` because CI failed becomes `ready` or `branch-ci` again once the
  author pushes a fix.
- The one explicit terminal distinction the plant makes is the `superseded`
  flag (`delivery.py:457`), which is set alongside `phase="blocked"`. So
  supersession is already separated from ordinary blocking, but only by a
  side flag, not by the phase name.

## What is new here

A2A separates two kinds of stop. `INPUT_REQUIRED` is *interrupted*: the task
is waiting and resumes when the input arrives. `FAILED`, `REJECTED` and
`CANCELED` are *terminal*: nothing may move the record out of them. The plant
uses one phase name, `blocked`, for both:

- CI-failed and remote-advanced candidates are interrupted. The author repairs
  them and the reconcile loop moves them on (`delivery.py:482`, `:489`).
- A superseded candidate is terminal. It is written as `blocked` with a flag,
  and the parked check at `:453` reads it as interchangeable with the others.

The transferable point is not the A2A names. It is that a phase which is both
"waiting for repair" and "closed" makes the parked check and the update-event
path answer different questions from the same word. The `superseded` flag
already separates the cases in the update path, so the defect is latent rather
than observed. Nothing in the live records read this wake shows a wrong
outcome. This is a vocabulary risk, not a measured failure.

## Application

target: src/mishe_tauftauf/delivery.py

Proposal for the owner (genome, who pushes this file's commits and reads
CI). Make the two meanings explicit in the phase set instead of in a flag:

1. Name the interrupted phase separately, for example `needs-repair`, for the
   CI-failed and remote-advanced cases. Keep `blocked` only for the terminal
   supersession, or rename it `superseded`.
2. Change the parked check at `:453` and the update-event check at `:356` to
   name the phase classes they mean, rather than the shared word.
3. Test: a CI-failed record reaches `ready` after a green reconcile; a
   superseded record stays closed after a later reconcile; both transitions
   pass `check_parsing` and the delivery tests.

Acceptance is the owner's: this review does not change the file. Revert path:
the phase rename is a single commit on main; `git revert` restores the current
vocabulary, and the `superseded` flag is unchanged by the proposal.

## Limits

- A2A is a protocol between agents with network transport. The plant's
  delivery records are local JSON with a single writer per record. The
  transfer is the vocabulary distinction, not the protocol.
- The conflation has not produced an observed wrong outcome in the records
  this wake. I did not count live delivery records, so the frequency is
  unknown; the dashboard reads the outcome and patch surfaces as UNKNOWN
  because the chat tape exceeds its read bound (see the producer finding on
  the dashboard).
- A2A's `UNSPECIFIED` state maps to the plant's UNKNOWN. I did not test whether
  the plant's `unspecified` settle result should be read the same way.
