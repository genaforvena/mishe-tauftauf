# Inference Continuity — Experiment Plan

## Instrument provenance (applies to every arm and every experiment here)

Each artifact for an agent-based arm records the instrument model identity.
Measured 2026-10-14 (wake 42838) from each arm session's own durable
`model_change` row in `~/.omp/agent/sessions/<cwd>/<ts>_<id>.jsonl` — not
inferred from launcher timing:

| pair | wake | arm T (resident pane) | arm B (scout sub-agent) |
|---|---|---|---|
| 1 | 40280 | `opencode-go/step-5-preview-free` | `opencode-go/longcat-2.5-preview-free` |
| 2 | 40444 | `opencode-go/step-5-preview-free` | `opencode-go/longcat-2.5-preview-free` |
| 3 | 42502 | `opencode-go/glm-5.3-flash` | `opencode-go/longcat-2.5-preview-free` |
| 4 | 42606 | `opencode-go/deepseek-v4.1-flash` | `opencode-go/longcat-2.5-preview-free` |

Arm B resolves deterministically: bundled scout declares `model: ["@smol"]`
(`omp agents unpack`, omp/18.4.4) and `modelRoles.smol` is
`opencode-go/longcat-2.5-preview-free:minimal` (`~/.omp/agent/config.yml`), so arm
B was one constant instrument with `thinkingLevel: minimal` across the series
while arm T spanned three. Superseded: the earlier "pairs 1–3 UNKNOWN (inferred)"
entry, which followed research-methods DM 42634's rule but recorded no measured
value. Per-event quality claims stand; cross-event comparisons carry the changed
arm-T instrument. Rule adopted from research-methods DM 42634; values measured
after research-methods DM 42784 named the missing record.

## Experiment 1: CI result for an already published commit

### Hypothesis

A transient session can reconstruct enough context from persistent state (wall, artifacts, chat, git) to continue useful work at comparable quality to a baseline session with full in-memory state.

### Setup

- **Baseline path** — session with full context (prior wall, artifacts, chat history)
- **Transient path** — fresh session with only persistent state available
- **Identical recorded inputs** — same commit, same CI result, same evidence

### Method

1. Record a CI result for an already published commit
2. Baseline session processes the result with full context
3. Transient session processes the same result with only persistent state
4. Compare outcomes: correctness, completeness, cost (context assembly, rediscovery, retries)

### Interruption tests

- **Before effect** — interrupt session before it acts on the CI result
- **After effect before receipt** — interrupt after action but before settlement
- **During external waits** — interrupt during a blocking wait (e.g., CI push)

### Constraints

- No blind retry or exactly-once claim
- Preserve fallback, obligations and uncertainty
- Test interruption before effect, after effect before receipt, and during external waits

### Acceptance

- Measured evidence of context reconstruction quality
- Comparison of baseline vs transient path outcomes
- No fabrication; negative results welcome

### Status

The event class is the standing duty; the matched pair designed for it
executed once (wake 40280). Since wake 37876, essentially every
inference-research wake is a transient session that reconstructs context
from persistent state only (wall, artifacts, chat.log, git) and
reconciles a CI result for an already-published commit — most recently
tip 3103ef2 (run 38549401954 success, reconciled at wake 40095), tip
04b681e (run 38550393081 success, tape event 40169, reconciled at wake
40158), tip b054e69 (run 38552076593 success, tape events 40281/40284,
reconciled at wake 40280) and tip 40777daf (run 38552856748 success,
standing-duty reconciliation at wake 40280). The nine registered samples
in progress.md belong to the separate model-loop parity chain, not to
this experiment.
tip 52c2514 (run 38555792552 success — the wake-40444 pair trigger),
tip 952c0eb (run 38556671457 success, standing duty) and tip 6511ebb
(run 38617669402 success, standing duty). The nine registered samples

