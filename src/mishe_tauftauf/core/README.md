# Mishe core

The minimal life-support core is a candidate supervisor for the plant's
canonical lifecycle. Live wake and settlement acceptance remains open.

Mishe's continuity is an ongoing loop: keep an intention, observe the world,
choose and take an action, examine its effects, and carry unfinished work
forward. A mind contributes judgment; durable records, bounded execution and
independent observation let that work continue across interruptions. Recovery
must distinguish a problem that calls for investigation from an uncertain
effect that must be reconciled before repeating an action. The aim is to
recover autonomously from demonstrated failures, within the limits of the host,
storage, access and inference services on which the plant depends. This is a
design goal, not a claim that the candidate core can yet revive itself.

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

The generic core does not parse the tape, write a wall, speak to tmux or touch
the host guard. Those operations live behind `adapters.py`; tick receipts are
decoded at the core's text boundary in `dispatch_record.py`.

- `CanonicalRoleRegistry` reads the site's declared roles from `activity.roles`.
- `CanonicalWorker` asks `seed.tick` to create and deliver a new wake, then
  watches for that exact wake's settlement and safe idle clear.
- `CanonicalObservation` reads recorded state; `CanonicalLiveObservation`
  checks the owned renderer's live lease and current sensor.

The vocabulary is the plant's, not this package's. A wake is created by the
canonical tick, a turn is settled by the canonical settlement, and a turn is
cleared by the canonical clear. This package adds no second protocol and no
second record of what a wake was.

The resident claims a unique attempt under the dispatch lock before asking the
canonical tick for a wake. The tick refuses an existing pending wake under the
seed lock rather than redelivering it. A successful delivery binds the actual
wake number to the open dispatch. Completion requires the matching canonical
yield and idle clear; after the normal 15-second grace, clear checks the exact
settled wake under the seed lock and rotates only an observably idle mind.
A crash, ambiguous receipt, missing settlement or clear, or timeout remains
open for reconciliation. A crash before the wake number is bound still leaves
`started`. Reconciliation requires the canonical mind to be observably idle.

The resident checks the live sensor rather than requiring a recently changed
feed observation: missing, stale, failed, RED and UNKNOWN evidence still hold.
The tick checks the sensor again before creating a wake. An effect-free idle
poll restores the prior dispatch record, so idle polls do not grow key history;
a crash before restoration retains the open claim. Actual work keys remain

`STALE PEND: GREEN` is the healthy witness label; other `STALE` lines are failing
or stale evidence. A busy probe before any wake is a skippable poll, not an
open dispatch needing reconciliation.
useful wake, settlement or tested backout; do not use it for live migration on
that basis alone.


Every import of the plant's machinery happens inside a function in
`adapters.py`, so importing `mishe_tauftauf.core` does not load the tape
parser, the wall writer, the tmux layer or the effect ledger. `tests/core/
test_boundaries.py` enforces both halves of that rule.

## The rules the supervisor enforces

1. **Only a declared role is dispatched.** The site decides what "declared"
   means; the core reads that decision and refuses everything else.
2. **One core dispatch at a time, across all roles.** The check and claim are
   under one file lock, so two core processes cannot both start a turn.
   Other plant dispatchers do not acquire this lock.
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
