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

`inference_effects.EffectBoundary` is the optional caller-owned binding for that
`dispatch` callback. Constructed with one store directory, a `writer_id`, the
obligation identity, the bound authority and the caller's capability executor,
it maps the provider's call shape onto a durable, reconcilable operation: it
allocates the intent before any effect, persists the start record before the
executor runs, and appends the outcome afterwards. The reply it returns is the
loop's expected status shape, so `completed` continues and `unknown` stops
without retry. A second dispatch of the same operation id returns the recorded
outcome or an explicit `unknown`, never a second execution. The scope is a
single local store and single writer per process; process-crash recovery, not
power loss.

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
turns/results and used IDs into a new journal. Exhausted turn budgets cannot call
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

`inference_inspect.prepare_exact_inspection` freezes one caller-supplied absolute
file path, SHA256, byte count, source/task identity and loop budgets before a model
turn. Preflight and dispatch use the same bounded read: every path component
refuses symlinks, the target must be a stable regular UTF-8 file, and its digest
must match before any text is returned. The model can select only `inspect` with
that exact path and no extra arguments. `EffectBoundary` binds the read to the
actual caller obligation and writer, rather than a prototype identity.
`run_exact_inspection` composes this executor with `drive_native` and an exclusive
`NativeJournal`; reusing a prepared run refuses before another model turn.
An evidence change after dispatch starts remains UNKNOWN even if bytes are later
restored. The caller owns trusted unchanged manifest/executor objects, session
lifetime/output/time limits and the independent completion oracle. The recorded
selector is intended-model metadata, not proof of the session's provider choice.
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

The packaged native provider session permits at most one underlying `fetch`
dispatch per turn, including lower-level HTTP retries and encoding fallback.
An attempted second dispatch poisons the session and raises
`native provider fetch budget exhausted`; provider cancellation normalization
cannot replace that diagnostic. An ordinary error terminal remains an error,
and a later caller-authorized turn has its own one-fetch allowance. This is not
automatic retry authority, an output-token cap, a spend guarantee or proof that
the server processed only one request. `drive_native` currently propagates this
exception without recording its diagnostic in the caller journal; the pending
model input remains unresolved and must not be blindly resubmitted.

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
resident adoption. Preserve the existing harness route until a reviewed canary.

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
coupling rather than a coverage gap.

The kernel journal sense counts readable priority `err` or higher kernel entries
in a fixed ten-minute UTC window, not message lines, independent faults or a
rate. Its coverage records endpoints, boot identity and monotonic acquisition
bounds. Failed, incomplete or invalid acquisition is UNKNOWN, with no last-N
fallback. A verified zero does not establish journal retention or permission
completeness. The window is historical evidence; current decisions need a fresh
scan, and a boot or source change invalidates comparisons.

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
for help through the shared tape. Coordinate overlapping edits before acting.

Each blocker names resolver, missing evidence, bounded evidence-producing action
and disposition time. At cutoff resolve, escalate or defer to a named trigger.
Continue useful independent work while waiting; unchanged audits are not progress.

`wall outcome` binds an existing nonempty owned-site evidence file by digest.
Write that file once, keep it under `artifacts/`, and never reuse it for a second
outcome; [wall coordination](wall-coordination.md) states the rule and its
failure modes.
Outcomes record author reports, not independent acceptance. Historical task and
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