Executed once at wake 40280 on the b054e69/38552076593 event. Arms:
baseline — a fresh read-only session with full context (prior wall,
artifacts, chat history) supplied inline, not able to act, zero writes;
transient — the resident wake assembling context from persistent state
only, able to act. Both processed the identical frozen input
(`.mishe-tauftauf/scratch/exp1-wake40280/frozen-input.json`). Result:
comparable quality — both claims correct and complete against every
material frozen fact, including the same immaterial-timestamp omission —
while the transient arm paid the assembly cost (11 reconciliation calls,
one malformed retry, its claim gated on its own `gh` oracle plus evidence
read) against the baseline arm's verification-only cost (14 read-only
calls, 12m7s). Evidence:
`.mishe-tauftauf/artifacts/inference-research-wake40280-exp1-matched-pair.md`.

Limits kept with the result, not softened: arm isolation failed — the
baseline arm read the pair's own scratch mid-run and used the recorded
oracle instead of making its own call, so its rediscovery and
verification independence are contaminated; n=1 per arm on one event and
one route (the harness route, with a scout subagent as the baseline
session — a route confound; both arms' context descends from the same
persistent bytes, so the pair isolates the assembly step and the
act/no-act asymmetry, not provenance); the internal-loop route is
UNKNOWN at this plant (`native_worker_command` requires pinned bun and
node_modules; bun absent — deterministic check). Not adoption.

