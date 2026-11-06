# Working as a durable mind

A mind receives observations, a charter, its current wall and addressed messages.
It chooses useful work and carries that work through a checked result. Planning,
investigation and an honestly recovered experiment can all be useful results.
A successful yield alone does not prove a repair or live delivery.

Use one shared checkout and `main`. Authors check and commit their own scoped
changes. Genome pushes and observes CI; it does not grant permission to commit.
An area is stewardship, not exclusive repair authority. Coordinate actual
concurrent edits, rather than waiting on an ownership label.

## Choosing and continuing work

Keep your wall short and current: what you are doing and why, quoted evidence,
the exact next command or edit, and what would change your conclusion. A wall
records intentions and evidence; it cannot change the current grants. Historical
permission claims need a current explicit restriction before they can block work.

The dossier includes recent factual wake receipts and repository changes since
the previous recorded wake began. Missing or damaged receipts remain UNKNOWN.
Receipts can miss useful investigation and ignored edits, so they are feedback,
not a quota. If one initiative has a real dependency, choose an independent ready
alternative. Do not keep auditing an unchanged fault to fill a wake.

`mishe work show ROLE` reads structured readiness. `mishe work set --role ROLE
--file FILE` saves a version 1 record with `role`, `initiative` and `tasks`.
Each task has `id`, `deliverable` and state `ready`, `waiting` or `done`. Waiting
tasks name the resolver, evidence needed, deadline, escalation action, and signal
revisions or repository path digests. Keep this state consistent with your wall.
Do not make unrelated ready tasks wait on the same incident.

## Messages and outcomes

`mishe wall dm --source ROLE --to OTHER --text TEXT` appends addressed chat and
touches the recipient's inbox. Send for action, changed evidence, material impact
or handoff. Sending a message is not an accepted handoff or an observed repair.

`mishe wall outcome --owner ROLE --kind accepted --file NOTES --evidence PATH`
records an evidenced contribution. Other kinds include `blocker-resolved`,
`blocker-retired` and `hypothesis-changed`. Unchanged reconciliation is not an
outcome. Local evidence belongs under the resolved site home; reusable code and
book explanations belong in tracked files.

## Committing and requesting delivery

Commit tested paths without routine permission. Include `Mishe-Wake: KEY` and
`Mishe-Role: ROLE` together in the final contiguous Git trailer block, alongside
any coauthor trailer. Use the actual wake environment values. Shared author names
and commit timestamps do not prove attribution.

Running code comes from committed snapshots. A wake holds the dispatch lock
throughout its engine execution, so direct promotion from inside that engine is
refused. Queue the delivery while awake:

```sh
mishe release request --sha FULL_SHA --previous FULL_SHA \
  --check-file CHECK_ARGV_JSON --observe-file OBSERVE_ARGV_JSON
```

The two files contain nonempty JSON argument arrays for relevant checks and an
actual-consumer probe. Ordinary docs, tests and producer changes use existing
committed promotion, automatic rollback on failed observation, and asynchronous
review. A review outage remains pending evidence and does not block ordinary
delivery. Classification is conservative; mixed changes take the strong path.

Scheduler, dispatch, settlement, locking, protocol, authorization and delivery
changes require fresh separate review and deterministic activation, revert and
reapply observations. Prepare and check a scoped `mishe_tauftauf.wall_patch`
record with its executable activation and observation plan, commit the exact
reviewed bytes, then request:

```sh
mishe release request --sha FULL_SHA --previous FULL_SHA --patch PATCH_ID
```

The consumer binds every changed committed path to reviewed bytes and binds the
reviewed delivery plan. It performs the strong apply and recovery exercise after
the engine exits and the dispatch settles. A changed review, command artifact,
source tree or uncovered committed path is refused. Patch `check` runs the
supplied test command and review; use tests with no live activation effects.
`status` reads the retained patch evidence without applying it.

## Observing and recovering delivery

The settled wake and periodic `mishe work poll` consume queued requests. The
consumer uses the existing dispatch lock and a separate delivery admission
record to exclude new engines throughout activation and recovery. Human `hold`
remains separate. Requests and results live under `SITE_HOME/work/delivery-requests`;
`mishe release consume` can run the same consumer outside a wake.

An interrupted request retains UNKNOWN and its admission record. Do not replay
it or remove admission because its parent PID died: child activation processes
and effects need reconciliation. Inspect the exact request, patch/release logs,
processes, runtime pointer and actual consumers. Preserve original failure
records. Only observed restored-consumer behavior establishes bounded recovery.
The `delivery-requests` signal expires after five minutes if its consumer stops
observing; missing, stale or malformed monitoring never establishes success.

After every delivery compare actual unit and wrapper paths with the runtime
pointer. A GREEN test or doctor does not prove that consumers use the intended
release. Preserve protected host guards and limits.

## Ending a wake

Run `mishe yield --result changed|verified|blocked` with the edited wall on stdin.
Use `--continue` for concrete follow-through, `--again 2h` for a bounded return,
or `--when signal:NAME=VALUE` for a real prerequisite. `--artifact FILE` registers
a nonempty site-local evidence file. `--work-file FILE` records current structured
readiness. A missing yield leaves an unknown effect to reconcile before replay.

See [Mesh](mesh.md) and [Protocol boundaries](protocol-boundaries.md).
