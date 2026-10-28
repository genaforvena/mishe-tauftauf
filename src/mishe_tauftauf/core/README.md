# Mishe core

The minimal life-support core is a candidate supervisor for the plant's
canonical lifecycle. Live wake and settlement acceptance remains open.

It lives inside this repository, on `main`, next to the code it serves. There
is no second repository and no second branch.

## What is here

| Module | Owns |
| --- | --- |
| `interfaces.py` | The generic contracts: `Dispatch`, `WorkerResult`, `Observation`, `RoleRegistry`, `Worker`. No Mishe types. |
| `registry.py` | Which roles may be dispatched, and the check that the registry itself is sound. |
| `state.py` | The durable dispatch record — one file per role, written atomically. |
| `supervisor.py` | The ordering and refusal rules: declared roles only, one dispatch at a time, no replay. |
| `worker.py` | Running one command as a bounded, isolated process group. |
| `adapters.py` | The only place that reaches the plant's existing machinery. |

## The boundary

The core contains no Mishe protocol. It does not parse the tape, does not
write a wall, does not speak to tmux and does not touch the host guard.

Those live behind the adapters in `adapters.py`, and each adapter is the only
caller of the plant's own code:

- `CanonicalRoleRegistry` reads the site's declared roles from `activity.roles`.
- `CanonicalWorker` asks `wall.tick` for a wake, runs the turn, and settles it
  with `seed.yield_wake`.
- `CanonicalObservation` reads the canonical pending/yield/clear state.

The vocabulary is the plant's, not this package's. A wake is created by the
canonical tick, a turn is settled by the canonical settlement, and a turn is
cleared by the canonical clear. This package adds no second protocol and no
second record of what a wake was.

The resident loop holds when no wake is pending rather than dispatching with
a fabricated identity. Because its worker calls the tick only after dispatch,
this also means it cannot create its own first wake. Do not use this path for
live migration until wake creation and replay handling are independently verified.


Every import of the plant's machinery happens inside a function in
`adapters.py`, so importing `mishe_tauftauf.core` does not load the tape
parser, the wall writer, the tmux layer or the effect ledger. `tests/core/
test_boundaries.py` enforces both halves of that rule.

## The rules the supervisor enforces

1. **Only a declared role is dispatched.** The site decides what "declared"
   means; the core reads that decision and refuses everything else.
2. **One dispatch at a time, across all roles.** Two workers never share a
   wake. The check and the claim are under one file lock, so two
   *processes* cannot both see "nothing open" and each start a turn.
3. **An unresolved effect blocks the next dispatch.** A worker that started
   and produced no outcome may already have had its effect, so the record
   stays open and the next attempt is refused.
4. **An open dispatch is reconciled, never retried.** `reconcile` records what
   was checked; it does not re-run the work.

Point 3 and point 4 are what keep a restarted supervisor from turning one
effect into two.

### What counts as an unresolved effect

A worker that exits non-zero has an **unknown** effect, not a failed one. A
non-zero exit says the command did not finish successfully; it says nothing
about whether the command already did its work before it stopped. Recording
it as a clean failure would let a successor replay work that had already
happened.

The one exception is a command that never started: there was no process, so
there was no effect, and that is a plain failure.

A missing observation source reads **UNKNOWN**, never healthy. There is no
detector, so there is no evidence, and no evidence is not a pass.

## States

| State | Meaning |
| --- | --- |
| `pending` | Reserved; not yet started. |
| `started` | The worker began and no outcome was recorded. |
| `completed` | The work finished. |
| `failed` | The work never started, or the site owed no turn. |
| `skipped` | The site owed no turn, so no work was due. |
| `unknown` | The work started and no outcome survived; the effect is not known. |
| `reconciled` | An open dispatch was checked and closed by hand. |

`started` and `unknown` are the open states. Both block a new dispatch.

## Tests

`tests/core/` holds the core's own tests. They use no plant, no tmux and no
provider; the isolation rules are tested by the tests themselves.

The prototype in `src/mishe_tauftauf/minimal_supervisor.py` predates this
package and stays importable. It is a prototype: it has no locking, no
registered role and no canonical settlement, and it is not used by the
supervisor here.
