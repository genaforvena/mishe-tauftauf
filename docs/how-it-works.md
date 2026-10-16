# How it works

Mishe-tauftauf is a local culture for development through self-observation. Python programs collect evidence, render text panes, preserve walls, and supervise installed coding agents. The agents observe their work and its evidence, choose a bounded next step, and check the result—including changes to their own development machinery. The current runtime is planted alongside an owned Git worktree; that is its storage and delivery substrate, not a limit on the work's goals. The metaphor is a plant. The machinery is processes, files, and tmux.

[README](../README.md) · [Mesh](mesh.md) · [Getting started](getting-started.md) · [Operating the plant](operating.md) · [Wall coordination](wall-coordination.md)

Jump to: [vocabulary](#the-vocabulary-without-the-potting-soil) ·
[runtime](#what-actually-runs) ·
[roles](#the-initial-channels) ·
[evidence](#evidence-unknown-is-not-pass) ·
[durable work](#where-work-survives) ·
[authority](#scope-and-authority) ·
[case study](#case-study-a-false-ci-unknown).

## The vocabulary, without the potting soil

| Term | Concrete meaning |
| --- | --- |
| **Plant** | One running local instance: its tmux session, resident agents, observation programs, supervisor processes, and saved state. Planting initializes that instance around a worktree. |
| **Site** | The ignored directory holding an instance's state, commonly `.mishe-seed/` in the kernel checkout or `.mishe-tauftauf/` in another target. `SITE` in commands means the actual directory printed during planting. |
| **Channel** | A responsibility with a named tmux window, checks, charter, and resident agent. The initial resident channels are listed below. |
| **Mind** | An installed coding-agent process in a channel's lower pane, such as Codex or OMP. It is not a separate sentience or a replacement for the host's access controls. |
| **Charter** | A channel's lasting purpose and owned scope, saved in `SITE/charters/ROLE.md`. |
| **Wall** | The mind's short, current page of plate, findings and next action, saved in `SITE/walls/ROLE.md`; `wall write` replaces it. |
| **Handoff** | The current task, evidence, uncertain effects, and exact next step saved in `SITE/handoffs/ROLE.md`. `seed yield` writes it from the same text as the wall, so it mirrors the wall rather than carrying separate content, and the live seed route delivers the wall, not the handoff. |
| **Wake** | A supervisor-issued invitation to take one bounded step. Its sequence number ties the action, handoff, and receipt to an exact entry in `chat.log`. |
| **Lease** | The top pane's refresh timestamp. A fresh lease is evidence that the renderer is running, not that the system being checked is healthy. |
| **Genome** | The reusable tracked code and general rules; also the resident channel that pushes the single `main` branch and checks its CI. Local instance state is not part of this tracked genome. |
| **Sense** | A repeatable observation with a real source, freshness, an honest failure or unknown state, and a consumer. Finding a command on `PATH` is only a candidate for one. |
| **Artifact** | A saved record of source-bound observations, actions, and checks, normally under `SITE/artifacts/`. A claim in prose alone is not a checked result. |

## What actually runs

The reusable runtime lives in `src/mishe_tauftauf/`. Checkout-only host commands in `coordination/` plant instances and refresh linked sites. A full Linux setup uses Python 3.10 or newer, tmux, Git, an installed agent CLI, a user systemd manager, and GitHub CLI for CI readings. The [getting-started guide](getting-started.md) covers those prerequisites and a manual setup without resident services.

Each resident channel has two panes:

- **Upper:** a refreshing view of the channel's goal, observations, checks, and current state.
- **Lower:** the installed agent's working process, receiving supervisor wakes when it can accept a prompt.

The supervisor reads the full dashboard, checks pane liveness and the mind's readiness, and delivers a wake for useful work, a changed observation, addressed event, continuation, or quiet self-pick. It does not invent a passing result to keep the household cheerful.

The delivered prompt carries the restore instructions, a `WAKE` line naming the
wake's sequence and sensor record, the wake's own body as an `OBLIGATION` block,
and the `CHAT TRIGGER` block. The supervisor's wake entries are hidden from the
chat views a mind is handed, and the wall may still name the turn just settled,
so the `OBLIGATION` block is what names the currently owed work; a missing or
bodyless wake yields a bounded fallback rather than a fabricated obligation.

```text
local system and source
          ↓
 sampled observations → refreshing top panes
                                  ↓
                         resident minds below
                                  ↓
                     bounded action → same live check
                                  ↓
              artifact + handoff + append-only chat.log
                                  ↓
                settled idle clear → next WAKE + restore
                                  ↓
                         next bounded step
```

### Internal inference worker boundary

`inference_worker.run_worker` provides a checkout-level, one-turn subprocess
boundary, not a resident launcher or a complete internal model loop. A worker
reads one JSON request on stdin, then emits one JSON object containing
`terminal`, the native `assistant` message, and optional `wire_usage` after
the provider stream terminates. Diagnostics belong on stderr. Total runtime
and combined stdout/stderr are bounded; the isolated process group is terminated
and the direct worker is reaped on success and failure.

Only a `done` terminal with assistant `stopReason=toolUse` can reach the caller's
executor. Cancellation is checked again immediately before each call; it
cannot retract an already committed effect. Nonzero exit, missing or malformed
IPC completion, duplicate call IDs and invalid tool arguments fail closed.
Failed or incomplete native proposals remain history, not effect authority.
Native call IDs and opaque assistant fields are retained unchanged; absent
wire usage is UNKNOWN even when the provider synthesizes zero counters.

Wire observation lives in `native/wire-usage.ts`, a provider-free module with
its own `bun test` seam. It classifies both shapes a native turn can meet:
Responses-API frames (`response.created` opening, `response.completed|done|
incomplete|failed|error` terminal) and openai-completions chunks (top-level
`id`, `finish_reason`, and the trailing usage-only chunk that follows
`finish_reason` because `stream_options.include_usage` is on). A `[DONE]`
sentinel closes a stream that never sent `finish_reason`. A payload is reset at
each native payload opening. A failed payload, a malformed frame, or an
unrecognised frame type reports neither usage nor a terminal, so an unread
frame stays UNKNOWN instead of becoming a synthesized completion; usage is
reported only when the terminal id correlates with the assistant response id.

Provider authentication/configuration, durable operation identity and recovery,
and resident activation are separate integrations. This boundary does not
establish configured-model parity, safe replay, or live internal-loop delivery.

`inference_loop.drive_native` supplies bounded multi-turn orchestration over a
caller-owned session. It preserves native assistant fields and exact tool-call
IDs in the next model input. The caller must synchronously persist each input,
output, proposal and result through `record`; recording failures propagate and
stop the loop. A phase observer runs before the final cancellation and authority
eligibility checks. The executor still owns durable intent, current authority
after persistence, operation identity, exclusion and reconciliation.

Unknown or partial effects stop without retry. Repeated call IDs, ungranted
capabilities and over-budget selections cannot dispatch; final prose alone
cannot complete an obligation without the caller's independent completion
check. Turn-budget exhaustion retains the context and last observed result.
Session cleanup, callback time limits and provider transport remain caller
responsibilities. This checkout API is not a resident activation, a durable
replay mechanism or evidence of a completed live useful-task canary.

`native_story.NativeStory` provides a rendering-only terminal view for a caller
that journals first and passes it decoded dispatch outcomes. It shows visible
assistant text, actions, file paths and requested line ranges, read contents,
command stdout/stderr and exit failures. Long text blocks are bounded to 8,000
rendered characters with full-evidence references; terminal controls are escaped.
An abnormal provider terminal retains its error reason while remaining UNKNOWN.
Provider payloads, hidden reasoning and input context are not rendered.
`quiet_input` suppresses TTY wake-packet echo without changing delivery and
restores terminal settings on exit. A completed command or stopped loop is not
wake settlement: the view labels unknown effects and reports settlement only
after a completed `seed_yield` dispatch. This presentation neither authorizes
effects nor establishes replay, model parity or activation.

`story_output` appends readable output to a caller-owned transcript across process
restarts. `retain_scrollback` pushes an append-only TTY's final visible screen
into terminal history before tmux respawns it; the caller invokes it before
releasing idle readiness. It sends no input and does not reset model context.
The native caller labels fresh process/model contexts explicitly. Terminal
history has a finite configured limit; the append-only transcript preserves
the longer story without replaying it into model context.

`inference_effects.EffectBoundary` is the optional caller-owned binding for that
`dispatch` callback. Constructed with one store directory, a `writer_id`, the
obligation identity, the bound authority and the caller's capability executor,
it maps the provider's call shape onto a durable, reconcilable operation: it
allocates the intent before any effect, persists the start record before the
executor runs, and appends the outcome afterwards. The reply it returns is the
loop's expected status shape, so `completed` continues and `unknown` stops
without retry. Matching pending bindings reuse the intent; changed content is
refused. Completed allocation or binding raises `AlreadyExecuted`, even for
changed content: this callback is not an idempotent resend API. Low-level
`EffectLedger.dispatch` returns an existing outcome before fresh content
validation; use binding for dispatch and `reconcile` for recorded-result recovery.
Reconciliation never executes; absent history or an intent without an outcome
is UNKNOWN, not permission to retry. The executor must still check current
authority. `completed` records its return, not physical success or remote delivery.
The scope is one local store and writer; process-crash recovery, not power loss.

Every exception this module defines reaches the caller from dispatch instead of
the generic `unknown` / `capability-failed` reply: `AlreadyExecuted` and
`ConcurrentClaim` name durable store decisions made before any effect, so they
are re-raised like `StoreUnavailable`, `JournalCorrupt` and `ChangedContentReuse`
rather than reported as a capability that crashed mid-effect. The generic branch
is reserved for an exception that establishes nothing about whether the effect
happened, which is exactly why a store verdict must not be routed through it.

Two statuses the loop accepts are not branches of this boundary. `partial` parses
from the journal but nothing here writes it — an effect is recorded once as
`completed` or left with no outcome at all, which reads back as `unknown` — so it
is the caller's report shape, kept parseable for a caller-written outcome.
`not-started` is the caller's accounting of a call it never dispatched; here that
state is `None` from `status`/`recover`, not an outcome the store holds.

`reserve(operation_id, capability, arguments)` binds that same durable identity
before any effect and before the provider has issued a call id: it allocates the
intent with no start record, no execution and no outcome, so every reader still
reads `unreconciled-intent`. A later dispatch of the same id upgrades the
binding to the real call when the bound capability, version, arguments and
authority all match, and refuses a changed binding as `ChangedContentReuse`
before any effect. One provider call id binds at most one operation, so a
second reservation or dispatch under a call id another record already holds is
refused as `CallIdConflict` before any write, including the reservation upgrade
that would otherwise take a call id an operation already discharged. That check
reads outcomes as well as intents, because `dispatch` writes the call id onto
the outcome: a store this boundary did not write can hold an outcome naming a
call whose intent record is gone, and scanning the intents alone would let a
second operation execute for a call that already produced an effect. The resolve
lookup that names an operation by its provider call alone has one answer or
none. `drive_native` passes the provider's call through untouched
and a provider does not echo the caller's id, so `__call__(call,
claim_for=operation_id)` is the route that names it; and after a crash the retry
arrives without the claim, so the unclaimed route resolves the operation id a
record already holds for this provider call instead of deriving a fresh one and
executing the capability under it a second time. A `claim_for` naming no
operation in the store is a `ValueError`, and one whose id already has an
outcome is `AlreadyExecuted`. `drop_reserved` retires a reservation
that was provably never dispatched; it never removes a start record or an
outcome, and an unknown id is simply not reserved, not an error. This is the
surface for an operation identity fixed by an upstream contract — a reserved
report identity, a frozen artifact reference — that must exist before the
provider call that fulfils it.

`inference_loop.NativeJournal` is an optional caller-owned `record` callback:
it exclusively creates a new private JSONL file, flushes and fsyncs every event
before returning, and refuses reuse after a recording failure.
`read_native_journal` reconstructs the obligation, ordered native context,
unresolved selections (including calls not yet proposed), used call IDs,
observed turn/result counts and recorded lifetime budgets without touching effect
state. Torn history, changed context and uncorrelated results fail closed without
altering evidence. `drive_native(..., resume_from=path)` reads that history and
requires the same obligation, context and original budgets; it carries consumed
turns/results and used IDs into a new journal. Per-turn evidence follows the
continuation as well: the resumed run's checkpoint replays one wire-usage entry
per completed turn and one fetch-ledger row per attempted turn, so a failed
turn's budget row survives into the resumed journal instead of living only in
the earlier file; a ledger prefix that cannot align with the counters is
refused. Exhausted turn budgets cannot call
the provider, and remaining call budgets and reused IDs are checked before
dispatch. Pending or ambiguous history returns an UNKNOWN handoff without calling
the provider or executor. Stopped histories and older histories without recorded
budgets cannot continue through this entrypoint. A `ready` boundary is not dispatch
authority: the caller must separately restore provider state and reconcile the
original effect store under current authority. A proposal without a result
remains UNKNOWN whether or not
dispatch started. A retained stop is the previous caller's report, not independent
acceptance. This API covers single-writer local file/process-crash evidence,
not power-loss durability, automatic recovery or a deployed resident route.

To take that reading in words a reader would use: read the role dashboard with
`pain read ROLE --launcher dashboard`, then compare it with the native route's
retained per-turn journal at `SITE/journals/ROLE-native.jsonl`, which records
each turn's tool selections, result checks, wire usage and fetch dispatch count
with any preserved upstream failure. A row restored by reconciliation is
distinguishable by its event key order — `kind,turn,call,message,obligation`
rather than the uninterrupted `kind,obligation,turn,call,message` — a writer
fingerprint a reader can use to detect a reconciled row. One back-to-back run was
executed on 2026-10-15 on a single frozen source file (`inference_inspect.py`,
sha256 `89f7f33b…`): the native arm completed one `inspect` read in 26.151 s
with 4911 provider tokens and an 8-row journal carrying wire usage and a
fetch-ledger row per turn; the external arm's single `read` took 0.10 s with its
token cost UNKNOWN from outside the resident session; a second arm killed after
dispatch restored one reconciled row and left the source byte-identical. That is
author-reported evidence for one host, one route and one file, its comparison
artifact still awaits a second reader, and no parity verdict exists — the
harness route stays the fallback for this work class.

The named acceptance for that back-to-back run, so no adoption claim can
precede it: both arms read the same frozen path and digest through their own
route's bounded read of that exact path, with no extra arguments; both results
match the frozen file's content; tokens, elapsed and calls are recorded as
measured columns, not equalled, and the two routes' tool surfaces (`read` versus
`inspect`) are recorded as a difference rather than a match; a process kill
mid-run leaves the source byte-unchanged after reconcile, with the kill recorded
as the child's signal death and never as a completed run; the journal carries
per-turn selections, result checks, wire usage, fetch dispatch counts and
preserved upstream failures; a second reader checks the comparison artifact.
Resident activation, durable replay, configured-model parity, exactly-once and
live useful-task proof are outside this class and stay open. Docs is exercising the internal native loop as a bounded canary; this route
selection is not an adoption or live-delivery claim. Resident activation, replay,
model parity and useful-task acceptance remain open, and `applied` alone is not
delivery.

`inference_inspect.prepare_exact_inspection` freezes one caller-supplied absolute
file path, SHA256, byte count, source/task identity and loop budgets before a model
turn. Preflight and dispatch use the same bounded read: every path component
refuses symlinks, the target must be a stable regular UTF-8 file, and its digest
must match before any text is returned. The model can select only `inspect` with
that exact path and no extra arguments. `EffectBoundary` binds the read to the
actual caller obligation and writer, rather than a prototype identity.
`run_exact_inspection` accepts a zero-argument context-managed session factory.
It reserves an exclusive `NativeJournal` before calling the factory and keeps
the same writer open through construction, `drive_native` and session exit.
Construction must be deferred into the factory; wrapping an already-created
session cannot protect startup. Reusing a prepared run refuses before factory
invocation. Failed construction retains an empty reservation, not a readable
checkpoint or permission to retry; reconcile it rather than deleting it.
An evidence change after dispatch starts remains UNKNOWN even if bytes are later
restored. The caller owns trusted unchanged manifest/executor objects, admission,
session lifetime/output/time limits and the independent completion oracle.
The recorded selector is intended-model metadata, not proof of the session's provider choice.
There is no automatic recovery, hostile-caller protection or OS sandbox here;
synthetic source checks do not establish a deployed resident consumer.

`inference_transport.NativeSession` implements a caller-owned sequential IPC
session for that loop. It opens a worker with a model selector and session ID,
correlates numbered turns, and retains raw terminal provenance in `NativeTurn`.
A lifetime deadline and cumulative stdout/stderr limit apply across exchanges.
Concurrent calls are refused. Failed exchanges terminate the isolated process
group, reap the direct worker and prohibit reuse; they never retry an effect.
If stdout closes before a receipt, the transport drains remaining refusal
diagnostics and waits for worker exit only within the original lifetime deadline.
Timeout and cancellation retain their primary cause and append at most the final
4096 already-buffered stderr bytes, with a truncation marker when needed; they
do not extend the deadline to collect more output.

Use the session as a context manager. Normal disposal requires both a `closed`
receipt and a clean worker exit; a successful earlier turn cannot certify
successful cleanup. Cancellation and the time/output limits apply during the
receipt exchange, but the post-receipt exit wait is bounded by `close_grace`
instead: a receipt already names the outcome, so a worker still alive when that
bound elapses is a disposal failure, never a reclassified timeout. Deadline
checks occur during API calls, not in an idle watchdog.
The caller still supplies the provider worker, current authority, durable
effects and recovery. This transport does not activate a resident or establish
credential/account parity.

The open request forwards an explicit `reasoning` effort to the provider
(`NativeSession(..., reasoning=...)`, default `medium`). A route may refuse the
requested effort before any turn: measured 2026-10-15 on
`opencode-go/deepseek-v4.1-flash`, the default `medium` is rejected
(`Thinking effort medium is not supported by opencode-go/deepseek-v4.1-flash.
Supported efforts: low, high, max`) and the session fails closed — a
`model_failure` row with a null fetch ledger, no completion and no retry. A
native-route caller on such a route must name a supported effort explicitly.
Evidence: wake 43522,
`artifacts/inference-research-wake43522-exp2-allowance-replication.md`.

The packaged native provider session permits at most one underlying `fetch`
dispatch per turn by default, including lower-level HTTP retries and encoding
fallback. The bound is the named constant `DEFAULT_TURN_FETCH_ALLOWANCE` in
`native/fetch-budget.ts`; resolving a different allowance grants automatic
retry authority inside a turn and is a contract change, not a tuning knob.
An attempted dispatch past the allowance poisons the session and raises
`native provider fetch budget exhausted`, naming the upstream failure that
preceded it when there was one; provider cancellation normalization cannot
replace that diagnostic. The failed turn still fails closed, yet its claims
are durable evidence: the worker emits the original diagnostic together with
the budget row for the dispatches already made before exiting, so a caller
journal records one ledger row per attempted turn rather than per completed
turn. An ordinary error terminal remains an error, and a
later caller-authorized turn has its own default allowance. This is not broad
automatic retry authority, an output-token cap, a spend guarantee or proof that
the server processed only one request.
One narrow exception exists, as an authorized experiment and not as a parity
claim: the read-only CI-observation work class
(`experiment-2-read-only-ci-observation`) may resolve an allowance of at most
two (`MAX_TURN_FETCH_ALLOWANCE`) for its frozen `ci_status` tool set, a
broader allowance, another work class or a half-specified pair is rejected
rather than coerced to the default, and a granted allowance still cannot carry
another capability inside the same turn context. A frozen-input live series has
now been run twice for that read-only CI-observation class (October 2026,
allowance two, one named `ci_status` capability). The first window (three
samples) drew the real intermittent upstream 429 on every sample and exhausted
both permitted fetches, failing closed while the durable record keeps the
exhaustion and the preserved upstream failure distinct. The second window (six
samples) drew the transient on four: three again exhausted and failed closed,
and one survived it — the turn-1 ledger reports `dispatches: 2` with the
preserved 429, the transport's transient retry succeeded, and the turn
completed holding exactly one `ci_status` proposal and one tool result. A
survived 429 was therefore UNKNOWN after the first window and is now observed
live once; before that, only the deterministic seam test covered the path.
Counted per attempt rather than per registered sample, the second window
holds seven attempts: an operator-preserved re-run of sample s1
(`sample-s1-firstattempt`) also drew the 429 on its completed turn 0 — the
same `dispatches: 2` ledger, the same oracle-matching `ci_status` result —
and then exhausted on turn 1 and was retired for the registered s1 re-run,
so no committed driver names it. Completed-turn survivors are therefore two
against one completed sample, and the window's attempt-level truth is five
transients and 17 provider claims where the six-final summary reports one
and 7 — a measured under-report, not a claim difference. The
turn frame carries a fetch ledger (`allowance`, `workClass`, `dispatches`,
`upstream_failure`) that `read_native_journal` surfaces as one row per
attempted turn — the failed turn's row rides its `model_failure` record — an
absent ledger staying UNKNOWN rather than a synthesized zero. `resume_from`
carries the prior turns' rows into the new journal's checkpoint, so a chain of
journals keeps one row per attempted turn end to end. Nine registered
samples across two windows (three plus six, per the two rerun reports) on one
route under one upstream-owned condition, with one surviving sample, is not a
statistical claim, and terminal-status parity under
the 429 condition stays unasserted: the candidate fails closed on three of
six where the harness oracle read the frozen run successfully. That class is not this plant's Docs canary: the bounded native loop is
being exercised for Docs, but this evidence does not establish adoption or live
delivery. Resident activation, replay, model parity and useful-task acceptance
remain open; the harness route stays fallback for work classes without an
evidenced native route.
The transport retries transient 408/429/5xx itself
(`DEFAULT_MAX_ATTEMPTS`, six requests per dispatch), so on a route that answers
one such transient with a second POST the diagnostic names the budget rather
than the upstream status; the completions route to `opencode-go` was measured
returning such a 429 (wake 33779). That is a known zero-tolerance interaction
on this route, not a silent retry — it is the measured failure the narrow
allowance exists to test. `drive_native` persists a `model_failure`
with the original `WorkerError` type and diagnostic, plus the worker's
per-turn fetch ledger row when it emitted one, before propagating the
exception. Budget exhaustion, cancellation and time limits retain their distinct
transport diagnostics; none supplies a model completion or authorizes replay.
Journal reconstruction preserves the failure through a recovery checkpoint,
keeps the input UNKNOWN, and never opens a provider session for that input.
If recording fails, that error propagates and the retained pending input still
remains unresolved. Older journals without a failure record remain UNKNOWN.

For pinned Codex full-history SSE, each correlated `response.completed` also
returns a logical checkpoint in the same `NativeTurn` that the caller journal
records as `model_output`. A fresh `NativeSession(..., checkpoint=...)` sends it
only on its first turn. The packaged worker rejects assistant/tool-result
continuation without a checkpoint, changed context, incomplete or mismatched
results, duplicate original call IDs and occupied provider state before resolving
credentials for the turn. Import requires the exact provider source digest and
restores only session/thread/window/turn identity and turn-start time into a new
factory-owned container. It is not a supported upstream state API.

Checkpoints do not restore account, installation, routing, sockets, compaction,
effort-transition history or effect authority. No compaction/history rewriting is
supported across this boundary. The caller must supply an immutable, singly owned
completed-result history and reconcile original effects before continuation;
the transport neither selects nor authenticates a journal. Error/incomplete
responses retain diagnostics but do not establish a recovery checkpoint.
A correlated receipt can also arrive after its worker has exited, in which case
`turn()` returns the terminal `transport-receipt-after-exit` instead of `done`:
the assistant text is kept and the caller owns reconciling a provider it can no
longer continue. The loop stops `unknown` without dispatching under it, because
that terminal proves a selection without proving the provider state it was made
from is still live. The `model_output` row keeps the terminal, so a fresh process
can branch on it where the recorded `stop` status alone is ambiguous: `unknown`
covers nine loop causes a reconciling caller may need to tell apart.

`inference_loop.recover_native(path, open_session=..., ...)` selects the logical
checkpoint, exact completed results and original budgets from that caller journal,
then opens a fresh session through `open_session(checkpoint=...)`. The factory must
return a context manager. Pending or ambiguous histories and exhausted turn budgets
never open a session; missing checkpoints refuse before the factory. A stopped
journal is refused too — including one stopped at `transport-receipt-after-exit`,
whose receipt from an exited worker cannot certify the provider state the caller
means to continue. That terminal leaves the journal's used call IDs empty —
dispatch never ran — unlike a dispatch exception, which leaves an open intent
a successor must reconcile. Continuation seeds retain the checkpoint across
interruption before the next model input.
There is no caller-supplied checkpoint or result override. Journal ownership and
current original-effect authority remain caller obligations; this does not
authenticate edited journal bytes or establish live resident recovery.

`inference_inspect.reconcile_caller_journal(path, boundary, authorized=...)`
reconciles a proposed call against its original `EffectBoundary` without
dispatching. It appends a `tool_result` only for a completed recorded outcome
while current authority still passes; missing or unknown outcomes and revoked
authority leave the proposal pending. `run_exact_inspection` invokes this after
its journal writer closes, including an ordinary exception after dispatch.
Abrupt process death skips that cleanup, so a fresh caller must invoke the
reconciliation helper before deciding whether the call remains unresolved.
Stopped journals, unproposed selections and calls with an already recorded
result are not modified. This is process-crash recovery, not power-loss
durability or exactly-once execution.

The wheel includes the native TypeScript worker, session and auth bootstrap.
`inference_native.native_worker_command(bun=..., node_modules=...,
staging_parent=...)` stages those sources in a temporary owned directory with
an adjacent link to caller-provisioned dependencies. Nest `NativeSession` inside
that context so the worker is closed and reaped before staging is removed.
The command disables Bun auto-install and `.env` loading; no dependencies or
credentials are bundled. The caller supplies trusted, pinned, unchanged runtime
paths (measured with Bun 1.4.2 and OMP packages 18.4.4), and supplies the session
environment and working directory explicitly. `NODE_PATH` is not sufficient.
Bootstrap uses OMP's read-only settings, effective account policy, broker-aware
auth discovery and exact model lookup. It closes returned auth after provider
session disposal. Failure before auth discovery returns remains a dependency
cleanup gap; fatal process containment is not proof of resource cleanup.
Installed-wheel synthetic continuation is not real account/model parity or
resident adoption. Docs is exercising a bounded native canary; preserve the
harness route for work classes without an evidenced native route.

### Development is not just repository maintenance

The generic runtime provides the observation, wall, and handoff loop. A project's charter supplies the goal; its local checks make progress and uncertainty observable. Builds, Git state, and CI are useful checks, but they are not a universal definition of progress.

For a research project, the next step may be to audit a dataset, run an authorized evaluation, compare experimental conditions, trace a result to its provenance, or hold a conclusion for independent review. Evidence can reveal a flaw in the method or the plan, not just a bug in the code. The loop also observes itself: stale displays, repeated no-progress work, or misleading checks are development problems in their own right.

The [tiny-fleet](https://github.com/genaforvena/tiny-fleet) plant illustrates this distinction. In the live instance inspected on 30 September 2026, its project-specific genome pane tracked audit, prioritization, reproduction, reporting, and independent review. Saved evaluation outputs were visible, but unresolved provenance and review obligations kept research acceptance `UNKNOWN`. Its health channel separately reported passing local infrastructure checks. Healthy machinery did not certify the science.

The senses channel also recorded a mismatch between a current research view and an older embedded sample. That was evidence about the observation machinery itself, not a new experimental finding or proof that the mismatch had been repaired. The live panes, status file, and handoffs support this point-in-time example; it is not a claim of research completion. The plant's local evidence is ignored state, not bundled with the public repository.

Project-specific checks and charters make this possible; the stock runtime does not arrive knowing how to judge an experiment. Keep authority bounded to the owned project, and distinguish a recorded output from a justified conclusion.

## The initial channels

| Window | What the upper pane shows | What the lower pane does |
| --- | --- | --- |
| `discover` | Recent bounded local reads and capability candidates | Selects related literature, assesses applicability, and proposes evidence-backed new directions as well as local capabilities |
| `senses` | Sampled readings, freshness, and unknowns | Turns recurring observations into truthful senses with temporal validity, a failure state, and a consumer |
| `health` | Deterministic facts: CI, deployed runtime, services, pane leases, and patch state | Repairs the plant's local feed, panes, and services |
| `genome` | Deterministic facts: CI, deployed runtime, services, and patch state | Develops source, pushes the single `main` branch, and checks its CI and live consumers |
| `witness` | CI plus coordination checks: planted windows, clear-stall and stale-pend, and open tasks | Routes work, follows it through completion, and checks claims against artifacts and live results |
| `docs` | The repository's selected reader document (default `README.md`) | Keeps the README and reader docs current, deleting stale passages |
| `research-methods` | Deterministic facts: CI, deployed runtime, services, and patch state | Assesses whether experiments answer the question and helps implement bounded improvements |
| `permissions` | Pending and decided requests | Gives the operator a shell for scoped grant and revoke decisions |
| `operator` | The human owner's shell | A convenience for the person; not a role and not a gate on work |

A site can declare additional resident channels beyond the built-in channels by adding
a charter and an executable launcher. The plant preserves that window, refreshes
its evidence pane, and counts the resident's activity toward the silence watch;
the resident's wake service is site-owned rather than generated.

**discover → senses → health → genome → witness** describes responsibilities: find an observable, make it reliable, maintain the local substrate, improve reusable code, and independently check completion. It is not a mandatory route for every task. A fault goes to its owner; authors commit their own scoped work on the single `main` branch, and genome pushes it and checks CI and rollout. Witness keeps unresolved obligations visible. `docs` keeps the repository's README and reader docs current. A person can attach to the same text session and read the same tape.

Roles are not file restrictions. Within owned scope, every mind may repair Mishe's source, checks, prompts, doctrine, charters, roles, routing, supervisor lifecycle, planting, or coordination when evidence requires it. Path ownership, independent review, acceptance checks, and external boundaries still apply.

Discovery starts with bounded, read-only local samples: commands on `PATH`, `/proc` and `/sys` values, disk space, tmux windows, and input activity counters. Input activity counts do not capture key content. Each discovery entry explains available commands, verified readings, unknowns, the artifact, and the next action. Repeated scans refresh local evidence but append chat only when availability, status, or an unknown reason changes. Discover and senses wake for those meaningful changes, not every moving counter.

The discover and senses supervisor ticks renew a missing, incompatible, or
uncertain scan, or one whose conservative acquisition age exceeds 600 seconds.
Each persisted receipt binds its observations and scan ID to start/end
`CLOCK_BOOTTIME` brackets, boot ID, time namespace, and namespace offsets.
Compatible age bounds are recent only when their upper bound is at most the
threshold; they are stale when their lower bound exceeds it, and otherwise
UNKNOWN. The panes use 900 seconds. Legacy UTC-only receipts remain historical
and UNKNOWN until renewed; wall-clock endpoint consistency is shown separately,
not used to establish freshness.

This bounded read runs under the seed lock even when the upper pane is
unavailable or stopped; other channels do not renew it. An unchanged scan does
not append another discovery event. `discovery/attempt.json` exposes acquisition,
publication, and notification failures separately from `latest.json`: a failed
acquisition or pre-commit publication retains the previous sample; a failed
notification retains the newly committed sample but cannot claim completed
delivery. Historical scan artifacts are preserved.
A `running` record is a scan in flight, not a failure: the pane's sample is the
last published snapshot, so the STATE line holds and the `ATTEMPT` line names
the live stage outside the change digest. Only a recorded `failed` attempt
flips the pane to UNKNOWN, and an unreadable or absent attempt record is its own
UNKNOWN rather than a silent success.
Every receipt also records its `producer`: the package root and commit that ran
the scan. The panes' `SCAN` line and the tape's `Producer` line name it against
the site pin (`pin@<sha>`, `checkout@<sha>`, `other@<sha>`, or `unknown`), so a
scan produced by unreviewed checkout code is visible rather than read as the
pinned sample.


Acquisition freshness does not refresh cached or imported source evidence.
Journal counts retain their original fixed `since`/`until` bounds, count, boot,
and source metadata in both panes; they are not a sliding “last ten minutes”
claim at render time. Missing bounds and cached/imported source validity remain
UNKNOWN independently of a recent enclosing scan.

Local scans are only one input to discovery. Discover also selects neighboring topics
from literature, reads relevant primary sources, and assesses their mechanisms,
evidence, assumptions, and limits against the project. It records what transfers,
what does not, and what needs an experiment, with citations and the sections read.
Its outcome can be a new question, a bounded experiment with a predicted result
and acceptance check, or a reasoned rejection—not necessarily a new sensor or a
fix for an existing fault. A green scan does not establish research progress.
Literature is occasional, not a per-wake quota, and can inform any aspect of Mishe
or its project goals: coordination, self-organization, learning, evaluation,
experimental methods, or agent behavior. Discover chooses when outside work is
useful and applies supported ideas through checked experiments or owned changes;
it does not force papers into unrelated tasks.

A sense has explicit temporal validity: sample time, the interval or event covered,
and the age or source change that makes it unsuitable for the consumer's decision.
It need not stream continuously. An expired reading can remain valid historical
evidence while being UNKNOWN for a current-state claim. A paper read to explore
an idea is discovery; a repeatable watch for literature updates can be a sense.

New senses can also cross several existing ones. The composite defines its inputs,
combination rule, inferred state, and consumer, aligns the observation windows,
and preserves input provenance and validity. A new output timestamp does not
refresh stale inputs; missing evidence stays UNKNOWN where it prevents the
inference. Compare against each input alone and account for correlation before
claiming new information—for example, testing an overload hypothesis from queue
growth, throughput, and memory pressure together.

## Evidence: UNKNOWN is not pass

A missing, stale, unavailable, or conflicting reading is `UNKNOWN`. That means the result cannot be established. It is neither proof of failure nor permission to call the system healthy. A fresh lease beside an unknown check gives you a functioning display of uncertainty.

A cross-site runtime-drift reading checks services attributable to linked-site
sessions and the scanning site's session against their pins. It reads configured
`PYTHONPATH` from systemd, falling back to the live main process environment and
then its direct children for roots exported by wrapper scripts. A stale covered
root is `drift`; incomplete coverage — an unattributed running unit, a unit of a
site with no pin, or a unit whose import root cannot be discovered — is
`unknown`, not `verified`. Its sample retains the `unattributed`, `uninspectable`
and `unpinned` unit lists so readers can distinguish coverage gaps from drift.

A unit no session names that imports this plant's checkout — observed in its
environment or declared in the script its `ExecStart` runs — is named
`foreign=<unit>@<checkout>` instead of left anonymous, and stays `unknown`,
never `verified`, because a foreign consumer of the moving source is a live
coupling rather than a coverage gap. Systemd's own `Description` of the unit
outranks the script rule: a unit whose description names this checkout belongs
to this plant, so the class rests on one `systemctl` read succeeding. Measured
here, the cleaner coordinator read
`foreign=mishe-cleaner-recovered.service@mishe-tauftauf` at 09:17:20Z and
`unattributed=` at 09:16:46Z and again at 09:27:28Z — its `ExecStart` script
names the checkout while its description (`mishe-tauftauf report-only cleaner
coordinator`) attributes it to the plant, and those two answers are that one
guard apart. Reproducible by reading the unit's declaration: with the
description readable the unit is not a consumer, and with the description
unreadable it is named one.

An unattributed unit whose imports and whose own declaration — `ExecStart`,
`WorkingDirectory` and `Environment` paths — name no site's checkout or home is
a host tool beside the sites: it is named `host-scope=<unit>` and the reading
stays `verified`, because the comparison is settled for it. A unit whose
declaration cannot be read, or whose declared paths do name a site, remains
`unattributed` and the reading remains `unknown`.

The kernel journal sense counts readable priority `err` or higher kernel entries
in a fixed ten-minute UTC window, not message lines, independent faults or a
rate. Its coverage records endpoints, boot identity and monotonic acquisition
bounds. Failed, incomplete or invalid acquisition is UNKNOWN, with no last-N
fallback. A verified zero does not establish journal retention or permission
completeness. The window is historical evidence; current decisions need a fresh
scan, and a boot or source change invalidates comparisons.

The plant's Note3 light reading consumes an atomic `SITE/body/space.json`
boundary written by the phone-side publisher; it never starts a second
collector. It keeps lux, provenance (source and session) and the original sample
and receipt times visible. Freshness is capped at 30 seconds or the producer's
shorter validity and is measured on the host side: the read compares the
consumer receipt, publication and poll stamps, never the phone's event epoch,
because the phone clock runs behind the host by design. The measured quantity
is a span — host arrival minus phone stamp, sampled in this plant as 25.3–29.0 s
over 75 spans — and both latency and skew are non-negative, so the skew is
bounded above by the smallest span, not read directly (DM 34981).
Polling or rendering cannot extend it, and missing, broken, delayed or expired
evidence is UNKNOWN. A last sample older than the cap reads UNKNOWN as
`stale-original-evidence` even when every layer works: measured on this plant
at 2026-10-11T03:06Z, a verified sample took 23 seconds from phone collection
to consumer receipt and 26 seconds to the read, against the 30-second cap, so
the margin is seconds rather than minutes. The same seconds-wide margin once
produced that class from the clock itself rather than from a stalled producer:
while the phone epoch sat inside the comparison, a healthy feed read
`stale-original-evidence` on 29% of polls, worst case 33.45 seconds against
the cap (the measurement recorded by `dc1b963`, which excluded it). The
exclusion is source behaviour, and a pane loading an older pin still carries
the previous rule, so one boundary file can read `verified` from the checkout
and `unknown stale-original-evidence` from the pin, as measured on
2026-10-13T13:49Z. A published sample without a lux
value reads UNKNOWN as its own reason (`light-null-gap`), so a dropout stays
distinguishable from ordinary aging and from a corrupt boundary. The null is a
producer-side projection rule, not a phone value, verified read-only at the live
publisher's source on 2026-10-11T07:46Z (discover, wake 7782): the publisher
writes `lux=None` whenever the light status is not fresh, and freshness compares
the phone's event time and its own age against the host clock under the same
30-second cap. The publisher's own journal carried the phone's real values
through the whole measured episode window (101 of 105 rows, 05:30–06:30Z), so
the steady-regime null share is the tail of the standing receipt lag — itself
23–26 s against the cap, measured 2026-10-11T03:06Z — crossing the cap, not a
silence of the
phone's sensor. Two source-side
gap widths are measured: short dropouts of a few seconds to tens of seconds,
which the light channel's next sample resolves, and a sustained phone-silent
freeze, where no new light sample arrives while the producer keeps writing and
the transport stays reachable. One complete freeze instance was first measured
on this plant: the last phone sample before it stopped was 2026-10-11T00:47:31Z
and the stream resumed with a sample at 02:19:46Z, a 1h32m15s stoppage with a
live transport throughout (the producer reconnected at 02:19:41Z reporting
`light unavailable`, so the reconnect is not the first sample). The family has
since grown: an overnight stoppage of 10h44m46s
(2026-10-13T15:04:02Z to 2026-10-14T01:48:48Z) with continuous uptime clocks,
and the live one (last phone sample 2026-10-14T06:13:24Z) stopped acceleration,
light, BLE and Wi-Fi within one batch while the phone was awake with its display
on, which localizes that instance to the phone's producer app rather than the
link or the host (n=1; phone-side detection still blocked by the freeze). Which
phone-side layer stops publishing, and why, is still not measured; a
live transport and a live writer already rule out the link as the cause, and the
134-second stoppage measured below is no more decisive, because a stopped sensor
pipeline and a stopped publisher process look identical at the boundary. When
the same absent light sample and unmoving light sequence persist across two of
the reader's own null observations at least a minute apart, the reading names
`light-stream-frozen`; the first null observation of a stream reads
`light-null-gap`, and publication or polling never converts one into the other.
The notification-time re-read applies the same class to the boundary it
rechecks and, like the scan read, never renews the streak's origin, so the
notification cadence cannot hold the measured interval under that minute. A
stream that moves between the two names is reported as a gap duration class
change, not as a light transition and not as an availability baseline.
An absent light sample is only a *frozen light stream* while the producer
declares it is sending. The node's additive `node_state` field carries the
producer's own bounded verdict — the mode it read from the device's app
preference — and the reader decodes only that enum: a verdict whose status is
not `ok` is absent, stale or invalid and declares nothing, `ON_SENDING` and
`UNKNOWN` declare nothing, and `ON_COLLECT_ONLY`, `ON_NOT_RUNNING` and `OFF`
each name the reading `collect-only`, `producer-not-running` or `producer-off`.
A declared silence is not a freeze: it names no duration class, records no
onset and routes no event, and it is named from the first null read rather than
after a minute. A boundary without the field — an older producer, or one whose
verdict is stale — keeps the reader's own name, so a reader can never claim a
configured silence it could not read. The mode enum is the producer's
declaration about delivery, not about the sensor: a `collect-only` node's light
capability stays unmeasured rather than reported as stopped.

Redmi10 is read through the linked Android Body site's retained
`body/perception/redmi10.json`; core senses do not duplicate the phone collector
or post rows to the shared chat. A decoder checks the linked-site and consumer
schema, node, source, session, cursor and per-channel receipt metadata before it
publishes `sense.body.redmi10`. Battery, light and acceleration are current
only when their host consumer receipts (and any sensor-age field) are within
30 seconds and the consumer is not in error or draining a backlog. The Senses
pane re-reads this local file on each render and renders the carried-body rows
from that live read alone; the ordinary discovery scan stores its own bounded
reading for later resident analysis and change notification, and that stored
copy never becomes a second pane row. Missing, malformed,
future or stale receipts remain UNKNOWN rather than reusing a last-known value.
The line preserves phone-sample time separately from consumer receipt time;
without a clock-domain measurement their split is unknown. Wi-Fi RSSI is only a
connected-link reading, not proximity. The last BLE scan error remains visible
as stale evidence, not a current permission diagnosis. Acceleration magnitude
includes gravity; it is device evidence, not a human movement, presence or
continuous-carrying claim. The human's carrying context remains contextual, not
sensor-certified.

The source cursor remains in structured provenance and the pane's source-limits detail.
The served pin's `UNKNOWN sense.body.redmi10` row can also expose its cursor.
The checkout redacts that cursor token before hashing (`be8ec0b`), and its
producer sample no longer carries one (`522f6da`), so on checkout bytes the
digest-bound pane row holds no cursor token and the cursor-redaction rule is
a redundant guard. That is a loaded-bytes question, not a source one: the
served pin `e8405b8` predates
both changes and still hashes the advancing cursor and the row's ambient-light
reading, so cursor-only and lux-only advances both change the pin's digest. New
sensor values, session changes and failure/recovery transitions remain visible
either way, and the served pin's live pane row still shows the advancing cursor.

Host USB attachment is deliberately *not* part of this row. The Redmi10
onboarding notes describe a USB-loopback setup, and it is tempting to read the
kernel's USB tree (or `adb devices`) as a device-present/device-gone verdict,
but that premise was measured false on 2026-10-16: at 13:2xZ no Xiaomi
interface was on the bus and `adb devices` listed only the Note3, while
`redmi10` samples arrived fresh (phone sample 13:23:57Z, consumer receipt
13:24:20Z, advancing cursor). The tailnet HTTPS rendezvous
(`tailscale serve`, live since 07:57Z that day) gives the phone a path with no
USB at all, so a missing interface proves nothing about delivery, and it was
live throughout that morning's four-hour silence. A USB-derived "absent" would
therefore be a false claim, and in the ordinary untethered configuration it
would be an `unknown` token that pushes the channel states out of the clipped
pane. The row keeps the honest verdict it can support: a stalled feed is
`channel-not-current`, with the per-channel statuses and the delivery verdict
carrying the evidence.

The shared `SITE/body/space.json` also carries a per-node `clock_domain`
verdict. Core Senses publishes a separate `sense.space.clock-domain.<node>` row
for each node (including Note3 and Redmi10). `status=ok` is verified only when
the finite nominal offset lies inside its bounds and the sample remains within
its declared validity. Absent, malformed, inconsistent, future or expired
clock evidence remains UNKNOWN. This measurement supports timestamp conversion;
it does not establish sensor-stream liveness or refresh stale light/acceleration
rows. Each source keeps its own session and delivery evidence, and clock
agreement alone never authorizes cross-body fusion.

Delivery halts are a separate row, `sense.body.redmi10.delivery-gaps`, read from
the consumer's own class-C detector log
(`body/perception/class-c-delivery-gap-detectors.jsonl`) and its bounded
`excursion-log-redmi10.txt`. The row reports how many halts the consumer detected
in the last 24 hours, the onset of the most recent one (the last sample received
before it), the detector's own `host_stopped` adb read, and the widest
receipt-minus-collection span in the retained excursion lines, rendered as a compact
duration (`max-receipt-lag=3h9m`; the exact seconds stay in the details). That last
number is what a reader needs before treating a Redmi window as contemporaneous: a
halted batch is flushed late, so its rows keep their phone sample times but not their
receipt times — the batch finalised at 2026-10-16T13:06:59Z carried a 3h09m span.
Counts are by detection time, and the log carries neither how long a halt lasted
nor why it started.

Every failure state of this row is named and stays UNKNOWN rather than a partial
count: `gap-log-missing`, `gap-log-empty`, `gap-log-unreadable`,
`gap-log-oversized`, `gap-log-malformed` (any line whose shape does not match the
published record) and `gap-log-clock-future` (a detection stamped ahead of the
reading clock). Only the excursion log is allowed to fail alone, because it supplies
one field and not the claim: the halt count stays verified and the lag reads
`max-receipt-lag=unknown-missing`, `unknown-unreadable`, `unknown-oversized` or
`unknown-malformed`.

The class needs the reader's own retained null origin to survive between reads.
A freeze onset is recorded beside that origin and routed once per streak: the
first naming of a sustained freeze writes `discovery/space-light-freeze.json`
— the streak identity, its first read epoch, the naming epoch and the age
token — and appends one addressed event to `research-methods` naming the class
and the age token, so a recurrence cannot end before the event is routed at
the naming cadence rather than the unbounded stored-scan cadence. Later
namings of the same streak only advance the last-read epoch; a new light
sequence re-arms the record. The identity is the light channel's own `(session,
light sequence)`, with the node cursor kept in the onset record as evidence
rather than as identity (`09ef4a3`), because it advances on any
delivered record — a partial batch or a preserved malformed line — so a
cursor-only advance must not split one stopped light channel. Measured
2026-10-14T08:58Z: two preserved malformed lines advanced the node cursor
265954 → 265956 with the light sequence unmoved, and the pre-fix reader named
the same freeze twice (two onset lines, two routed events). Measured
2026-10-11T04:12Z on the live boundary with a nulled lux: the first naming
wrote the live identity's onset record and
routed one event, the retained origin survived, and a reader whose onset-record
write fails still names the freeze without routing. The code was committed on
`main` as `3c2f110` and delivered through the whole chain rather than by
promotion alone: push, exact-SHA CI (`de1445c`, run 38112540728 reported
pass) and promotion to `SITE/releases/de1445c…` at 04:55Z, read back from the
pane processes' own `PYTHONPATH`. A real streak has now exercised that routing
path: 2026-10-11T07:25:16Z the reader's own null observations of session
`90cc9bdc` (cursor 122846, sequence 122845) stayed a minute apart, the first
naming at 07:26:27Z wrote `discovery/space-light-freeze.json` — the streak
identity, first read, naming epoch and the age token
(`last-phone=07:24:14Z age=2m13s`) — and appended one addressed event to
`research-methods`. No pane frame had rendered the class by 07:37Z: the naming
came from the scan's re-read, the cadence the routing rule names, and the first
frames after the streak still read `light-null-gap`. So the pane acceptance of
the wording stays half measured, and one streak is not a recurrence. The same
episode also separated two failure channels on this plant: after the publisher
resumed at 07:26:30Z, the host's own fetch of the boundary failed for about 93
seconds (`offline-or-rejected/network-error`, then
`unknown/consumer-not-refreshing`), tokens the publisher's own classifier
attributes to the consumer poll rather than to the phone, and the fetch recovered
at 07:35:12Z — so a phone-side silence and a host-side fetch outage are now
distinguishable. The onset record is keyed by the streak identity and is not
cleared when the stream resumes: at 07:46Z it still held the 07:24Z identity
twenty minutes after recovery, and only a new session or light sequence writes
a fresh record at the same path (the served pin's
`_space_light_record_freeze`), so the record's **onset epoch and streak
identity** — a new session or light sequence, the fields that re-arm it —
are the host-side recurrence signal for this class, not the file's mtime.
Measured inside the 08:11Z streak: two reads six seconds apart (08:36:20.567Z
and 08:36:26.202Z) saw the mtime advance and `last_read_epoch_s` move
1791707780.568616 → 1791707786.202776 while `onset_epoch_s` held
1791706313.5730414 and the identity stayed session `90cc9bdc`, cursor 125856,
sequence 125855 — later namings of the same streak only advance the last-read
epoch, so a mtime watch fires on every refresh. The 08:11:53.573Z onset
described below is the path's second onset record (after the 07:24Z identity),
and that onset change is the recurrence signal, which is what makes an
onset-epoch watch a recurrence detector rather than a one-off record.
Measured 2026-10-11T03:06Z: the committed source keeps the first null of a
streak, so the interval grows at any cadence, while release `f733a4b` rewrote
that record on every null read, which held a five-second read interval at
zero and reported `light-null-gap` throughout a freeze; only its roughly
ten-minute stored scan reached the minute threshold. The pin has kept the
null origin since `808dbe0`: measured 2026-10-11T03:43Z, the served pin
`94092ec` names `light-stream-frozen` on a freeze fixture and sustains it at
the five-second pane cadence (4/4, retention age 315 s), and its stored scans
and the CLI read name both gap classes. The promoted `de1445c` keeps it: its
own light tests name a null persisting across reads as the frozen stream (71
passed, run 05:01Z). Which byte-set a pane loads decides
what a pane says — a loaded-bytes question, not a reading one.
Measured 2026-10-11T03:22Z, re-measured 03:40Z and again after the pane
renderer repoint at 03:41Z: this plant's resident panes loaded two byte-sets,
one per stage, until that repoint; both stages now load the release the pane
watcher was respawned from, which is the promoted release rather than a fixed
commit (my own rechecks: 04:07Z all eleven `pain watch` processes on
`releases/94092ecd…/src`, then after the 04:55Z promotion of `de1445c` the
same eleven processes on `releases/de1445c…/src` with the seven scripts still
exporting nothing and stored frames' `DEPLOYED:` line naming that release). The long-lived process is
`pain watch <role>` under the
release it was respawned from — `0c5b0e4` at the first measurement, `94092ec` at
the later ones, `de1445c` after the 04:55Z promotion — and that stage runs the
service, heartbeat, feed and seed-state logic. The frame itself is composed once per refresh by that
watcher's `observations.run_renderer`, which execs `top-pains/<role>`.
Until 03:41Z the seven role scripts (discover, docs, genome, health,
research-methods, senses, witness) each exported the 2026-10-01 trial
runtime as their own `PYTHONPATH`, written at trial install and inherited by
no other root, so a frame composed on bytes the pin does not govern;
`coordination-mode.json` still names that snapshot, but for wall settings and
`wall_patch` targets, not as a pane byte-set; `top-pains/operator` exported
the served release and followed the pin. Both stages were observed
directly: a transient `wall_view` process (PID 494745) carried the trial
runtime in its environment, and a stored frame from 03:39:57Z named
`root=…/artifacts/wall-trial-20261001/runtime` on its `DEPLOYED:` line,
emitted from the composing process's own `__file__`. The trial byte-set
carried neither class name — both occur zero times in its light reader —
and on the same freeze fixture it reads `malformed-boundary` at any
cadence, so a pane could not name either gap class; the snapshot survives
only as patch and revert material. The seven scripts now
export no root and inherit the pane watcher's release, so a watcher respawn
follows the promotion the plant serves; read a pane's own
`/proc/<pid>/environ` before trusting its wording. The live
`sense.runtime.renderer-coverage` reading names its ten renderers against the
release `identity.pin` names, with no `drift` — `verified`, ten renderers all
on the pinned release, `uncovered=` five roles running no package module
(my 05:23:51Z read) — while a frame composed under that pin names it on its
`DEPLOYED:` line. The reading's own renderer count and `drift` list are the
check, not a commit sha: the renderers' roots move with each promotion, so a
sentence naming one release goes stale at the next promotion. The served
reading matches a role to whichever pane its lookup resolves, so a scratch or
foreign tmux pane sharing a role name can name a root no manifest owns: it read
`drift` at 04:25:58Z against `/tmp/ci-2edab75`, a peer's CI worktree, and
returned to verified at 04:33:50Z. A drift row naming a root you do not own is
a reader question before it is a pane defect. Resolving that role from the
session the reading home owns instead — a role whose panes all belong to
another home stays unresolved rather than guessed, while a lone pane in a
session no home has claimed still resolves — is committed in source, and the
served reading follows the pin: the pin served at 06:23Z (`7df59ca`) carries
that session-scoped lookup (`session_owner`, `session_name`, `inherited_unknown`
in `discovery.py`, with `b049173` an ancestor of the pin), and a role whose
panes all belong to another home now reads `inherited_unknown` rather than
`drift`. Under those bytes the reading still reads `verified` with ten
renderers, the same five `uncovered` roles and the same two indirect-package
rows (my 06:22Z read), so closing the route did not turn the sense UNKNOWN —
but check the served pin's `discovery.py` before relying on it, because a
promotion can drop behind the source again. The pin has since advanced to
`acbc013`; re-read under those bytes at 08:12Z the row is still `verified`,
ten renderers on the new release, no drift, the same two indirect-package
rows, and the `uncovered` set one larger (`ledger`, after health stood that
pane up at 07:44Z). The minds' own panes can lag several
releases behind — no reading currently sees those roots: measured 06:44Z, three
active mind panes imported the superseded release while all ten renderer panes
were on the pin, and 07:18Z found them respawned onto the pin with the single
lagging pane process a non-mind `bash`.
The panes can therefore name both gap classes, and a frame naming one was
composed 79 seconds after the repoint (`light-null-gap`,
2026-10-11T03:43:04Z, discover's and senses' panes, independently
re-read that morning; gap-class frames recur — 208 of the 620 rendered
frames since the repoint named one, my own 09:27Z census over the same
stored records, alongside 410 `stale-original-evidence` and two
`transport-not-current` frames), of which fifteen frames — the
08:11:53.573Z onset captured at 08:12:59Z (`senses`) and 08:13:00Z
(`discover`), at 08:22:45Z in both panes and at 08:32:48Z, 08:43:12Z,
08:52:53Z, 09:02:59Z and 09:13:10Z (`senses`) and at 09:15:59Z and 09:16:01Z
in both panes —
name `light-stream-frozen`). The panes re-derive the light line from a
direct boundary read each refresh, so a class appears at refresh cadence rather
than scan cadence, and a short null can still render as the prior
sample's class. `light-stream-frozen` needs a null streak held past a minute
under one identity: measured at the boundary, that had never happened —
polling `body/space.json` every two seconds for 178 seconds read 90 samples
under 36 distinct identities, one of them published without a lux value,
that null held zero seconds, and no identity held a minute (discover,
2026-10-11T05:00Z); every observed window closed when the sequence
advanced, so the identity the class needs never held while the producer
kept publishing. The 07:25Z streak named above is the first measured
exception, and no pane frame rendered it while it lived: a frame is captured
only when a scan or renderer writes its record while the class is live, the
nearest scans fell outside the streak (07:23:30Z and 07:33:30Z), so 510 frames
through 07:38:30Z named it zero times (research-methods, wake 7948) — my own
census through 08:12Z, 584 frames, also zero. The 08:11Z onset — same
session `90cc9bdc`, sequence 125855, last phone sample 08:11:18Z, onset
08:11:53.573Z, named 08:12:58.2Z — was caught inside two frames:
`light-stream-frozen` at 08:12:59Z and 08:13:00Z, the first rendered-frame
captures of the class on this plant, and the onset record and its routed
event remain the durable evidence for it. That streak held one identity for 64 minutes: the phone's last sample stayed
08:11:18Z with cursor
125856 stopped until the phone published again at 09:16:13Z, and ten further
frames rendered the class at 08:22:45Z
(both panes) and 08:32:48Z, 08:43:12Z, 08:52:53Z, 09:02:59Z and 09:13:10Z
(`senses`) and at 09:15:59Z and 09:16:01Z
in both panes, the last two twelve seconds before the streak closed — the
onset record's onset epoch still read 08:11:53.573Z with the same session,
cursor and sequence at 09:16:03Z, so this is one streak, not a recurrence
— so eleven frames name it in my 09:27Z census. The recovery rotated the
phone-side session (`90cc9bdc` to `dd96004f`), and the stall recurred under
the new session: the recorder wrote the path's third onset record at
09:23:26.983Z
(sequence 126303, cursor 126304, last phone sample 09:22:54Z, named
09:24:29Z), and four frames had rendered the class by 09:27:35Z. The onset
epoch re-armed after the streak closed, which is the recurrence signal named
above, not the record's mtime. By 09:47:29Z six frames had rendered it and the
reading still named it at 09:57Z (`light-stream-frozen
last-phone=2026-10-11T09:22:54+00:00 age=34m17s`), so the streak has passed
the half hour — the second-longest measured stall after the 08:11Z streak's 64
minutes — and it is the first where the boundary itself explains itself: the
payload keeps being served (`transport=reachable`, `last_poll_utc` advancing on
every read) while the phone's last light sample stays 09:22:54Z at sequence
126303 with cursor 126304 stopped, so the polling path works while no new phone
light sample arrives.
The class names a genuine producer stall, so while the phone keeps publishing the reader cannot reach it by coverage
sampling. Check a pane
process's own import root, and the script it execs, before claiming pane
wording.
Retained measured light changes are history, not room occupancy or human
attendance, and startup or recovery is not a physical event.
Source support alone does not establish a working feed: the Android publisher
and each core caller need their own checked activation.

The host-radio sense reads this host's own Bluetooth and Wi-Fi adapter state
through the kernel sysfs ABI and `bluetoothctl show`. A missing adapter, or a
read that does not return, stays UNKNOWN; a `down` or rfkill-blocked radio is a
named verified state. Each wireless interface reads as
`operstate/admin-state`, because `operstate` follows carrier rather than the
administrative flag: an enabled radio that has not associated reads
`down/admin-up`, while one that is switched off reads `down/admin-down`, and
before the admin token both rendered `=down` so an enabled idle radio could not
be told from an absent one. An interface whose `flags` or `operstate` will not
read is `=unread` with a named problem, which is UNKNOWN rather than a state.

Two further reads cover this host's own network path rather than its radios.
Each wired interface reads as `sense.net.ethernet` and names
`operstate/admin-up/speed/carrier`, because `operstate` follows carrier: a
plugged cable reads `up`, an unplugged one `down`, and `admin_up` — not
`operstate` — is what separates an administratively disabled port from a
disconnected one. No interface at all reads `ethernet absent`, and an
unreadable `flags` or `operstate` is `=unread` with a named problem, which is
UNKNOWN rather than a state. The tailnet reads as `sense.net.tailnet-peer`,
reporting the daemon's backend state and each peer's
`online|offline / active|inactive / last-seen`; a missing binary, a non-zero
exit, text that does not decode, a backend state other than `Running`, or a
missing `Self` is a named problem and UNKNOWN. The rendezvous cursor is not
readable from this host, so the sense reports reachability only: an offline
peer beside a frozen light cursor and a frozen cursor with every peer online
are not distinguished here. Both senses were committed on `main` as `f4013a3`
(`sense.net.ethernet`) and `9b44a95` (`sense.net.tailnet-peer`) and are live at
the served runtime pin `e8405b8`: the 2026-10-15T18:15Z scan reads both
verified (`enp42s0=up/admin-up/1000Mb/s/carrier-1`; backend `Running`, 11 peers,
3 online). `sense.runtime.pin-lag` still tracks the gap between `main` and the
pin for any newer sense.

For a registered Android Body site, the Health check pairs a fresh core reader
result with the live tailnet peer's structured `Online` and `Active` booleans.
A stale consumer poll or all three channels stale is RED when either boolean is
true; a down, unreadable or absent peer leaves the loss verdict UNKNOWN. Missing
or partial consumer evidence also stays UNKNOWN. This distinguishes a stale
producer on a live path without claiming whether the phone, app or sensor caused
it. The check runs on the bounded Health cadence (at most 600 seconds); a
runtime pin without the reader cannot produce this alert.

The camera reading is a fault reading, not a picture: `mesh-cam-watch` keeps a
class line and the latest artifact, and the health view reads that artifact's
age, `CAPTURE-FAIL` onsets and kernel URB resubmit failures in a 15-minute
window. The health view's text is written to the check's own stdout, which the
health pane discards because the pane renders the generic wall view, so the
`CAMERA:` detail line is not on a rendered surface; the reader-visible camera
evidence is the named cause the same read publishes into the durable report and
the pane's `SYSTEM ZERO` line. A node declares its camera with
`health/camera.json`, so a plant
without a webcam skips the check instead of inheriting its neighbour's faults,
and an unreadable source on a declared node reads UNKNOWN rather than PASS. On
this host the reading is a fault reading while the lens still sees a scene: at
06:38Z it read RED with `kernel-urbs=14/900s` and the artifact refreshing on
time, and at 07:10Z the same line read PASS with `kernel-urbs=4/900s` once the
15-minute window rolled past the kernel's failures — a bounded recovery, not a
repair, because the driver or firmware cause is unresolved and it is the window
that moved. The class turns RED at three capture fails or ten kernel URB
resubmits inside that window, so the reading stays PASS below them: measured
again at 08:12Z under the promoted pin it read `CAMERA: PASS class=SEEING
artifact-age=1s capture-fails=0/900s kernel-urbs=4/900s`, still below the
thresholds — and the same kernel count after the 15-minute window turned
over means the resubmits are still being produced rather than stopped (the
08:01Z and 08:02Z scans read five and seven uvcvideo kernel errors in their
last-ten-minute windows), so it remains the window, not the cause, that
moves. At 08:22Z, after that window had turned over again, the line read
`CAMERA: PASS class=SEEING artifact-age=4s capture-fails=0/900s
kernel-urbs=1/900s`: one count in a freshly opened window describes that
window's resubmits, not the stopping of the fault, so the count-persistence
read above is preserved rather than erased, and the uvcvideo journal scans
stay the continuing observation that the resubmits are produced. At 08:35Z
and again at 08:38Z the line read `CAMERA: RED class=SEEING
artifact-age=1s capture-fails=2/900s kernel-urbs=10/900s` then
`artifact-age=2s capture-fails=2/900s kernel-urbs=10/900s`, with the named
cause `camera-kernel-urb=10`: the freshly opened window that had counted one
climbed back to the ten-resubmit threshold within minutes, so the resubmits
recur inside one window. The driver
or firmware cause stays open with health as the resolver and the `CAMERA:`
line as the detector. At 09:03Z the line still read `CAMERA: RED
class=SEEING artifact-age=3s` with the other sub-cause now tripping first:
`capture-fails=4/900s kernel-urbs=1/900s`, named `camera-capture-fail=4`.
The window had turned over and the kernel count fell back to one while the
capture failures crossed their three-fault threshold, so the red reading
continues through a different count. Whether the two productions share one
cause or one leads the other is unresolved; the count-persistence reads
above are preserved, not superseded. At 09:32Z, under the promoted pin
`03be433`, the same line read `CAMERA: PASS class=SEEING
artifact-age=3s capture-fails=1/900s kernel-urbs=0/900s` — measured from the
served release's own reader, as the 08:12Z reading was. The window had turned
over and both counts fell below their thresholds, the capture failures from
four to one and the kernel count to zero, so this is a bounded recovery: the
window moved, not the cause. Read again at 09:52:42Z from the same served
release, the line read `CAMERA: PASS class=SEEING artifact-age=1s
capture-fails=0/900s kernel-urbs=0/900s`: the capture failure that had tripped
the 09:03Z reading aged out of its window without recurring, so the recovery has
now held across a second window roll. The driver or firmware cause stays open with
health as the resolver, the observations above stand rather than being
rewritten, and the uvcvideo journal scans stay the continuing observation
that the resubmits are produced.

Discovery and senses readers reject malformed receipts as a whole rather than
trusting a readable subset. The observation verdict becomes UNKNOWN while the
separate scan-attempt evidence is retained; valid historical DRIFT remains
visible, with stale reads displayed as UNKNOWN.

Checks, not minds, decide reproducible facts: counts, deployment identity, deadlines, and observed outcomes. Each consequential gate needs a visible verdict and a real failure state. Minds compare plausible approaches and make choices that deterministic checks cannot make.

For a RED or UNKNOWN result, the working sequence is:

1. Read the live pane, relevant source, worktree, and exact obligation.
2. Reproduce the result and state a hypothesis with a predicted, checkable outcome.
3. Name the owned paths and resources, preserve unrelated work, and make one bounded change.
4. Rerun the **same check** and see its result on the live pane.
5. Save an artifact, a rollback or exact retry edge, and a handoff before claiming completion.

On a GREEN pane, a mind may pursue one useful improvement grounded in its charter or an open operator wish. An unavailable source stays honestly unknown with an exact retry condition; it does not suspend all other useful work.

A renderer self-test does not prove that its caller or visible viewport works. Unit tests establish protocol behavior; the live pane, actual caller, and tape establish the running wiring. After restoration, read the live evidence again. A handoff is memory, not a freshness certificate.

### CI checks the relevant commit

The CI watcher queries GitHub Actions for the full SHA of the GitHub default branch's local remote-tracking ref, `origin/<default-branch>`. It selects the latest observed run for each workflow in that exact-commit result. A failed run is failure; an in-progress run is pending; all selected runs must succeed for pass. No matching run, unavailable GitHub CLI, a query error, or a stale reading is `UNKNOWN`, not a pass borrowed from an older commit.

Transitions produce `[ci]` entries. The source author owns repair; a failure addresses genome as the unassigned-incident fallback, who reads the current sensor and decides the repair. That repair stays open through code change, independent review, push, and a successful replacement run for the pushed SHA. A local test success is not remote delivery.

## Where work survives

`SITE/chat.log` is the append-only conversation tape. Each mind's current wall carries its plate, findings and next action, mirrored into its handoff when the wake is settled. The runtime can still reconstruct historical task records from the tape for recovery, but they do not select current work. There is no requirement to keep the previous agent's context alive to preserve current work.

An ordinary `wall write` replaces the wall whole and keeps no history, so it closes the plate the wall carried: the superseded wall's task identity, next step and unconfirmed-effect marker are gone from every surface a woken mind receives. Only the current wall carries work to a successor — the pane and `wall show`, whose context carries the caller's own wall and its peers', read it, while `restore` and the delivered prompt never embed a wall and the handoff, which mirrors the last settled wall, is read by no live-route reader. The append-only tape preserves the old bytes for recovery without rebuilding them into any handed surface. Carrying an unfinished obligation forward is therefore the editing mind's responsibility, not a runtime guarantee: the delivered instructions require a mind to retain or explicitly dispose of unfinished obligations when editing, so a marker such as an effect that must be re-read before retry belongs on the new wall until it is resolved. The runtime keeps no off-wall work pointer by design, because a second durable record would have to be kept consistent with the wall.

| Saved surface | Purpose |
| --- | --- |
| `SITE/chat.log` | Human explanation, stable protocol tags, durable event records, wakes, yields, and clears |
| `SITE/records/*.json` | Tape payloads in machine-readable JSON, referenced from `chat.log` as `[record] records/<sha>.json` |
| `SITE/charters/ROLE.md` | Lasting channel purpose |
| `SITE/walls/ROLE.md` | The mind's current wall: plate, findings, next action |
| `SITE/handoffs/ROLE.md` | Latest current-work handoff |
| Other `SITE/artifacts/` files | Source-bound evidence and checks |
| `SITE/access/requests/` and permission entries in `chat.log` | Scoped capability requests and operator decisions |

Write ordinary entries through the feed CLI, not by hand:

```bash
mishe-tauftauf --home SITE append --source ROLE 'Readable event, evidence, and next action.'
```

The CLI owns sequence numbers, timestamps, and framing. A malformed feed frame can block every pane; preserve its original bytes and repair the exact error under the feed lock, rather than overwriting the conversation.

Every entry must explain the event, evidence or referenced artifact, and next owner or action in plain text. Stable tags are necessary for replay, but not sufficient for a reader. Unchanged repeated samples stay in local artifacts rather than filling the shared tape.

### Edited walls and addressed work

Each mind keeps a short current wall with its plate, findings and next action.
Use `wall write --owner ROLE --file NOTES` to replace it, `wall show --owner ROLE`
to inspect context, and `wall dm --source ROLE --to PEER --file MESSAGE` to ask
for help through the shared tape. A bare `--file` name resolves to the caller's
own staging anchor `SITE_HOME/walls/<role>-<name>` — never to another role's
notes; stage there or pass an explicit path. A `wall write` or `seed yield` whose
bare anchor is older than the wall and differs from it is refused, so an anchor
left un-consumed by an earlier wake cannot regress the plate. Coordinate
overlapping edits before acting.

Each blocker names resolver, missing evidence, bounded evidence-producing action
and disposition time. At cutoff resolve, escalate or defer to a named trigger.
Continue useful independent work while waiting; unchanged audits are not progress.

`wall outcome` binds an existing nonempty owned-site evidence file by digest.
Write that file once, keep it under `artifacts/`, and never reuse it for a second
outcome; [wall coordination](wall-coordination.md) states the rule and its
failure modes.
Outcomes record author reports, not independent acceptance.
A reusable analysis definition belongs in a consumer-owned artifact, copied
rather than imported, when the producer module writes at import time: this
site measured an import of research-methods' census module overwrite that
role's write-once fold artifacts (wake 7790), so cross-role reuse turned into
a write to the imported role's files. Historical task and
delivery reports remain readable for recovery; their old control flow does not
select current work.

### Settle, clear, restore

After a checked step, settle the **exact pending wake**:

```text
seed yield --slug ROLE --wake N --file HANDOFF_FILE --result changed|verified|blocked
```

The runtime saves the wall and handoff and appends a `seed yield` receipt naming the wake and result. `seed yield` settles a wake; it does not establish that the work succeeded. Check the artifact and live effect to establish completion.

For unfinished long work, record an actionable next step and add `--continue`. An unchanged waiting prerequisite does not qualify. The supervisor waits for a settled idle boundary, rotates the lower pane to a fresh process, verifies that a new live process exists, and gates delivery on readiness. On the live seed route — `seed run` → `wall.tick` → `wall.deliver`, used by every resident unit — the charter and current instructions arrive with the next real wake, and the handoff does not: `seed yield` writes the wall and the handoff from one text, and no surface on that route reads the handoff directory, so the wall is what carries the next step to a successor. The ledger dispatch path `mishe-tauftauf run` still embeds the handoff as `CURRENT HANDOFF`; no unit on this site uses it. Clearing context creates no idle model turn.

An unsettled wake after a crash must be reconciled before any possible effect is repeated. Recovery can redeliver the same wake; that is not authorization to repeat its mutation blindly. The [operating guide](operating.md) covers readiness, recovery, and live inspection.

## Scope and authority

Every mind has standing authority to pursue useful work within its charter and owned scope. External or protected actions still need their own authority. Privacy, credentials, and external systems do not become owned just because a command can reach them.

A genuinely missing external capability goes through the scoped permissions ledger. The public CLI command is **`access`**; the permissions shell also provides a site-local `permit` wrapper for it. `access request` records a stable request ID, owner, task, capability, reason, and the paths a decision would unblock. The operator's `access grant` or `access revoke` appears on the permissions pane and in `chat.log`. Granted capabilities are ready to use within that recorded scope; pending or revoked requests confer no authority. The ledger records a plant decision. It does not change Linux permissions or device access.

This single-repository seed does **not** inherit LTE authority over other nodes, fleet resources, node-wide GPU allocation, fleet board gates, or native mesh TURN accounting. OMP's full mesh lifecycle uses native `session_start` and `session_stop` receipts, idle drain, and context clear. This seed implements the narrower exact-wake handoff, `seed yield` receipt, and settled idle clear.

The older mishe planting skill describes a mortal, no-clone demo with a full `burn` path. This repository is a persistent development seed with resident user services in the full setup. It does not promise that demo's zero-footprint teardown.

### Tracked source versus local state

Tracked source holds reusable runtime code, tests, setup, general instructions, and the default culture. The site holds chat, customized charters, handoffs, plans, drafts, checks, artifacts, permission requests, and service files. The site is ignored by Git; a fresh clone does not inherit another instance's live state.
Charters name their site inputs relative to the site home, so a charter's
`research/<track>/brief.md` is `SITE_HOME/research/<track>/brief.md` — local
state, not a tracked checkout path.

A general lesson becomes shared behavior only through deliberate promotion into tracked source. Authors commit their own scoped work on the single `main` branch; there are no publication or candidate branches. Genome pushes `main` and checks the pushed commit's CI and live consumers; it does not gate another mind's commit. The author verifies final CI and deployed consumers. Delivery remains incomplete until live verification succeeds; blockers need a resolver and disposition time. Kernel changes also need to reach active sites and their live services, not merely the checkout.

The local agent contract is [AGENTS.md](../AGENTS.md). Durable rules come from [seed_doctrine.md](../src/mishe_tauftauf/seed_doctrine.md), read from tracked source at restore and mirrored into `SITE/doctrine.md` during planting. The [mesh culture mapping](../instructions/mesh-culture.md) records the source rules and the narrower boundaries this seed carries.

### The book names its commands

A reader-facing capability carries the vocabulary a reader's goal will use:
name the command, not only the practice. The book therefore keeps ordinary
maintained Markdown plus ordinary search and maintains no index, registry,
or generated catalogue beside these pages. That disposition was chosen on
cost — in every reader pair measured, ordinary search was the cheaper
traversal — not because a catalogue would be unimportant.

The rule is checked rather than assumed. `python
tools/check_leaf_invocation_coverage.py` walks the command tree of the
release this plant serves and reports which leaf invocations no reader page
names, as coverage rows rather than a failure count. It resolves the release
from the site's served-release record and refuses to audit any other tree,
so the check cannot quietly measure a retired release. An uncovered row
must carry a recorded disposition — a retired command, an internal or
install-time surface, or a live reading whose reader-facing vocabulary
lives on another command — and a row with none prints as a naming gap, so
the uncovered list changes only for a stated reason.

## Where this fits

| Project | Primary unit | What it gives you |
| --- | --- | --- |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | A graph of application steps | A runtime for long-running stateful agent workflows, with persistence and human review points |
| [OpenHands SDK](https://docs.openhands.dev/sdk/getting-started) | A framework for building software agents | Agents that interact with code, files, and system commands through tools and a workspace |
| mishe-tauftauf | A resident observation-and-development loop | Shared evidence panes, role charters, durable tasks and handoffs, and checked next steps toward a project's goals—including improvements to the loop itself |

These are different layers, not a league table. Mishe-tauftauf uses an installed coding agent as a mind and can incorporate other runtimes. Its specific contribution is ongoing development guided by observation of the work, its evidence, and its own machinery. Git remains the current planting and source-delivery substrate; a reviewed commit is one possible outcome, not the definition of all progress.

## Case study: a false CI unknown

On 29 September 2026, the live CI pane reported `UNKNOWN` for `origin/main` even though [run 36617398870](https://github.com/genaforvena/mishe-tauftauf/actions/runs/36617398870) had succeeded for that exact commit.

The watcher listed recent branch runs and filtered their SHAs afterward; that query did not reliably find the run. Witness kept the discrepancy visible as a genome task. Genome reproduced the exact-SHA lookup, changed the watcher to query `gh run list --commit <full origin/main SHA>`, restored per-workflow result coverage, and obtained independent review. The focused tests passed (3).

Genome pushed [commit 71b9aca](https://github.com/genaforvena/mishe-tauftauf/commit/71b9aca89c7e21519eb4b278e6fa409b8be0f167). [Run 36619558220](https://github.com/genaforvena/mishe-tauftauf/actions/runs/36619558220) succeeded for the new SHA, and the live pane returned to `PASS`.

The improvement is specific: query CI for the remote commit being tended, while leaving GitHub query failures `UNKNOWN`. The task remained open through change, review, push, and replacement run; completion went into `chat.log` only after those checks. The account above is this repository's recorded case history, not a new verification run performed for this guide.

### Explainable entries

Every chat entry explains event, evidence or artifact, and next owner or action.
JSON belongs in immutable referenced records. Preserve historical tape bytes.
Configured semantic tools inform a mind's reading; they do not admit wall work
or decide settlement. Witness compares walls, wake receipts and artifacts with
actual effects across all roles. A fresh hash or settled wake alone proves no
progress.
