# Mishe core

The dispatch core is the small, generic part of the runtime that decides whether a role
may be given a turn and keeps the durable record of what happened to it. A mind
contributes judgment; durable records, bounded execution and independent observation
let work continue across interruptions. Recovery must distinguish a problem that calls
for investigation from an uncertain effect that must be reconciled before an action is
repeated.

It lives inside this repository, on `main`, next to the code it serves.

## What is here

| Module | Owns |
| --- | --- |
| `interfaces.py` | The generic contracts: `Dispatch`, `WorkerResult`, `Observation`, `RoleRegistry`, `Worker`. No Mishe types. |
| `registry.py` | Which roles may be dispatched, and the check that the registry itself is sound. |
| `state.py`, `dispatch_record.py` | The durable dispatch record: one file per role, written atomically, decoded at one boundary. |
| `supervisor.py` | The ordering and refusal rules: declared roles only, one dispatch at a time, no replay. |
| `restore_boundary.py`, `restore_request.py` | The offline, signed-request boundary for restoring the host's containment latch. |

The wake runner (`wake.py`) is the one caller: it hands the supervisor a worker that
builds a dossier and runs an engine, and the supervisor records what came back.

## The rules the supervisor enforces

1. **Only a declared role is dispatched.** The site decides what "declared" means (a role
   is a charter that exists); the core refuses everything else.
2. **One dispatch at a time, across all roles.** The check and the claim are under one file
   lock held for the whole run, so two processes cannot both start a turn.
3. **A dispatch whose outcome was not recorded stays open** (`started` or `unknown`) and
   blocks the next attempt, because its effect may have happened.
4. **An open dispatch is reconciled, never retried.** `reconcile` closes it only after
   something checked what it did, and refuses while the recorded worker is still alive.
5. **A key names one piece of work, for the whole history of the role.** A used key cannot
   be dispatched again, so a replay needs a new key and an explicit reason.
6. **A malformed record is an error, not an absent record.** Unreadable state is never
   treated as "nothing happened".
7. **Missing observation is not a pass.** With no observation source the supervisor reads
   UNKNOWN.

## The boundary

The generic core imports nothing from the rest of the package at module scope, so it can be
read, tested and reasoned about alone. `tests/core/` holds its tests; they use no plant, no
tmux and no provider, and `test_boundaries.py` enforces the import rule.