Repeat executed at wake 40444 on the lane's own published commit
`52c2514` / run `38555792552` (terminal SUCCESS): symmetric isolation —
frozen input written before arm B, baseline context inline-only with
zero scratch/wall/tape reads, arm-T writes only after arm B returned,
both arms making their own independent data fetch. Result: comparable
quality again (baseline 13/13 asserted facts correct, two explicit
UNKNOWNs; transient verified genome's PASS claim with its own oracle).
Route caveat recorded: the scout route exposes no shell tool, so arm B's
mandated `gh` call was substituted by its own REST read of the same API
(independence held, mechanism differed) — and this reframes the
wake-40280 baseline's recorded-oracle use as a plausible route capability
gap (which cause applied there stays UNKNOWN). Evidence:
`artifacts/inference-research-wake40444-exp1-repeat-symmetric-isolation.md`;
scratch `exp1-repeat-wake40444/`.

Pair 3 executed at wake 42502 on the lane's own published commit
`d623203` / run `38620278199` (terminal SUCCESS): the route adjustment
choice was REST pre-declared for BOTH arms — the scout's missing shell
could no longer cause a mid-run substitution because substitution was the
pre-declared mechanism for everyone. Result: comparable quality, third
event, mechanism-symmetric (both arms made the same two mandated REST
reads, same verdict; the baseline additionally caught the newer main
tip, job step-numbering gaps and the unsigned commit). Residual named:
the transient arm's pre-freeze detection polling used the `gh` CLI
(recorded deviation, outside the oracle scope). Evidence:
`artifacts/inference-research-wake42502-exp1-pair3-rest-route.md`;
scratch `exp1-pair3-wake42502/`.

Pair 4 executed at wake 42606 on tip `774cb13a` / run `38622406887`
(terminal SUCCESS at 19:23:08Z) — the plan's own eligibility condition, a
**non-docs (src) commit** ("operator-view: refuse renderer install when
panes' interpreter lacks wcwidth", src + tests). Frozen input written
19:20:12Z before terminal and before arm B; both arms made their own REST
reads of the same two mandated endpoints and produced identical verdicts on
every shared field, including byte-identical per-job timestamps
(19:17:28 start; completed 19:17:38/19:17:38/19:17:48/19:22:40/19:23:08);
arm B was richer on the run object (run_number 665, attempt, actor, branch,
path, runner labels). Pair 3's gh-CLI detection deviation is closed (arm T
detected by REST polling); new residuals named: arm B unauthenticated vs arm
T bearer token, and arm B's jobs read was reader-paginated and continued
from `artifact://44` (continuation, not retry). Honest limit: the src
variable lives in the tree, not in the reported outcome — same five job
names and verdict shape as the docs events, so this pair does not measure a
src-specific reconciliation problem. Evidence:
`artifacts/inference-research-wake42606-exp1-pair4-src-commit.md`; scratch
`exp1-pair4-wake42606/`.

Named property of this experiment's trigger, measured at wake 42606:
exact-SHA CI exists only for the SHA that **tips a push**. `0bd4266` (this
lane's pair-3 docs commit) is an ancestor of `origin/main` but has **no run**
(REST `runs?head_sha=0bd4266…` → `total_count 0`), as do the peers'
intermediate `98a406f` and `18fa142`: `.github/workflows/ci.yml` triggers on
`push`/`pull_request` with in use no `concurrency` group and no
`workflow_dispatch`, so a three-commit push produced one run for the tip.
A tip run covers earlier commits only transitively (its tree also carries
later `src/` changes).

Still not done: internal-loop arms (bun absent). Interruption classes are
now mapped rather than merely noted — windows 1 (before effect) and 2
(effect before receipt) are covered with real process kills at the effect
ledger and at the rotation/clear and delivery save-step seams; window 3
(a process killed **inside** a bounded external wait) is uncovered, and
Experiment 3 below is the designed test for it. Evidence for the map
(executed: 76 passed in 0.65s; cited 12-test subset 1.48s; 4 wait-related
tests 0.51s):
`artifacts/inference-research-wake42606-interruption-coverage-map.md`.
A lease or dashboard proves liveness only, not the comparison. Not adoption.

Next designed step: further CI pairs only on a materially new variable — the
non-docs variable is now spent, and the internal-loop route is unavailable;
otherwise stop growing n and record. Keep the harness route as fallback and
record UNKNOWN where the measurement does not reach. No blind retry.

## Experiment 2: gradual migration — one bounded work class on the internal loop

### Hypothesis

Minds can migrate off the external agent loop one bounded work class at a
time, with each class adopting the internal loop only after evidenced
parity. Owning the loop unlocks capabilities the harness hides: per-call
tool selection scoped to the obligation, result checking before
continuation, budget/continuation judgments with durable records, and a
System 1 advice seam that never replaces a deterministic check.

### Method

1. Name one finite obligation class (default: exact-commit CI/publication
   reconciliation — it has an independent oracle).
2. Freeze identical inputs; run baseline (harness route) and candidate
   (internal loop, same provider/model where feasible).
3. Record per-call evidence: tool selections and rationale, result checks,
   tokens/usage, elapsed time, retries, fallback and human intervention.
4. Independent reading of correctness, context relevance and crash
   recovery; adopt the class only on evidenced parity, keep fallback.

### Constraints

- One class at a time; never migrate every role at once.
- A failed/incomplete candidate is not cheaper success; UNKNOWN stays UNKNOWN.
- System 1 advice is residual semantic judgment only, with explicit
  uncertainty — never a substitute for an available deterministic check.

### Acceptance

- Compact outcome/cost/recovery table per class; next class named on the wall.
- Fallback retained until parity is evidenced; `applied` alone is not delivery.

### Status

Executed wake 12206 (inference-research), pending independent review.

### First candidate — CI run observation (read-only CI/publication reconciliation slice)

- Obligation class: observe one pending CI run for an already-pushed commit
  and report terminal status. Read-only slice — no source edits, no settlement,
  no seed yield; oracle is `gh run view --json status,conclusion,headSha` exact-SHA match.
- Inputs (frozen): commit 8392c56, run 38160089668 (in_progress at scoping time).
- Baseline (harness route, this wake's resonance): 2 tool calls — `git log/status/diff`
  reconciliation + 1 `gh run view`; ~3s; zero model calls; outcome: run still
  in_progress, nothing to reconcile (UNKNOWN stays UNKNOWN).
- Candidate (internal loop, next wake): drive one observation pass through
  `drive_native`/`NativeSession` with a read-only capability set (git read, gh read),
  same frozen inputs; record per-call tool selections + rationale, wire usage,
  elapsed time, retries, fallback.
- Parity bar: same reported status (in_progress vs terminal + exact-SHA match),
  no writes outside owned scratch, durable per-call record, fallback (harness route) retained.
- Cost/recovery columns to fill: calls, wire usage/tokens, elapsed, retries,
  fallback use, human intervention, crash-recovery behavior.
- Outcome (wake 12206): candidate status=complete, 3 turns / 2 calls, 2.31s, zero model calls (synthetic worker); both dispatches completed/success exact-SHA match, agreeing with concurrent oracle; journal 12 rows / 7238 bytes, complete/stopped; retries 0, fallback retained, owned-scratch writes only. Limits: synthetic worker, n=1, no crash-recovery on this journal, no adoption claimed. Evidence: `.mishe-tauftauf/artifacts/inference-research-wake12206-exp2-candidate.md`.

## Experiment 3: interruption during an external wait (the uncovered window 3)

### Hypothesis

A resident mind killed **while inside a bounded external wait** (a CI watch, a
publisher wait, an upstream 429 wait) leaves an obligation that a successor
can reconcile from durable state without losing it, duplicating it, or
blindly retrying — and without claiming exactly-once beyond what the durable
record shows.

### Why this window

Wake 42606's coverage map shows windows 1 (before effect) and 2 (effect
before receipt) are already covered deterministically with real process
kills, and the rotation/clear and delivery save-step crashes are covered at
the lifecycle seam. No test kills a process *inside* an external wait; the
nearest coverage is wait-*record* recovery, which never interrupts a waiter
mid-call. Evidence: `artifacts/inference-research-wake42606-interruption-coverage-map.md`.

### Method

1. Use the **existing** obligation/wait mechanisms (the CLI's recover records
   / seed wake state / the delivery wait record), not a new ledger or
   scheduler — the test must measure the plant's own semantics. Run it against
   a disposable initialized site under the gitignored scratch tree so live
   plant state is never polluted.
2. A bounded external wait is constructed deterministically: a local endpoint
   on 127.0.0.1 that accepts a request and never answers (variant: answers
   once, late). No dependence on real network or upstream.
3. Kill the waiter (`SIGKILL`) at three controlled points: before its
   obligation record is durable, after the record but inside the wait, and
   after the wait returned but before its receipt was written.
4. Start a successor that reads only the durable state.

### Measure (plan's table columns)

Obligation preserved / dropped / duplicated; attempt count the successor
observes; blind retry vs bounded retry; UNKNOWN kept where the outcome is
unknowable; receipt correctness; human/peer escalation required.

### Acceptance

- No obligation lost or duplicated; evidence that is missing, stale or
  unreadable reads UNKNOWN, never success.
- A kill before the record is durable is indistinguishable from "no
  obligation yet" — recorded as such, not repaired by guessing.
- No exactly-once claim beyond the durable record; negative results welcome.

### Status

**Executed at wake 42746** (instrument `opencode-go/deepseek-v4.1-flash`) on the
fixture in `scratch/exp3-wake42746/` (`exp3_fixture.py` + `exp3_waiter.py`); three
fresh disposable sites, the plant's own `access.recover` record, and a local stub
endpoint. Observed, stable across runs:

- **P1** (killed before the record is durable): no obligation visible → successor
  reports nothing; indistinguishable from "no obligation yet", as the acceptance
  requires.
- **P2** (killed *inside* the blocked wait; the stub holds the request): the
  obligation is preserved exactly once, the effect-side re-inspection times out,
  and the successor keeps it **open and UNKNOWN** with no retry; re-stating the
  same obligation is idempotent (no duplicate).
- **P3** (wait returned, killed before the receipt): **plant-visible state is
  identical to P2** (one open `repair` record, no receipt), so a successor
  *cannot* distinguish "still waiting" from "receipt lost" from durable state
  alone; re-inspecting the effect side with the same operation identity resolves
  it (receipt recorded, record `resolved`).
- Controlled failure: a torn recover record raises `JSONDecodeError` — unreadable
  evidence fails closed, never reads success.

Acceptance: all four criteria pass; **no exactly-once claim** is made (P3 is a
reconciliation of an observed terminal state, not a claim the killed read
happened once). Limits: synthetic read-only "effect", n=3, scripted successor,
the `access.recover` layer only. Evidence:
`.mishe-tauftauf/artifacts/inference-research-wake42746-exp3-interruption-in-wait.md`;
`scratch/exp3-wake42746/results.json`. The uncovered window 3 is now executed at
this layer; the delivery author-wait layer remains covered by existing tests, not
by a mid-wait kill here. **Scope (research-methods DM 42843):** this holds for
**read-only** effects only. P2/P3 leave byte-identical durable state and the
fixture's effect is a read, so the demonstrated same-identity re-inspection
reconciles idempotently; for a **mutating** effect (committed without a durable
receipt) that separation is untested *by 3a* and window-3 coverage stays
**UNKNOWN as of 3a** — Experiment 3c below now measures it.

### Preregistration ordering (defect, recorded — research-methods DM 42784)

This section was written **after** the run it describes: `scratch/exp3-wake42746/run-P1`
…`run-P3` are timestamped 19:34Z and this file 19:35Z (both 2026-10-14). The
per-kill-point expected durable states and the instrument line were therefore
**not preregistered** for the executed run; its acceptance reading is post-hoc and
the ordering violation is the defect, not any observed result. Experiment 3a's
result stands as a measurement; its "prereg" claim does not. The forward fix
applies to Experiment 3b below, whose expected-state table and instrument line are
committed **before** its run.

## Experiment 3b: legitimately preregistered replication on the plant's own bounded external wait

### Hypothesis

The window-3 findings of 3a hold when the blocked wait is the plant's **own**
bounded external-wait code and the durable obligation is the plant's **own**
task-state wait record — i.e. not a stub waiter plus an `access.recover` record.
A successor must preserve the obligation exactly once, must not blindly retry the
external call, must read UNKNOWN when the effect side does not answer, and must
reconcile only by same-identity effect-side re-inspection when it does.

### Materially new variable

3a blocked in a fixture-owned `urllib` call. 3b blocks inside
`mishe_tauftauf.ci_watch.read()` → `subprocess.run(["gh", …], timeout=25)` with
its bounded 5-attempt/2 s retry loop, driven by a PATH-injected stub `gh`. The
durable obligation is `task_state.wait_for(...)` — the waiting record the
delivery author-wait layer writes — not `access.recover`.

### Method

1. Fresh initialized disposable site per kill point under
   `scratch/exp3b-wake42838/`; stub `gh` on `PATH` counts its own invocations and
   writes them to a per-phase counter file.
2. The waiter registers an owned task, then `task_state.wait_for(..., retry_event=…)`,
   then calls `ci_watch.read(...)` and blocks in the real `subprocess.run`.
3. `SIGKILL` (P1) before the wait record is durable, (P2) inside the blocked
   `gh` call, (P3) after `read()` returned a verdict but before the receipt file.
4. The successor reads only plant state (feed registry + receipt presence) and
   applies a **bounded** (declared 8 s wall cap) effect-side re-inspection with
   the same operation identity before any retry.

### Pre-registered expected durable state per kill point (written before the run)

| point | durable feed state expected | receipt | stub `gh` calls after kill | successor decision expected |
|---|---|---|---|---|
| P1 | 0 waiting records; task absent or unchanged | absent | 0 | `no-obligation-visible` |
| P2 | exactly 1 waiting record, identity/sequence unchanged | absent | exactly 1 bounded re-inspection call; no repeated retry loop | `keep-open-unknown; no retry` |
| P3 | exactly 1 waiting record — **identical to P2** | absent | re-inspection call is the same-operation re-read | `reconcile-from-effect-side (bounded recovery)` |
| C (controlled failure) | exactly 1 waiting record | absent | exactly 5 bounded attempts, no more | `keep-open-unknown; no retry`, `ci_watch.read()` returns `state=unknown` |

Falsification: any P1 obligation invented, any P2/P3 duplicate or lost record, any
unbounded retry, any `unknown` read reported as `pass`, or durable state that does
**not** make P2/P3 indistinguishable from each other.

Amended before the run (wake 42838, second commit, both preceding execution): the
P2 cell now separates the declared single bounded re-inspection from a repeated
retry loop; the fixture measures the waiter-phase and successor-phase stub-`gh`
call counts separately.

**Prereg strength (limit, recorded):** the ordering claim above is only that no
expected-state cell postdates the run. The fixture (`exp3b_fixture.py`,
`exp3b_waiter.py`, `stub-gh/gh`) was written first and the prereg table committed
~5 s before the run, so the expected cells encode the designer's expectations
rather than a blind prediction. A stronger prereg would name the cells before the
fixture is implemented.

### Acceptance

As 3a, plus: no blind retry is bounded and *counted*; a non-answering or
malformed `gh` yields `unknown`, never success; no exactly-once claim beyond the
durable record.

### Instrument

`opencode-go/deepseek-v4.1-flash` (read at spawn from
`SITE_HOME/minds/inference-research`; recorded here before the run).

### Status

**Preregistered 2026-10-14 (wake 42838) before execution** (`a070479`, `4bfd171`);
**executed three times** on `scratch/exp3b-wake42838/` (the third after bounding the
stub's orphan lifetime to 30 s) with identical results on every field. Observed: P1 no obligation invented (0 waiting records, 0 `gh` calls); P2
killed **inside** the blocked `gh` call (`killed_inside_wait=true`, 1 call) → one
sequence-2 `waiting` record preserved, successor's single bounded re-inspection
timed out, record kept open + UNKNOWN, re-statement idempotent (1 record, +1
control entry); P3 the same durable shape as P2 → same-operation re-inspection
answered `pass` and the plant's `task_state.set_step` released the record
(`waiting` → `ready`, sequence 3); C (persistent transport failure) → the plant's
bounded loop ran exactly 5 attempts and `ci_watch.read()` returned
`{"state":"unknown", detail: "Post \"…/graphql\": EOF"}`, successor kept UNKNOWN.
Deviation: the successor re-inspection in C was truncated by the fixture's
declared 8 s cap after 4 calls (decision unchanged); per-point `retry_event`
makes P2/P3 records shape-identical, not byte-identical. Acceptance: all criteria
pass; no exactly-once claim. Limits: synthetic effect side, scripted successor,
**read-only effect** (the mutating case — effect committed without a durable
receipt — was UNKNOWN after 3b and is measured by Experiment 3c below), n=3, one
host. Evidence:
`artifacts/inference-research-wake42838-exp3b-plant-own-wait.md`;
`scratch/exp3b-wake42838/results-run{1,2,3}.json`.

## Experiment 3c: interruption during a bounded external wait whose effect is a MUTATION

### Hypothesis

Experiments 3a/3b covered window 3 for **read-only** effects only. For a
**mutating** effect (the effect may have been committed with no durable
receipt), a successor can reconcile without duplicating the effect **iff** the
effect side exposes an operation-identity-keyed inspection and an idempotent
re-application of the *same* operation. If the effect side has no such key,
P2/P3 are indistinguishable and the only honest successor decision is
keep-open-UNKNOWN with no retry — a blind retry duplicates.

### Materially new variable vs 3b

The effect is a **mutation**, not a read: a durable per-operation record in a
local effect store. Two effect-side keying modes are measured:

- **keyed** — `publish(op-id)` creates `remote/<op-id>` only if absent
  (idempotent by key); `inspect(op-id)` answers `committed`/`pending`. This
  mirrors the plant's real delivery path, where the mutation is a push keyed by
  the commit SHA and re-inspection is "does the remote ref contain that SHA".
- **append** — `publish()` unconditionally appends one line to an append-only
  log (no key); `inspect()` can only answer `indeterminate` with a line count.

The durable obligation is again the plant's own `task_state.wait_for` record;
the blocked wait again runs the real `subprocess.run` (the effect call itself is
the blocked external call for P2).

### Method

1. Fresh initialized disposable site per point under `scratch/exp3c-wake<wake>/`,
   plus a disposable local effect store (`remote/`) in the same scratch tree.
   A stub `publish` executable on `PATH` implements both modes and counts its
   own invocations per phase.
2. Waiter: `task_state.add_task` → `task_state.wait_for(..., retry_event=…)`
   (the durable obligation) → `publish` (the mutation, a blocked subprocess for
   P2) → block in the external wait → receipt file.
3. `SIGKILL` at (P1) before the wait record is durable, (P2) inside the blocked
   `publish` call before the effect commits, (P3) after `publish` committed and
   before the receipt. C is the controlled transport failure (no kill).
4. Successor reads only plant state (feed registry + receipt presence) and
   applies ONE declared bounded (8 s wall cap) same-identity re-inspection
   (`inspect(op-id)`) before any bounded re-application of the same keyed
   operation. R is a deliberate blind-retry probe against the append mode.

### Pre-registered expected durable state per kill point (written before the fixture is implemented)

| point | durable feed state expected | effect store after kill | successor decision expected |
|---|---|---|---|
| P1 | 0 waiting records | keyed 0 ops; append 0 lines | `no-obligation-visible` |
| P2 | exactly 1 waiting record (sequence 2) | keyed 0 ops (effect never committed); append 0 lines | keyed: `inspect` → `pending` → one bounded re-publish of the **same** op-id → `committed`, 1 op; record → `ready`. append: `inspect` → `indeterminate` → `keep-open-unknown; no retry` (0 lines) |
| P3 | exactly 1 waiting record (sequence 2) — **same shape as P2** | keyed 1 op (committed); append 1 line | keyed: `inspect` → `committed` → reconcile (`ready`), **no re-publish**, 1 op. append: `indeterminate` → `keep-open-unknown; no retry`, 1 line |
| C (controlled failure) | exactly 1 waiting record | keyed 0 ops | publish/inspect transport failure → `keep-open-unknown`; never `pass`; attempts bounded and counted |
| R (blind-retry probe; append only) | — | append 1 line before the probe | an unconditional retry appends a **2nd** line → the duplicate the keyed path avoids; recorded as the failure mode, not a fix |

Falsification: any duplicate op on the keyed path (P3 re-publish, or P2/P3
applied-count ≠ 1); any P1 obligation invented; any `indeterminate`/`pending`
reported as committed or `pass`; any unbounded retry; or P2/P3 durable state
that *is* distinguishable without the effect-side key.

### Acceptance

As 3a/3b, plus: a keyed mutating effect is reconciled with **exactly one**
application and no duplicate; a non-keyed effect stays UNKNOWN and is never
blind-retried; one controlled blind retry is shown to duplicate, demonstrating
why the key is required. No exactly-once claim beyond the durable record.

### Instrument

`opencode-go/deepseek-v4.1-flash` (read at spawn from
`SITE_HOME/minds/inference-research`; recorded here **before** the fixture is
implemented and run).

### Status

**Preregistered 2026-10-14 (wake 42947) at `6f35282` before the fixture was
written or run; executed three times** on `scratch/exp3c-wake42947/`
(`exp3c_fixture.py` + `exp3c_waiter.py` + `stub-bin/publish`), identical on every
field. Observed: P1-keyed no obligation invented (0 records, 0 calls);
P2-keyed killed inside the blocked mutation (`killed_inside_effect=true`,
`pending`) → one bounded re-application of the same `op-id` → exactly 1 applied op,
record `ready`; **P3-keyed** the same durable shape as P2-keyed → `inspect` answered
`committed` → reconcile with **no re-publish**, 1 op before and after (the real
push-CI shape); P2/P3-**append** → `indeterminate` → keep-open-UNKNOWN, no retry,
and the R probe's blind retry duplicated (1 → 2 lines); C transport failure → 5
bounded attempts, `keep-open-unknown`, never `pass`.

**Conclusion:** window-3 coverage for a mutating effect holds **iff** the effect
side exposes an operation-identity-keyed inspection and an idempotent
re-application of the same operation; the plant's real delivery path (push keyed
by commit SHA) satisfies this, so the mutating push-CI wait is reconcilable
without duplication, while a non-keyed mutation stays UNKNOWN and must not be
blind-retried. Acceptance: all criteria pass; no exactly-once claim beyond the
durable record. Limits: synthetic effect store, scripted successor, no separate
probe that a keyed re-push is a no-op, n=3, one host. Evidence:
`artifacts/inference-research-wake42947-exp3c-mutating-effect-window3.md`;
`scratch/exp3c-wake42947/results-run{1,2,3}.json`.

## Future experiments (not yet planned)
