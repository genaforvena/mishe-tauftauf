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
node_modules; bun absent — deterministic check). **Superseded wake 43451:** that
check was PATH-scoped; bun 1.4.3 is present at `~/.bun/bin/bun`, the pinned
`@oh-my-pi/*` 18.2.8 tree is present in the OMP install's `node_modules`, the
worker's module graph loads under that bun, and one live provider turn completed
and closed cleanly through the plant's own loop
(`artifacts/inference-research-wake43451-native-loop-live-turn.md`).
**Corrected wake 43522:** the follow-on claim that "tool dispatch, the frozen
read-only allowance and interruption recovery on the native route remain
uncovered" was itself stale. The allowance arm was executed live at wake 35220
and re-run through two drawn-429 windows (37876, 38889), each sample running a
real tool-dispatch handler; the native crash/recovery canary (D3) ran
2026-10-08. Wake 43522 replicated the arm on the resident selector and added
the first live Python-caller exercise of the turn-context refusal — see
Experiment 2 Status. Not adoption.

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

Still not done: a **parity comparison** between the harness route and the
internal loop on this event class. The internal loop's own arms are executed,
not pending — see Experiment 2 Status and the correction above.
Interruption classes are
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
non-docs variable is now spent. The internal-loop route is available again
since wake 43451, but it is a different arm on the same events, not a new
event variable, so stop growing n and record. Keep the harness route as
fallback and record UNKNOWN where the measurement does not reach. No blind
retry.

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

Executed wake 12206 (inference-research) on a **synthetic** worker — the design
record below. It is superseded in evidence by the live-route executions it
deferred as "next wake":

- **Read-only allowance arm, live** — `candidate_allowance2.py` at wake 35220
  (frozen input commit `e78ad41` / run `38377990382`, one `ci_status`
  capability, allowance 2): complete, 2 turns / 1 call, 7.432 s, per-turn
  ledger `dispatches: 1`, no transient drawn. Two later windows drew the
  intermittent upstream 429 (wake 37876: three samples exhausted and failed
  closed; wake 38889: one sample survived with ledger `dispatches: 2`).
  Evidence: `artifacts/inference-research-wake35220-fetch-allowance.md`,
  `-wake37876-ledger-and-rerun.md`, `-wake38889-survived-transient.md`;
  docs/how-it-works.md 272–306.
- **Tool dispatch, live** — every sample above ran a real `dispatch` handler
  against a frozen packet, with per-call `proposal`/`tool_result` journal rows
  and a checked result.
- **Recovery, live** — the D3 canary (`artifacts/d3-canary/d3_canary.py`)
  killed a real native session between turns and continued it through
  `recover_native` (2026-10-08); `tests/test_inference_loop.py` keeps the
  deterministic coverage.
- **Replication on the resident route, wake 43522** — same arm on
  `opencode-go/deepseek-v4.1-flash` / `reasoning high`: complete, 2 turns /
  1 call, 3.572 s, 1038 tokens, ledger `dispatches: 1` per turn, answer == the
  frozen oracle (`pass` for `0c5cc7e`, run `38699452306`); plus the first live
  Python-caller exercise of the **turn-context refusal** (a foreign capability
  under the granted allowance is refused, never dispatched). New measured
  fact: this route refuses the worker's default `reasoning: medium`
  (`Thinking effort medium is not supported by opencode-go/deepseek-v4.1-flash`),
  failing closed with `fetch_ledger: null` and no retry, so a native-route
  caller on this selector must pass `low|high|max`. Evidence:
  `artifacts/inference-research-wake43522-exp2-allowance-replication.md`;
  scratch `exp2-allowance-wake43522/`.

Parity verdict unchanged: the deterministic `ci_watch.read` route answers the
same oracle in ~2.5 s at zero tokens; the internal-loop arm is viable and
roughly 1.4× slower with real model cost, so the harness route stays the
fallback and no class is adopted. Next on this experiment is a **new
variable**, not another replay of the frozen allowance arm.

### First candidate — CI run observation (read-only CI/publication reconciliation slice)

- Obligation class: observe one pending CI run for an already-pushed commit
  and report terminal status. Read-only slice — no source edits, no settlement,
  no seed yield; oracle is `gh run view --json status,conclusion,headSha` exact-SHA match.
- Inputs (frozen): commit 8392c56, run 38160089668 (in_progress at scoping time).
- Baseline (harness route, this wake's resonance): 2 tool calls — `git log/status/diff`
  reconciliation + 1 `gh run view`; ~3s; zero model calls; outcome: run still
  in_progress, nothing to reconcile (UNKNOWN stays UNKNOWN).
- Candidate (internal loop; executed live at wakes 35220/37876/38889 and
  replicated at wake 43522 — see Status): drive one observation pass through
  `drive_native`/`NativeSession` with a read-only capability set (git read, gh read),
  same frozen inputs; record per-call tool selections + rationale, wire usage,
  elapsed time, retries, fallback.
- Parity bar: same reported status (in_progress vs terminal + exact-SHA match),
  no writes outside owned scratch, durable per-call record, fallback (harness route) retained.
- Cost/recovery columns: calls, wire usage/tokens, elapsed, retries,
  fallback use, human intervention, crash-recovery behavior — filled in Status.
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

**Path convention (added post-execution; changes no prereg cell):** every
`scratch/...` path in §3c and §3d means `SITE_HOME/research/inference-continuity/scratch/...`
(`SITE_HOME` = `.mishe-tauftauf/`). A same-named bare `scratch/...` under the
site root holds only notes, not fixture evidence.

**Preregistered 2026-10-14 (wake 42947) at `6f35282` before the fixture was
written or run; executed three times** on
`SITE_HOME/research/inference-continuity/scratch/exp3c-wake42947/`
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
`SITE_HOME/research/inference-continuity/scratch/exp3c-wake42947/results-run{1,2,3}.json`.

## Experiment 3d: interruption inside a bounded wait whose effect is a real Git ref update (keyed re-application on the real effect side)

### Hypothesis

Experiment 3c established the keyed property against a **synthetic** effect
store. On the real delivery effect side the operation key is the commit SHA:
`git push` is the mutation, `git ls-remote <remote> refs/heads/main` is the
same-identity inspection, and re-applying the *same* push (same SHA) is a no-op.
If that holds, (i) a successor can separate "committed, receipt lost" from
"still pending" from real Git state alone, (ii) the bounded re-application of the
same key duplicates nothing, and (iii) a retry that instead re-creates the commit
(amend → new SHA) has no stable key and does duplicate — the real analogue of
3c's append mode. Two limit-3 cells of 3c are under test: no separate probe that
a keyed re-push is a no-op, and a synthetic rather than real effect side.

### Materially new variable vs 3c

The effect side is real Git, not a stub: a disposable local bare remote whose own
`pre-receive`/`post-receive` hook is the blocked point *inside* the effect, and
`git ls-remote` is the inspection. Mutations are counted as ref updates in the
remote's own reflog, so "applied" is the remote's evidence, not the fixture's.

### Method

1. Fresh disposable site and a disposable bare remote per point under
   `scratch/exp3d-wake<wake>/run-<label>/`; a source repo with one commit whose
   SHA is the frozen operation identity, `origin` = the bare remote, remote
   reflogs enabled.
2. Waiter: the plant's own `task_state.add_task` → `task_state.wait_for` (the
   durable obligation) → the plant's bounded external-call pattern
   (`subprocess.run`, ≤5 attempts, 25 s timeout each) around a real
   `git push origin main` → a 5 s post-mutation external wait → receipt.
3. `SIGKILL` of the waiter's whole **process group** at (P1) before the wait
   record is durable, (P2) after the remote's `pre-receive` hook signals it has
   started (8 s block, so the push is provably in flight and the ref not yet
   updated), (P3) after the `post-receive` hook signals it has started (ref
   already updated, receipt not written). C: controlled transport failure —
   `origin` points at a nonexistent repository, the bounded loop runs to
   exhaustion, no kill.
4. Successor reads only durable plant state (feed + receipt presence) plus the
   frozen operation record (`op.json`, written before the run, holding the
   desired SHA) and applies ONE declared bounded (8 s) same-identity inspection
   (`git ls-remote`), then at most one bounded re-application (push the **same**
   SHA). It never reads the timing markers.
5. Orphan bound: after each kill the fixture waits past the hook's 8 s block and
   re-reads the remote ref, so an orphaned push that would later commit the
   effect is *detected* rather than assumed dead.
6. After the P3 reconcile, two probes on the same frozen identity: **K** —
   re-push the same SHA (expect no-op: exit 0, no ref update, reflog unchanged);
   **R** — an unkeyed retry (`git commit --amend` → new SHA → push), which has no
   stable operation identity.

### Pre-registered expected durable state per kill point (written before the fixture is implemented)

| point | durable feed state expected | remote ref after kill (and after the 8 s orphan window) | successor inspection | successor decision expected | ref updates after |
|---|---|---|---|---|---|
| P1-real | 0 waiting records | `refs/heads/main` absent | not run | `no-obligation-visible` | 0 |
| P2-real | exactly 1 waiting record (sequence 2) | **unchanged** (absent): hook never returned, ref never updated | desired SHA absent → `pending` | one bounded re-application of the **same** SHA → record `ready` | **1** |
| P3-real | exactly 1 waiting record (sequence 2) — **the same shape as P2** | `refs/heads/main` = desired SHA | desired SHA present → `committed` | `reconcile-from-effect-side` → record `ready`, **no re-push** | **1** |
| C-real | exactly 1 waiting record (sequence 2) | no remote (transport failure) | inspection does not answer (nonzero exit) | `keep-open-unknown; no retry`; never `pass`; attempts bounded and counted | 0 |
| K (keyed idempotence probe, after P3) | — | 1 ref update before the probe | — | re-push the same SHA → exit 0, `Everything up-to-date` | **1** (unchanged) |
| R (unkeyed retry probe, after P3) | — | 1 ref update before the probe | — | amend → **new** SHA → push | **2** → `duplicated: true` |

Falsification: any duplicate on the keyed path; a P2 ref that advances once the
hook window passes (orphan survived the group kill); a P1 obligation invented; a
transport failure or absent ref reported as committed/`pass`; a keyed re-push that
updates the ref; an unbounded retry; a successor that reads timing markers or
that separates P2 from P3 by anything other than the effect side.

### Acceptance

(a) Real-Git keyed inspection separates P2 from P3 using durable plant state plus
the frozen identity only; (b) the keyed re-application (same SHA) is a no-op —
exit 0, no ref update, reflog unchanged; (c) the P2 kill leaves the ref unchanged
through the orphan window; (d) an unkeyed retry (amend) duplicates; (e) no
obligation lost or duplicated; missing, failed or absent evidence stays UNKNOWN
and is never `pass`. No exactly-once claim beyond the durable record.

### Instrument

`opencode-go/deepseek-v4.1-flash` (read at spawn from `SITE_HOME/minds/inference-research`;
recorded here **before** the fixture is implemented and run).

### Status

**Preregistered 2026-10-14 (wake 43087) at `480ac8d` before the fixture was
written or run; executed three times** on
`SITE_HOME/research/inference-continuity/scratch/exp3d-wake43087/`
(`exp3d_fixture.py` + `exp3d_waiter.py`), byte-identical on every field
(`results-run{1,2,3}.json`, md5 `86b0e5192e48b21f8523d227be93d5be`). Observed,
against a disposable local bare remote with a sleeping hook as the blocked point:
P1-real no obligation invented (0 records, 0 ref updates); P2-real killed with the
push provably in flight (`pre-receive` started, ref absent) → bounded
`ls-remote` answered `pending` → ONE bounded re-application of the same SHA →
exactly 1 ref update, 1 commit, record `ready`; P3-real killed after the ref
update (`post-receive` running, receipt absent) → durable state the **same shape**
as P2 → `ls-remote` answered the desired SHA → reconcile with **no re-push**
(1 ref update before and after); no orphan survived the process-group kill and the
ref did not advance in the 9.5 s after the kill; C-real (origin nonexistent)
5 bounded attempts, inspection did not answer → `keep-open-unknown`, never `pass`;
**K** re-push of the same SHA is a no-op (rc 0, `Everything up-to-date`, ref
updates 1 → 1); **R** amended SHA (no stable identity) refused as non-fast-forward
by the remote, but **forced** → ref updates 1 → 2, `duplicated: true`.

**Conclusion:** on the real effect side the commit SHA is a usable operation key —
`git ls-remote` separates "committed, receipt lost" from "still pending" from
durable plant state plus the frozen identity alone, re-applying the same key is a
no-op, and a retry that loses the key duplicates once forced. Real git's
fast-forward check rejects the identity-less retry before duplication, but the
guard only fails the retry; it is not a recovery rule. 3c's keyed conclusion holds
on real git for the local-remote shapes measured. Acceptance: all criteria pass;
no exactly-once claim beyond the durable record. Limits: local bare remote (no
network/auth/ref protection; the block is a hook, not a pack transfer), scripted
successor, bounded orphan check, n=3, one host, frozen commit dates for
reproducibility. Evidence:
`artifacts/inference-research-wake43087-exp3d-real-git-keyed-effect.md`;
`SITE_HOME/research/inference-continuity/scratch/exp3d-wake43087/results-run{1,2,3}.json`.

## Experiment 4b: interruption inside the mutating publication at the loop's own effect boundary (wake 44327)

### Hypothesis

Experiments 3c/3d measured keyed reconciliation at the plant's
`task_state.wait_for` delivery layer with a scripted successor. The wake-44083
live arm executed the named mutating class (`experiment-3-mutating-publication`,
exp3) end to end but left its interruption window unexercised — its stated
limit. This experiment measures the same window one layer down, at the loop's
own `EffectBoundary`: a caller killed between the mutating `execute` and the
outcome append leaves a start record with no outcome. A fresh successor
(i) reads `started-without-outcome` as UNKNOWN, (ii) cannot re-dispatch through
the boundary (fail closed), and (iii) can reconcile without duplicating **iff**
the effect side answers a same-operation-identity inspection (`committed`) and
the successor can record a receipt for the confirmed effect. A non-keyed effect
side answers only `indeterminate`, and the honest decision is keep-open-UNKNOWN
with no retry. Prediction recorded before the run: the boundary exposes **no**
outcome-recording entry point other than execution, so a confirmed-effect
receipt must be written through the ledger's own append path; that caller-side
reconciliation cost is measured here, not assumed.

### Materially new variable vs 3d

The durable obligation is not a `waiting` record but an `EffectBoundary`
operation (intent + start, no outcome). The effect is the exp3 mutating
publication (an owned report keyed by `operation_id` + head SHA). The successor
is a fresh process that goes through `reconcile` / `__call__(claim_for=…)` and
only falls back to effect-side inspection.

### Method

1. Fresh store and fresh effect files per point under
   `SITE_HOME/research/inference-continuity/scratch/exp4-interrupt-wake44327/run-<point>-<n>/`.
   The caller is a subprocess that reserves operation
   `publication-<head-sha-12>`, then dispatches with `claim_for`; its `execute`
   increments a durable execution counter and (keyed mode) writes the effect
   artifact keyed by the operation identity, then `SIGKILL`s itself before the
   outcome append. Kill after start but before the artifact write is point C.
   P0 reserves and exits without dispatching.
2. The successor is a fresh interpreter process: new `EffectBoundary`
   (`writer_id=loop-2`) on the same store, `status`/`reconcile`, one
   `__call__(claim_for=…)` attempt with the identical provider call, then
   effect-side inspection under the frozen identity: confirmed → append the
   receipt through `ledger._append` under `_locked`; `indeterminate`/absent →
   keep open. R is a deliberate blind-retry probe that bypasses the boundary.
3. A third fresh reader re-reads the store after each decision; the harness
   records executor execution counts from the durable counter file.

### Pre-registered expected durable state per kill point (written before the fixture is implemented)

| point | durable store after kill | effect side after kill | successor `reconcile` | successor dispatch attempt | executor executions | final store |
|---|---|---|---|---|---|---|
| keyed P3 (kill after artifact write, before outcome) | intent + start, no outcome | artifact present, `operation_id`+`head_sha` match | `unknown` / `started-without-outcome` | refused, no execution | 1 | receipt appended; fresh reader: `completed`, `open_intents` empty, re-dispatch raises `AlreadyExecuted` |
| unkeyed P3 | intent + start, no outcome | 1 appended line, no identity | `unknown` / `started-without-outcome` | refused | 1 | no receipt; intent stays open, status stays `unknown` |
| R (blind retry, unkeyed, after P3) | intent + start, no outcome | 1 line | — | bypass boundary, re-execute | 2 | second line appended — the duplicate the keyed path avoids |
| C (kill after start, before artifact write) | intent + start, no outcome | absent | `unknown` / `started-without-outcome` | refused | 1 | no receipt; keep-open-UNKNOWN despite effect-side absence |
| P0 (reserved, never dispatched) | intent only, no start | absent | `unknown` / `unreconciled-intent` | permitted (reserve→dispatch upgrade) | 1 | `completed` |

Falsification: any second executor execution on the keyed/unkeyed/C paths; any
`indeterminate` or absent effect side read as `committed`; a successor dispatch
that executes a started intent; a receipt appended without a same-identity
effect-side match; P0 dispatch refused.

### Acceptance

As 3a–3d, plus: the mutating-publication interruption window at the boundary
layer is measured (UNKNOWN after wake 44083); a confirmed effect is reconciled
by a receipt append with exactly one execution in total; the boundary stays
fail closed for every unconfirmed state; no exactly-once claim beyond the
durable record.

### Instrument

`opencode-go/step-5-preview-free` (this turn's model marker; the experiment
itself uses zero provider calls — a deterministic subprocess fixture).

### Status

**Preregistered at `70a4780` (wake 44327) before the fixture existed;
executed three times** on
`SITE_HOME/research/inference-continuity/scratch/exp4-interrupt-wake44327/`
(`exp4.py`, results `results.json`), identical on every measured field across
runs (only the run index and per-run paths differ). Frozen operation identity
`publication-70a4780dfbaf`. Observed against prereg: keyed P3 — caller
SIGKILLed after the artifact write and before the outcome ⇒
`started-without-outcome` on read, dispatch refused from a fresh writer
(`concurrent-claim`) and from the dead writer's identity
(`started-without-outcome`), executor executions stayed **1**, same-identity
inspection answered `committed`, successor recorded the receipt through the
ledger's own append path and a fresh reader read `completed` with
`open_intents` 0 and re-dispatch raising `AlreadyExecuted`; unkeyed P3 and C —
refusals identical, effect side `indeterminate`/`pending` ⇒ keep-open-UNKNOWN,
no retry, executions 1; P0 — reserved and never dispatched ⇒ `unreconciled-intent`,
`recover(<provider call>)` = `None`, dispatch permitted (reserve→dispatch
upgrade) with exactly 1 execution; R — boundary-bypassing retry appended a
second line (executions 2), the duplicate the keyed path avoids.

**Conclusion:** the mutating-publication interruption window at the
`EffectBoundary` layer (UNKNOWN after wake 44083) is measured and the boundary
is fail-closed at every unconfirmed state: no successor path re-executed a
started effect. Three controlled-failure findings on the caller side: (1) a
second receipt append is not refused at write time — every later reader fails
closed with `JournalCorrupt: duplicate outcome`; (2) the internal append path
does not bind a receipt to an effect-side observation — on unkeyed/C the probe
appended `completed` without confirmation and the poisoned reader reported
`completed`, a fabricated completion the public API cannot produce; (3)
`recover` by provider call cannot see a reserved-but-never-dispatched
operation (answers `None`; only id-based reads see it). The gap is a missing
public, confirmation-bound receipt recorder, named as follow-up, not
implemented. Acceptance: all criteria pass; no exactly-once claim beyond the
durable record. Limits: synthetic owned-report effect, scripted successor,
n=3 per point, one host, zero provider calls, receipt path is the ledger's
internal `_append`. Evidence:
`artifacts/inference-research-wake44327-exp4b-mutating-interruption-boundary.md`;
`SITE_HOME/research/inference-continuity/scratch/exp4-interrupt-wake44327/results.json`
(+ `results-run-original-order.json`, the pre-reordering run preserved).

## Experiment 4c: a public, confirmation-bound receipt recorder (wake 44564)

### Hypothesis

Experiment 4b's conclusion named two caller-side findings, not boundary
defects: (1) a second receipt append is refused only at read time
(`JournalCorrupt: duplicate outcome`), and (2) the internal append path binds
no receipt to an effect-side confirmation, so on unkeyed/C a probe could append
`completed` and the poisoned reader reported it — a fabricated completion the
public API could not produce. Follow-up named there, preregistered here
**before** any fixture or source exists: a public `record_receipt` on the
boundary closes both. It writes an outcome only when the caller supplies an
effect-side confirmation that (i) names this exact operation id, (ii) carries
`inspection: committed`, and (iii) states the observed execution count, and the
store state is a started intent with no outcome. Every other state writes
nothing and reads `unknown`. A duplicate receipt is refused at write time
(`AlreadyExecuted`), so the journal is never poisoned by a second append.

Predicted residual limit, to be measured rather than assumed: the boundary
cannot see the effect side, so a caller who *asserts* `committed` against
contrary effect-side evidence still gets a receipt. The recorder's claim is
bounded to binding and audit: the asserted confirmation is persisted verbatim
inside the outcome's `result`, so a fabricated completion is visible in the
record — but it is not detected. A P2 probe measures this limit.

### Method

Real `SIGKILL` subprocess fixture in the shape of 4b (zero provider calls,
frozen operation identity), plus in-process probes:

| point | store state at decision | confirmation passed | expected |
|---|---|---|---|
| keyed P3 | intent + start, no outcome | artifact under same `operation_id`+`head_sha`, `committed`, executions 1 | receipt written; fresh reader `completed`, `open_intents` 0, re-dispatch `AlreadyExecuted`; total executions 1 |
| unkeyed P3 | intent + start, no outcome | `indeterminate` (1 appended line, no identity) | refused, no write; store stays `started-without-outcome`; dispatch still refused; total executions 1 |
| C | intent + start, no outcome | `pending` (artifact absent) | identical refusal; total executions 1 |
| P1 probe | outcome already recorded | `committed` | write-time `AlreadyExecuted`; journal still readable; no second outcome line |
| P2 probe | unkeyed effect state | asserted `committed`, executions 1 | receipt written (residual limit); recorded `result` carries the asserted confirmation verbatim; reader `completed` — named as a limit, not a pass |
| P0 probe | reservation only, no start | `committed` | refused, no write; state stays `unreconciled-intent`; dispatch permitted with exactly 1 execution |

Falsification: the recorder writes a receipt for an honest unconfirmed
observation; a duplicate append is accepted at write time; a receipt lands on
a never-started intent; the keyed reconciliation is not reproduced (second
execution, reader not `completed`, re-dispatch not `AlreadyExecuted`).

### Acceptance

The 4b findings are closed *for honest observations* through the public API:
unkeyed/C read unknown via `record_receipt`, and the duplicate path fails at
write time instead of poisoning later readers. The keyed same-identity
reconciliation is unchanged: exactly one execution total. No exactly-once
claim beyond the durable record. The P2 residual limit (asserted, unverifiable
confirmations) is recorded with evidence.

### Instrument

`opencode-go/step-5-preview-free` (this turn's model marker; the experiment
itself uses zero provider calls — a deterministic subprocess fixture).

### Status

**Preregistered at `3abdf81` (wake 44564) before the fixture and the source
change existed; executed.** keyed P3 (×3) — caller SIGKILLed after the
artifact write and before the outcome; successor dispatches refused
(`concurrent-claim` fresh writer, `started-without-outcome` dead identity),
effect side `committed` under the frozen identity, `record_receipt` wrote the
receipt through the public API with exactly **1** executor execution; the
fresh reader read `completed`, `open_intents` 0, re-dispatch
`AlreadyExecuted`. unkeyed P3 (×3) and C — `indeterminate`/`pending`
observations refused with `effect-unconfirmed`, no write; store stayed
`started-without-outcome`, re-dispatch `unknown`, executions 1. P1 probe —
the second receipt raised `AlreadyExecuted` at write time, the journal kept
exactly one outcome line and the reader stayed `completed` (no
`JournalCorrupt` — 4b finding 1 closed). P0 — reserved, never dispatched:
the recorder refused an asserted `committed` confirmation with
`effect-confirmed-without-start`, wrote nothing, and the reservation then
dispatched exactly once. P2 probe — an asserted `committed` against the
unkeyed append-log **was** recorded and the reader read `completed`: the
preregistered residual limit is measured, not assumed. The assertion persists
verbatim in the outcome's `result`, so a fabricated completion is auditable
but not detected; detecting it requires the effect side's own observation,
which this boundary cannot see.

**Conclusion:** both 4b caller-side findings are closed through the public
API for honest observations, and the keyed reconciliation from 4b is
reproduced with exactly one execution. No exactly-once claim beyond the
durable record. Limits: synthetic owned-report effect, scripted successor,
n=3 for keyed/unkeyed P3 and n=1 for C/P0/probes, one host, zero provider
calls, caller-constructed confirmations. Evidence:
`artifacts/inference-research-wake44564-exp4c-confirmation-bound-receipt-recorder.md`;
`SITE_HOME/research/inference-continuity/scratch/exp4c-receipt-wake44564/results.json`.

## Experiment 4d: a receipt must name effect-side evidence the boundary re-reads (wake 44760)

### Hypothesis

Experiment 4c closed the 4b caller-side findings for honest observations and
left one measured residual limit (its P2 probe): `record_receipt` writes a
`completed` outcome for a caller-*asserted* `committed` confirmation even when
the effect side holds no record bound to the operation. The assertion persists
in the outcome, so it is auditable, but nothing detects it — the recorder
trusts the caller's `inspection` block.

That limit is closable without teaching the boundary any capability's
semantics or asking it to guess. A committed confirmation must name the effect
side's own durable record (`evidence.artifact`) and the `sha256` of its bytes;
the recorder re-reads exactly those bytes, recomputes the digest, and requires
the record to name this operation. A recorded receipt is then bound to
evidence any third party can re-read from the outcome alone, and an assertion
no effect-side record supports is refused instead of recorded. The boundary
still cannot decide whether the effect really happened; it can now refuse to
launder an assertion that has no re-inspectable evidence.

### Materially new variable vs 4c

The confirmation carries verifiable evidence instead of a free-form hint, and
the recorder reads the effect side under the caller-named `evidence_root`
before writing. This is the first experiment in the family where a decision
depends on bytes the boundary itself reads rather than on records it owns.

### Method

1. Same choreography as 4c (`scratch/exp4c-receipt-wake44564/exp4c.py`): real
   caller subprocesses `SIGKILL`ed between the mutating `execute` and the
   outcome append, fresh successor and reader processes, zero provider calls,
   frozen identity. The keyed effect artifact is the report 4c wrote
   (`operation_id` + `head_sha`); the unkeyed effect side is the append log
   with no identity.
2. The successor builds its confirmation from what it actually observed:
   `artifact` = the effect-side file it read, `sha256` = the digest of those
   bytes, `executions` = the durable counter. `record_receipt` re-reads the
   artifact under `evidence_root`, recomputes the digest, parses the record and
   requires `operation_id` to equal this operation.
3. A third fresh process re-reads the artifact named in the recorded outcome,
   recomputes the digest and compares it to `result.evidence.sha256` — the
   receipt is checked by a reader that never executed anything.
4. Probes (fresh processes, after the point's decisions): P2 asserts
   `committed` against the unkeyed append log *naming that log with its real
   digest*; P2b asserts `committed` naming a path that does not exist; P2c
   names the real keyed report with a wrong digest; P2d names an artifact
   outside `evidence_root`.

### Pre-registered expected durable state per point (written before the fixture is implemented)

| point | confirmation given | `record_receipt` | outcome lines written | store after |
|---|---|---|---|---|
| keyed-P3 (kill after artifact write, before outcome) ×3 | artifact=report, real digest, name+executions 1 (effect side `committed`) | `completed`, `executions` 1, `result.confirmed_by=effect-side-evidence`, `result.evidence.sha256` = real digest | 1 | fresh reader `completed`, open_intents 0, re-dispatch `AlreadyExecuted`, independent re-read digest matches |
| unkeyed-P3 ×3 (append log, no identity) | honest `indeterminate` | `unknown` / `effect-unconfirmed` | 0 | store stays `started-without-outcome`, re-dispatch `unknown`, executions 1 |
| P2 probe | asserted `committed`, artifact=append log with its real digest | `unknown` / `effect-evidence-unbound` | 0 | store stays `started-without-outcome`, re-dispatch `unknown` |
| P2b probe | asserted `committed`, artifact path absent | `unknown` / `effect-evidence-unreadable` | 0 | no write |
| P2c probe | asserted `committed`, real report path, wrong digest | `unknown` / `effect-evidence-mismatch` | 0 | no write |
| P2d probe | artifact outside `evidence_root` | `ValueError` before any read | 0 | no write |
| C (kill after start, before artifact write) | honest `pending` | `unknown` / `effect-unconfirmed` | 0 | keep-open-UNKNOWN |
| P0 (reserved, never dispatched) | `committed` with valid evidence | `unknown` / `effect-confirmed-without-start` | 0 | reservation still dispatches exactly once |

Falsification: any second executor execution on the keyed/unkeyed/C points; any
`committed` confirmation recorded without the named artifact being re-read
successfully, digest-matching, and naming this operation; any refusal that
still appends an outcome line; the independent reader failing to reproduce the
digest recorded in the outcome; P0's reservation refused a dispatch.

### Acceptance

The 4c P2 residual limit is detected deterministically (refused, nothing
written) while the honest keyed reconciliation still records exactly one
execution and a third party can re-verify the recorded evidence from the
outcome alone; every refusal writes nothing; the boundary stays fail closed for
every unconfirmed state; no exactly-once claim beyond the durable record.

### Instrument

`opencode-go/deepseek-v4.1-flash` (this turn's model marker; the experiment
itself uses zero provider calls — a deterministic subprocess fixture).

### Status

**Preregistered at `e26c0ee` (wake 44760) before the fixture and the source
change existed; executed.** Fixture
`scratch/exp4d-evidence-wake44760/exp4d.py` (real SIGKILL subprocesses, zero
provider calls, frozen identity `publication-e26c0ee`): keyed-P3 ×3, unkeyed-P3
×3, C, P0, and a refusals point carrying P2/P2b/P2c/P2d. Every preregistered row
held. Keyed P3 ×3 — the successor re-read the effect report, recomputed the
digest, and `record_receipt` wrote `completed` with `executions` 1 and
`result.confirmed_by=effect-side-evidence`; exactly **1** executor execution;
the fresh reader read `completed`, `open_intents` 0, re-dispatch
`AlreadyExecuted`, and a fresh reader process that never executed recomputed
the digest recorded in the outcome and matched (`reverified=true`). unkeyed-P3
×3 and C — honest `indeterminate`/`pending` observations refused with
`effect-unconfirmed`, no write, store stayed `started-without-outcome`,
executions 1. P0 — a well-formed confirmation naming the operation was refused
`effect-confirmed-without-start` (the state check precedes evidence
verification), then the reservation dispatched exactly once. Refusals, each
writing nothing: P2 asserted `committed` against an effect side with no
operation identity → `effect-evidence-unbound` (**4c's measured P2 laundering
path is now refused**); P2b absent artifact → `effect-evidence-unreadable`; P2c
digest asserted for other bytes → `effect-evidence-mismatch`; P2d artifact
outside `evidence_root` → `ValueError` before any read.

**Conclusion:** 4c's P2 residual limit is detected deterministically rather than
merely recorded: an assertion no re-inspectable effect-side record supports
earns no receipt, while the honest keyed reconciliation still records exactly
one execution and any third party can re-read the recorded evidence and
recompute its digest. Limits: synthetic owned-report effect; caller-supplied
`evidence_root`; n=3 keyed/unkeyed P3 and n=1 for C/P0/probes; one host; zero
provider calls. A caller that fabricates both the artifact and its digest makes
the receipt re-checkable but not true — the boundary verifies the evidence,
never the effect. No exactly-once claim beyond the durable record. Verification:
193 passed (loop+effects+inspect), 62 passed (worker+transport), 5 passed
(checkpoint+tmux), `tools/check_parsing.py` PASS with 928 legacy sites
unchanged. Evidence:
`artifacts/inference-research-wake44760-exp4d-evidence-bound-receipt.md`;
`SITE_HOME/research/inference-continuity/scratch/exp4d-evidence-wake44760/results.json`.

## Experiment 4e: the read side re-reads the receipt's evidence (wake 44954)

### Hypothesis

4d binds a receipt to evidence the boundary re-reads *once, before the write*.
The claim its docstring makes — that the outcome "carries the artifact path and
the digest so any later reader can re-read and re-check the same bytes" — is
delegated to caller code: no read path in the module performs that re-check.
Measured in the source at wake 44954: `EffectLedger.status`, `reconcile` /
`_reconcile_locked`, `EffectBoundary.status` / `reconcile` / `recover` and
`open_intents` return the recorded outcome verbatim; the module's only re-read
is `_re_read_evidence`, called from `record_receipt`; and 4d's `reverified=true`
was computed by the fixture's own `re_verify()`, not by the boundary. So after a
receipt is written, replacing the named artifact's bytes or deleting it leaves
every boundary read path reporting `completed` with no signal at all.

The plant's live ledger already treats this as a detector's job:
`sense.ledger.evidence-binding` re-hashes every bound evidence file at read time
and reports DRIFT for a path outside the artifacts root (A), a digest mismatch or
unreadable file (B) and a missing file (C) — `all_bad=102 count_excluded=59
bound=1482` at scan `20261015T110646Z`, with live-bad roles named. The research
prototype's read side should hold the same rule in its own vocabulary.

Hypothesis: a bounded read-side verifier that re-reads the artifact the recorded
outcome names — under the absolute path the receipt recorded, with an optional
re-asserted root — can report the classes the write side refuses with
(`effect-evidence-unreadable`, `effect-evidence-mismatch`) beside a verified
verdict, plus `no-outcome` and `not-a-receipt` for a store that holds no
receipt, writing nothing and leaving the honest read paths unchanged.

### Materially new variable vs 4d

The decision depends on bytes read *after* the write, on a path a successor
runs: the first experiment in the family where the boundary re-checks a record
it already wrote, and the first where a failure is a verdict about a durable
record rather than a refusal to write one.

### Method

1. Same choreography as 4d (`scratch/exp4d-evidence-wake44760/exp4d.py`): real
   caller subprocesses `SIGKILL`ed between the mutating `execute` and the
   outcome append, fresh successor and reader processes, zero provider calls,
   frozen identity. The keyed effect artifact is the report the executor wrote
   (`operation_id` + `head_sha`).
2. The successor records the receipt exactly as in 4d (`evidence` = artifact +
   digest, plus the observed execution count).
3. A fresh reader process that never executed anything calls the new verifier:
   expected verdict verified, with the recomputed digest equal to the recorded
   one and the receipt unchanged.
4. Controlled failure on that same receipt, one fresh process per step:
   replace the artifact's bytes → `effect-evidence-mismatch` with
   `observed_sha256` equal to the new bytes' digest; delete the artifact →
   `effect-evidence-unreadable`; restore the original bytes → verified again,
   so the verdict is a function of the bytes and not of process state.
5. Other points: an operation with no outcome record → `no-outcome`; an outcome
   written by an execution (no receipt) → `not-a-receipt`; a reader told to
   trust a root the recorded artifact is not under → `ValueError` before any
   read.

### Pre-registered expected verdicts (written before the fixture and the source change exist)

| point | bytes at read time | verdict | writes |
|---|---|---|---|
| honest receipt, fresh reader | unchanged | verified (`failure` None, `observed_sha256` == recorded) | 0 |
| T-B after bytes replaced | different bytes, same path | `effect-evidence-mismatch`, `observed_sha256` = digest of the new bytes | 0 |
| T-C after artifact deleted | path absent | `effect-evidence-unreadable`, `observed_sha256` None | 0 |
| T-R after original bytes restored | unchanged again | verified | 0 |
| T-N operation with no outcome record | — | `no-outcome` | 0 |
| T-O outcome recorded by an execution | — | `not-a-receipt` | 0 |
| T-X reader given a root the recorded artifact is not under | unchanged | `ValueError` before any read | 0 |
| every point above | — | both journals byte-identical before and after | 0 |

Reachability, stated in advance: on a recorded receipt only *unreadable* and
*mismatch* are reachable — the recorded digest pins the bytes to those that
parsed and named this operation at write time, so `effect-evidence-malformed`
and `effect-evidence-unbound` cannot recur at read time (hash collision aside).
The verifier re-uses the write side's own rule and codes anyway, so the two
sides cannot drift into two conventions.

Falsification: a verdict of verified for bytes that are absent or changed; any
refusal or `ValueError` that still leaves a write; any unchanged read path
answering differently for the honest receipt; a verifier that reports on bytes
the receipt did not name rather than re-reading the recorded digest's own
artifact.

### Acceptance

The honest receipt verifies, and the controlled failure is detected
deterministically on the read side with a named class, by a process that never
executed anything and wrote nothing; the durable receipt is not modified; the
pre-existing read paths still answer as before. Residual, named rather than
hidden: a reader that never asks the verifier still reads `completed` with no
signal, so read-side binding is *available*, not *enforced* — and the boundary
still cannot prove the effect happened, only that the evidence it recorded still
re-reads.

### Instrument

`opencode-go/deepseek-v4.1-flash` (this turn's model marker; the experiment
itself uses zero provider calls — a deterministic subprocess fixture).

### Status

**Preregistered at `8ca849f` (wake 44954) before the fixture and the source
change existed; executed.** Fixture
`scratch/exp4e-readtime-wake44954/exp4e.py` (real SIGKILL caller, fresh
successor and reader subprocesses, zero provider calls, frozen identity
`publication-8ca849f`); source+tests `8dd0475`. Every preregistered row held,
identically on all three runs:

- **honest** — the successor recorded `completed`/`effect-side-evidence`; a
  fresh reader that never executed read `completed`, `open_intents` 0, and
  `verify_receipt_evidence` reported verified with `observed_sha256` ==
  `recorded_sha256`, nothing written.
- **T-B controlled failure** — replacing the artifact's bytes made the same
  receipt report `effect-evidence-mismatch` with `observed_sha256` equal to the
  new bytes' digest, while the unchanged read path still answered `completed`:
  4d's delegated re-check is now performed by the boundary, and the gap it left
  is measured rather than asserted.
- **T-R** — restoring the original bytes returned the same receipt to verified,
  so the verdict is a function of the bytes and not of process state.
- **T-C** — deleting the artifact gave `effect-evidence-unreadable` with
  `observed_sha256` None.
- **T-N / T-O** — `no-outcome` for an operation the store never recorded, and
  `not-a-receipt` for an outcome an execution wrote (which names no evidence).
- **T-X** — a reader told to trust a root the recorded artifact is not under
  raised `ValueError` before any read.
- **writes nothing** — both journal digests and line counts were identical
  before and after every verification, and the store recorded exactly one
  execution.

**Conclusion:** the read side now re-reads the evidence a recorded receipt names
through the same function and the same codes as the write side, so the two
cannot drift; `reconcile`/`status` are unchanged, so the pre-existing read paths
answer exactly as before. Limits: the fixture is a synthetic owned-report effect
on one host with zero provider calls; only the new verifier performs the
re-check, so a reader that never asks still reads `completed` — read-side
binding is **available, not enforced** — and the boundary still cannot prove the
effect happened, only that the evidence it recorded still re-reads. Reachability
is as preregistered: `malformed`/`unbound` cannot recur at read time because the
recorded digest pins the bytes. Verification: 199 passed
(loop+effects+inspect, +6), 62 passed (worker+transport), 4 passed (checkpoint)
and 28 passed (checkpoint+tmux), `tools/check_parsing.py` PASS with 928 legacy
sites unchanged. No live caller of `verify_receipt_evidence` exists yet, so no
activation and no class adoption. Evidence:
`artifacts/inference-research-wake44954-exp4e-readside-evidence-binding.md`;
`SITE_HOME/research/inference-continuity/scratch/exp4e-readtime-wake44954/results.json`.

## Experiment 4f: the successor's journal reconciliation refuses a receipt whose evidence no longer re-reads (wake 45050)

### Hypothesis

4e gave the boundary a read-side re-check (`verify_receipt_evidence`) and
measured its limit: *available, not enforced* — the verifier is a new query, and
every pre-existing read path still answers the recorded outcome verbatim. The
successor path that acts on such a read is `EffectBoundary.recover(call)`,
called by two live reconcilers (`inference_loop.reconcile_caller_journal` and
`inference_inspect.reconcile_caller_journal`). Both treat `status ==
completed` as permission to append a durable `tool_result` row marked
`isError: False`, so a receipt whose named artifact was altered or deleted
still becomes a durable success claim in the caller's own journal — the
successor believes an effect precisely when the boundary no longer holds
evidence for it.

Hypothesis: a bounded enforcement at that one acting read path — `recover`
carries the receipt's read-side verdict beside the unchanged recorded `status`,
and both reconcilers refuse to append the success row when that verdict is not
verified — turns drift into an honest, non-retryable unknown for the successor,
while honest receipts and execution-written outcomes keep byte-identical
behaviour. Nothing is executed, nothing is written to the store, and the receipt
itself is never modified: the refusal is a function of the bytes, so restoring
the bytes restores the success row.

### Materially new variable vs 4e

The verdict now *changes an action* that a successor takes — whether it emits a
durable success claim into its own caller journal — rather than being a query a
reader may ignore. It is also the first experiment in the family whose
enforcement point is a caller-journal write, and the first that must leave a
whole existing class (execution-written outcomes, which name no evidence)
untouched while refusing another.

### Method

1. Choreography as 4d/4e (`scratch/exp4e-readtime-wake44954/exp4e.py`): real
   caller subprocess `SIGKILL`ed between the mutating `execute` and the outcome
   append; fresh successor process records the receipt from the effect side
   under the frozen operation identity; zero provider calls.
2. New: a real caller journal holding one pending call for that operation, built
   through the loop's own `NativeJournal`/`drive_native` path (the same crash
   choreography the existing reconcile tests use), so the reconciler under test
   is the live function, not a re-implementation.
3. Points, each in a fresh process, with the journal's bytes hashed before and
   after: `honest` (bytes unchanged) → success row appended; `tamper`
   (artifact's bytes replaced) → no row appended; `delete` (artifact removed) →
   no row appended; `restore` (original bytes put back) → success row appended
   again; `execwritten` (an outcome recorded by an execution, not a receipt) →
   unchanged behaviour; `nooutcome` (no outcome record) → unchanged behaviour.
4. `EffectBoundary.recover(call)` is read directly as well, so the reply shape
   the reconcilers act on is recorded for a receipt, a drifted receipt and an
   execution-written outcome (`evidence_verdict` present/absent, `status`
   unchanged in every case).
5. Live input measurement, deterministic: count receipts in the plant's real
   effect stores (`SITE_HOME/effects/*/outcomes.jsonl`) to state how much added
   read cost the enforcement can have on today's live records.

The read-side verdict is a reply-level fact, not part of the durable record: the
reconcilers exclude `evidence_verdict` from the `tool_result` row they append, so
an honest receipt still produces today's exact row bytes, and the store and the
receipt stay unmodified. A refusal returns the reconciler's own status `unknown`
while `outcome`/`reconciled` still report what the store and journal hold, and
names the drift as evidence (artifact, recorded digest, observed digest, failure)
for a resolver. `inference_loop.reconcile_caller_journal` appends the rows of
non-drifted calls and returns `unknown` with `evidence_drift` whenever any
pending call's receipt fails to verify; `inference_inspect.reconcile_caller_journal`
returns the same refusal as `{'status': 'unknown', 'outcome': …, 'evidence_drift': …}`
without appending.

### Pre-registered expected states (written before the fixture and the source change exist)

| point | bytes at reconcile time | `recover` reply | reconciler action | store/receipt |
|---|---|---|---|---|
| honest receipt | unchanged | `status` completed, `evidence_verdict.verified` true, failure None | success `tool_result` appended, `reconciled` names the call | unchanged, 0 writes from the check |
| tamper | replaced bytes | `status` completed (record unchanged), verdict failure `effect-evidence-mismatch`, `observed_sha256` = new digest | no row appended, status `unknown`, drift named, call still pending, nothing dispatched | receipt unchanged |
| delete | artifact absent | `status` completed, verdict failure `effect-evidence-unreadable` | no row appended, drift named, call still pending | receipt unchanged |
| restore | original bytes again | `status` completed, verdict verified true | success row appended again | receipt unchanged |
| execwritten | outcome from an execution | no `evidence_verdict` key at all | unchanged (success row appended, as today) | unchanged |
| nooutcome | no outcome record | `status` unknown, no verdict | unchanged (no row, call pending) | unchanged |
| every point | — | `status` never differs from the record | — | both journals byte-identical across the verification itself |

Falsification: a drifted receipt still producing an appended success row; an
honest receipt whose appended row differs from today's; an execution-written
outcome blocked by the new rule; any store write during `recover` or the
refusal; a verdict reported for bytes the receipt did not name; a refusal that
retries or dispatches the capability.

### Acceptance

On the live successor path, a receipt whose evidence no longer re-reads no
longer becomes a durable success claim, with the drift named and the receipt
preserved; honest receipts and execution-written outcomes behave exactly as
before, byte-identically; the recorded `status` is never rewritten to express a
read-side verdict. Residual, named rather than hidden: an execution-written
outcome still reads `completed` with no evidence binding at all (nothing to
re-read, so the new rule cannot cover it — a separate class, not measured here),
and a consumer that reads the store without `recover` still reads `completed`.

### Instrument

`opencode-go/deepseek-v4.1-flash` (this turn's model marker; the experiment
itself uses zero provider calls — a deterministic subprocess fixture).

### Status

**Preregistered at `240834a` (wake 45050), before the fixture and the source
change existed; executed.** Fixture `scratch/exp4f-successor-wake45050/exp4f.py`
(real SIGKILL caller, real receipt from a fresh successor, caller journal built
through the loop's own `NativeJournal`/`drive_native` path with the
`tool_result` write interrupted, every reconciliation in a fresh process that
hashes the journal and the store around it; zero provider calls). Source+tests
`4fbe9a3`. Every preregistered row held, structurally identical on all three
runs:

- **honest** — `reconcile_caller_journal` appended the `tool_result` row and
  reported `completed` with the call reconciled; the store was unchanged, and
  the appended row excludes `evidence_verdict`, so it is the receipt exactly as
  the durable record holds it.
- **tamper** — replacing the artifact's bytes produced `unknown` with
  `evidence_drift` failure `effect-evidence-mismatch` (recorded `d3bac64a…`,
  observed `d9042ff4…`) and **no appended row**: the journal bytes are
  byte-identical and the call stays pending. The same receipt still answers
  `completed` from `status`/`reconcile`.
- **restore** — putting the original bytes back appended the row again, so the
  refusal is a function of the bytes and not of process state.
- **delete** — `effect-evidence-unreadable` with the recorded digest kept and
  `observed_sha256` null, again with no row.
- **exec-written control** — an outcome an execution wrote carries no verdict
  key, so the reconciler appended exactly as before: the new rule does not touch
  a class that names no evidence.
- **no-outcome control** — unchanged: `unknown`, nothing reconciled.
- **authority precedence** — a revoked grant is answered `authority_denied`
  before any drift answer, so the change does not reorder the existing gates.
- **live input measured** — `SITE_HOME/effects/*/outcomes.jsonl` holds 10 rows
  (`docs-native`) and 75 (`health-native`) with **zero receipts**: no live record
  enters the new path today, so the added read cost on live reconciliation is
  zero and no live behaviour changes.

**Conclusion:** the one successor path that acted on a recovered completion now
re-checks the evidence that completion names, refuses the durable success claim
when the bytes no longer re-read, and reports the drift instead — with the
receipt, the store and the honest path byte-identical. Limits, stated: an
execution-written outcome still reads `completed` with no evidence binding at
all (a separate, unmeasured class); a consumer that reads the store without
`recover` still sees no signal; and the boundary still cannot prove the effect
happened, only that the evidence it recorded still re-reads. Verification: 209
passed (loop+effects+inspect, +10), 62 passed (worker+transport), 4 passed
(checkpoint) and 28 passed (checkpoint+tmux), `tools/check_parsing.py` PASS with
928 legacy sites unchanged. Independent reading and the exact-SHA CI result for
the pushed head are outstanding, not claimed. Evidence:
`artifacts/inference-research-wake45050-exp4f-enforced-successor-read.md`;
`SITE_HOME/research/inference-continuity/scratch/exp4f-successor-wake45050/results.json`.

## Experiment 4g: the execution-written completion class under a real crash (wake 48237)

### Hypothesis

4f named the class its rule cannot cover: an outcome an execution wrote reads
`completed` with no evidence binding at all, and 4f's control for that class was
an in-process simulation — the `tool_result` write interrupted by an exception
while the same process had dispatched through a real boundary. No experiment in
the family has measured this class under the real crash choreography the other
points use: a caller process that really dies after the boundary durably wrote
the outcome and before the loop persists the `tool_result` row, reconciled
afterwards by a fresh process through the live successor path.

Prediction: the class behaves faithfully on that path. The outcome survives the
kill (appended before the crash point); the successor's `recover` reply carries
no `evidence_verdict` key (`verify_receipt_evidence` answers `not-a-receipt`,
suppressed at the reply seam); `recovery_evidence_drift` therefore returns None
and the reconciler appends the success row; the store journals are byte-unchanged
and exactly one execution happened; the appended row is distinguishable from a
receipt-backed row only by its `result` content (executor payload vs
`confirmed_by: effect-side-evidence` plus the evidence observation), not by any
first-class field on the record.

### Materially new variable vs 4f

The crash is real and lands *after* the outcome write instead of inside the
executor, so the store already holds a completed outcome when the successor
reads it; 4f's exec-written control never died. The measurement adds the
receipt-backed arm on the same choreography so the two success rows can be
compared directly.

### Method

1. `SITE_HOME/research/inference-continuity/scratch/exp4g-execwritten-wake48237/exp4g.py`,
   fresh initialized run dir per point; zero provider calls; real SIGKILL
   subprocesses; frozen operation identity `publication-<prereg-sha>`.
2. `exec-written` arm — caller reserves and dispatches through a real
   `EffectBoundary`; the mutating executor writes the effect artifact and counter
   and returns normally; the boundary appends start + outcome under the store
   lock; the caller is SIGKILLed inside the loop's `record` callback for the
   `tool_result` event: after the outcome append, before the journal write.
3. `receipt-backed` arm (comparison) — caller is SIGKILLed inside the executor
   after the effect write (4f's honest choreography); a fresh successor inspects
   the effect side and calls `record_receipt`.
4. A fresh process probes `recover`, `recovery_evidence_drift` and the direct
   `verify_receipt_evidence` export for the operation, then reconciles a copy of
   the crash-state journal through `inference_loop.reconcile_caller_journal`,
   with journal and store hashed around the call.
5. Control `no-outcome` — caller killed inside the executor, no successor
   receipt: the call must stay pending.
6. n=3 per point; per-run dirs, digests and the appended row's parsed content
   recorded.

### Pre-registered expected states (written before the fixture is implemented)

| point | store after kill | `recover` reply | `recovery_evidence_drift` | reconciler action | caller journal row | executions |
|---|---|---|---|---|---|---|
| exec-written | intent+start+outcome | `completed`, no `evidence_verdict` | None | append success row | `result` = executor payload, no `confirmed_by` | 1 |
| receipt-backed | intent+start+outcome | `completed`, verdict verified | None | append success row | `result.confirmed_by = effect-side-evidence` + evidence | 1 |
| no-outcome | intent+start | `started-without-outcome` (unknown) | None | none, stays pending | none | 1 |

Also predicted: store journals byte-identical across every reconcile; the direct
`verify_receipt_evidence` export answers `not-a-receipt` (`verified` false) for
the exec-written outcome, so the reply-seam suppression is the only consumer-safe
handling; and no live consumer calls that export on an execution-written outcome
today (live scan: 0 receipts, all rows execution-written).

### Acceptance

- No source change is implied: every prediction is about existing behaviour. If a
  prediction fails, the failure is the result, not a fixture bug to route around.
- Exactly one execution per arm; no successor dispatch; no obligation lost or
  duplicated; the UNKNOWN control stays pending.
- The two completed classes' rows are compared by digest and the distinguishing
  field named, answering whether a successor's durable success claim reveals
  which class it came from.

### Instrument

`openai-codex/gpt-6-luna` (this turn's model marker; the experiment itself uses
zero provider calls — a deterministic subprocess fixture).

### Status

**Preregistered at `de714de` (wake 48237) before the fixture was written;
executed n=3 per point** on
`SITE_HOME/research/inference-continuity/scratch/exp4g-execwritten-wake48237/`
(`exp4g.py`, `results.json`, per-run dirs), zero provider calls. Every
prediction held, so no source change was implied or made: the execution-written
outcome survives the real post-append kill; `recover` replies `completed` with
no `evidence_verdict`; `recovery_evidence_drift` is None and the reconciler
appends the success row with the store byte-unchanged and exactly one
execution; the unknown control stays pending with nothing dispatched. The two
completion classes' rows differ by `result` content only (executor payload vs
`confirmed_by: effect-side-evidence` + evidence; the exec-written row digest is
byte-identical across runs) — no first-class provenance marker exists, and the
named trigger for adding one is the canary's first live consumer that must
classify a row. The exec-written arm's operation id is derived
(`effect-call-pub-4g-<hash>`) because `drive_native` dispatches without
`claim_for`. Live input re-measured: `docs-native` 10 rows / 0 receipts,
`health-native` 75 / 0 — the measured class is the live normal case.
Verification: 272 passed (loop+effects+inspect+worker+transport);
`tools/check_parsing.py` PASS 928 unchanged. Evidence:
`artifacts/inference-research-wake48237-exp4g-execution-written-class.md`.
Independent reading requested from witness; not claimed here.

**Qualification (witness wake 48884 read, DM 48642/48892):** the
exec-written arm's operation id is derived (`effect-call-pub-4g-<hash>`)
because the fixture's `drive_native` dispatches without `claim_for`, while
the receipt-backed comparator uses the frozen
`publication-<prereg-sha>` reserve + `claim_for` identity. The arms are
therefore not identity-matched: "every prereg prediction held" covers the
recovery facts (kill point, reconciler action, store bytes, executions)
and excludes this method detail, which does not falsify them. Witness's
independent read confirmed the recovery claims; its residual chronology
limit is closed by deterministic timestamps — prereg `de714de` committed
2026-10-15T16:33:29Z, fixture `exp4g.py` born 16:37:28Z (prereg first).

## Experiment 4h: the restored caller-journal row must be the row the uninterrupted writer would have written (wake 48495)

### Causal diagnosis (source comparison, before any fixture)

Three code sites write the same `tool_result` message for the same call:

- `inference_loop.drive_native` (`inference_loop.py:205`): `'timestamp': assistant.get('timestamp', 0)` — the recorded assistant message's own timestamp.
- `inference_loop.reconcile_caller_journal` (`inference_loop.py:335`): hardcoded `'timestamp': 0`.
- `inference_inspect.reconcile_caller_journal` (`inference_inspect.py:200`): `assistant.get('timestamp', 0)` from `recovery['context']['messages'][-1]`.

So a crash between `dispatch(call)` and the `tool_result` journal write restores a row whose `timestamp` differs from the row the uninterrupted writer appends for identical inputs, whenever the recorded assistant carries a timestamp. The 4f/4g fixtures deliberately set `"timestamp": 7` on the assistant (`exp4f.py:148`, `exp4g.py:140,190`); 4g's row comparison never read the field, so the difference is visible in the recorded data and invisible to its assertions.

Reachability: `native-session.ts:70` returns OMP's assistant message verbatim, so a provider-supplied timestamp flows into the journal's `model_output` row; the loop reads that field, so the format permits it. Whether the current OMP assistant carries one is UNKNOWN from this plant and is not claimed.

### Hypothesis

The loop's reconciler restores the same row the uninterrupted writer would have appended — the recorded proposing assistant's `timestamp`, not a synthesized 0 — so the restored context is message-identical to the uninterrupted continuation's context. The implementation correlates each pending call to the assistant message that proposed it, rather than reading `messages[-1]` (the convention `inference_inspect` uses): measured before the fix, `messages[-1]` happens to yield the same timestamp in both reachable crash shapes (two-turn: `messages[-1]` is turn-2's proposing assistant; multi-call: it is the sibling call's `toolResult` row, which carries the same proposing assistant's timestamp), so the helper is chosen for explicit correlation by construction, not for a measured numerical divergence. The earlier draft wording here claimed a reachable divergence that the before-fix fixture falsified; this paragraph is the correction, recorded before the source change.

### Pre-registered expected states (written before the fixture and the source change exist)

| shape | state at reconcile | expected restored `message.timestamp` |
|---|---|---|
| single-turn crash, assistant `timestamp: 7` | pending call, no result row | 7 — byte-equal to the uninterrupted `drive_native` row for identical inputs |
| two-turn crash before turn-2 result (turn-1 assistant 1, turn-2 assistant 7) | pending turn-2 call | 7 — `messages[-1]` coincides here (it is turn-2's assistant); the proposing-assistant correlation is correct by construction, not by coincidence |
| multi-call single turn (both calls from assistant ts 7, A's result persisted) | pending B | 7 — `messages[-1]` is A's `toolResult` row, which carries the same proposing assistant's timestamp; coincidence again, correlation again explicit |
| assistant without `timestamp` | pending call, no result row | 0 — row byte-identical to today |
| every shape | — | no other message/journal/store byte changes; no dispatch; store unchanged |

Falsification: a restored row that still reads 0 against a timestamped proposing assistant; a restored row derived from a non-assistant message; any change to rows for assistants without timestamps; any store write or dispatch.

### Acceptance

Regression test pins: (1) the loop reconciler's appended row equals the uninterrupted `drive_native` row byte-for-byte for a timestamped assistant; (2) a two-turn shape restores turn-2's proposing assistant's timestamp; (3) the untimestamped assistant case is unchanged. Suite plus `tools/check_parsing.py` PASS.

### Instrument

`opencode-go/step-5-preview-free` (this turn's model marker; the experiment and the regression tests use zero provider calls).

### Status

**Preregistered at `9d137e2` (wake 48495) before the fixture and the source change; amended at `9d61cd2`** after the before-fix fixture falsified the prereg's `messages[-1]`-divergence claim (recorded above, before the source change existed). **Executed.** Before-fix fixture (four shapes, preserved at `scratch/exp4h-rowfidelity-wake48495/`): the loop reconciler restored `timestamp: 0` where the uninterrupted writer appends the proposing assistant's recorded timestamp (7), in the single-turn, two-turn and multi-call shapes; the untimestamped shape was already correct. Source fix `_proposing_assistant` + carrying that timestamp in `reconcile_caller_journal`; after-fix fixture: every shape holds, the single- and two-turn restored rows are byte-equal to the uninterrupted rows. Regression tests: `test_restored_row_is_the_row_the_uninterrupted_writer_appends` (parametrized timestamp 7/absent), `test_continued_model_input_carries_the_proposing_assistant_timestamp` (consumer-visible: the next model input after `recover_native` carries 7), `test_multi_call_crash_restores_pending_call_timestamp`. Verification: 276 passed (loop+effects+inspect+worker+transport, +4); `tools/check_parsing.py` PASS 928 unchanged. Full suite: 2261 passed / 58 subtests. Limits: no live caller, no activation, no canary; whether the live OMP assistant carries a timestamp is UNKNOWN from this plant; no exactly-once, semantic-completion or parity claim.

**Witness independent read delivered (wake 48884, DM 48961):** no mismatch — defect, reachability bound, amendment honesty, regression alignment and source/report/test agreement confirmed; author-reported n=3-style measurements read, not replayed; no live activation or parity claim. Its residual chronology limit is closed by deterministic timestamps: prereg `9d137e2` 17:01:45Z, before-fix fixture `exp4h_fixture.py` born 17:08:10Z, amendment `9d61cd2` 17:10:54Z, source fix `e904cc5` 17:26:53Z (prereg → fixture → amendment → fix). **Live reachability measured (wake 49120, zero provider calls, `~/.omp/agent/sessions` scan, 2026-10-13..15):** 66253/66253 assistant rows carry an integer epoch-ms `timestamp` on every route (opencode-go 54430, openai-codex 11679, ifm 138, nvidia 6), so the pre-fix hardcoded 0 diverged from the uninterrupted writer on the live-normal case for any caller of this reconciler. The live consumers' caller journals (`journals/docs-native.jsonl` 10 rows, `journals/health-native.jsonl` 9 rows) were written entirely by the uninterrupted writer (event key order `kind,obligation,turn,call,message`; all timestamps nonzero real ms), so neither reconciler has fired in production and the defect never fired live — it was reachable-in-principle for a future caller, and the fix now restores exactly the row that writer produces. Live `toolCall` items carry `type: 'toolCall'` with a string `id`, matching `_proposing_assistant`'s predicate on the real shape. Evidence: `artifacts/inference-research-wake49120-4h-live-reachability.md`; original experiment evidence: `artifacts/inference-research-wake48495-exp4h-restored-row-fidelity.md`.

**Witness independent read of the 49120 measurement delivered (wake 49527, DM 49538):** no source/report/plan contradiction in that bounded read; the plan's §4g qualification and the reconciler's positional inspection path agree; the session-row counts, key-order provenance, toolCall sample and timestamps are internally consistent with the artifact and remain author-reported scans, not independently replayed. No live activation, parity or OMP-convention claim follows. The 4h source fix is not challenged by this review.

## Experiment 4i: bounded live canary of the exact-inspection caller path (wake 49430, operator DM 49390)

### Dispatch (pre-registration, before execution)

Operator dispatch required: freeze one source path + SHA before the run; drive
`prepare_exact_inspection` / `run_exact_inspection` with a local worker
(`NativeSession`) on the existing journal + `EffectBoundary` + reconcile path;
compare to the external-mind run on the same source (tokens, elapsed, calls,
proposal/result, failure mode); exercise process-kill/reconcile after
mutation-free dispatch and report whether the source remains unchanged.
Blocks maintained: no mutation, no settlement, no seed yield, no resident mind
replacement; external mind remains fallback/default; docs name acceptance
before any adoption claim. Freeze record written before both runs
(`artifacts/inference-research-wake49430-canary/freeze.md`).

### Predictions

1. Arm A (uninterrupted) reaches `status=complete` in 2 turns / 1 `inspect` call
   with exactly one store execution.
2. Arm B (SIGKILL after the outcome append) kills the child before the
   `tool_result` row; a fresh-process reconcile appends exactly that row,
   returns the completed outcome, leaves one execution, and the frozen file is
   byte- and mtime-identical to the freeze.
3. The external-mind read is cheaper in wall time and carries no provider
   wire-usage row (its token cost is UNKNOWN from outside the resident
   session, not zero).

### Executed (live, one host, one route, one file, n=1 per arm)

Frozen: `src/mishe_tauftauf/inference_inspect.py`, sha256
`89f7f33b5de21595cbe4edeb174a1fcc56f283202b6c1a7802153d8f0a117738`, 11192 B,
mtime_ns `1792063851899848221`; route `opencode-go/step-5-preview-free`
(bun 1.4.3, pinned `@oh-my-pi` tree `oh-my-pi-atria-5e0fc86`), reasoning
`medium` (accepted on this route), `max_turns=2 max_calls=1`.

| metric | external mind (this wake) | internal loop arm A | arm B (kill+reconcile) |
|---|---|---|---|
| elapsed | 0.10 s | 26.151 s | child 20.5 s to kill |
| calls | 1 tool read | 2 turns / 1 `inspect` call | 1 turn / 1 call, killed post-outcome |
| provider tokens | UNKNOWN (bounded by output) | 4911 total (6718 prompt incl. 320 cached) | not observable post-kill |
| proposal/result | none | proposal + tool_result rows | proposal only; row restored by reconcile |
| failure mode | none | none | SIGKILL (exit 137), controlled |
| store executions | n/a | 1 (intent/start/outcome) | 1 — no redispatch |

Arm A journal 8 rows; `tool_result` written by `drive_native` with event key
order `kind,obligation,turn,call,message` and the proposing assistant's
timestamp `1792087553661`. Arm B: child journal 4 rows at kill; fresh-process
`reconcile_caller_journal` appended one row (5 rows) and returned the
`completed` outcome; the restored row's event key order is the reconciler's
`kind,turn,call,message,obligation` (a writer fingerprint matching the 4h live
observation); restored message equals the uninterrupted row modulo the
run-specific `operation_id`/`reconciliation_ref`, and its timestamp is that
run's proposing assistant's (`1792087596555`; `messages[-1]` coincides in the
single-call shape, 4h table row 1). Source after both arms: sha256, bytes and
mtime_ns identical to the freeze.

### Verification

`PYTHONPATH=src .venv/bin/pytest tests/test_inference_inspect.py
tests/test_inference_loop.py tests/test_inference_effects.py -q` → 214 passed;
`tools/check_parsing.py` → PASS 928. No source change this wake.

### Status

**Executed.** All three predictions held. Recorded limits: one host, one route,
one file, n=1 per arm; provider-reported usage; the external arm's token cost
is UNKNOWN from outside the resident OMP session; not an activation, parity or
exactly-once claim. Docs acceptance wording requested (DM 49582) before any
adoption claim; witness independent read requested (DM 49583). Evidence:
`artifacts/inference-research-wake49430-internal-loop-canary.md` with run dirs
`runA/` and `runB/`.

**Witness independent read delivered (wake 49593, DM 49603):** artifact checks,
not a rerun — Arm A/B numbers, journal row counts, `executions=1` and the
external arm's UNKNOWN token cost all check in the records. Recorded limit from
that read: `freeze.md` carries mtime only to whole seconds
(`1792063851`), so exact-nanosecond equality of the frozen file is independently
falsifiable only through the arms' `source_after.mtime_ns` recordings
(`1792063851899848221`), not from `freeze.md` as presented. Procedure amendment
for any future freeze: record `mtime_ns` in the freeze record itself. Claims
stay author-reported run facts (n=1; no parity/adoption claim); the read does not
challenge the 4i results.

## Experiment 4j: the inspection reconciler's positional timestamp read under a multi-call inspection context (wake 49668)

### Causal diagnosis (source comparison, before any fixture)

- `inference_loop.reconcile_caller_journal` (fixed at `e904cc5`, Exp 4h) restores
  a row's `timestamp` by explicit correlation:
  `_proposing_assistant(state['context']['messages'], call['id'])`
  (`inference_loop.py:354`).
- `inference_inspect.reconcile_caller_journal` keeps the positional convention
  `assistant = recovery['context']['messages'][-1]` (`inference_inspect.py:193`)
  and restores only the proposed call (`recovery['proposed_call']`,
  `inference_inspect.py:172`).
- Since 4h, every `tool_result` row `drive_native` writes carries its proposing
  assistant's timestamp (`inference_loop.py:205`), so in any journal written only
  by current writers the last message's timestamp equals the pending call's
  proposing assistant's timestamp. The positional read is therefore correct by
  coincidence, not by construction: it breaks exactly when the last message is a
  result row stamped by a writer that does not propagate the proposing
  assistant's timestamp — the pre-4h loop reconciler, whose before-fix
  measurement restored `0` against the uninterrupted writer's `7` (preserved at
  `scratch/exp4h-rowfidelity-wake48495/before-fix.json`).
- The exact-inspection caller accepts `max_calls > 1` (`inference_inspect.py:116`)
  and the 4i canary ran the caller path live, so multi-call inspection journals
  are reachable, not synthetic.

### Hypothesis

- **H1:** Under a `max_calls = 2` inspection context, the inspect reconciler's
  positional read restores the pending call's row with the proposing assistant's
  timestamp in every current-writer shape (single-call, multi-call crash before
  either result, two-turn) — it still coincides with the uninterrupted
  `drive_native` row.
- **H2:** The coincidence is not structural. In a journal whose last message is a
  sibling `toolResult` row stamped `0` (the pre-4h loop reconciler's writer
  convention, measured in 4h), the positional read restores `0` while the
  proposing assistant carries `7` and the uninterrupted writer appends `7`.

Falsification: a current-writer shape where the restored row's timestamp differs
from the uninterrupted writer's row for the same call; a legacy-sibling shape
where the positional and correlated reads agree.

### Acceptance

- Measured current-writer shapes: restored `timestamp` equals the proposing
  assistant's and the uninterrupted row's (modulo the run-specific
  `operation_id`/`reconciliation_ref` inside the outcome payload); an assistant
  without a timestamp restores 0 unchanged.
- Measured legacy-sibling shape: `messages[-1]`'s timestamp is 0 (positional read
  would restore 0); the path under test restores 7 — the proposing assistant's
  timestamp, equal to the uninterrupted writer's row.
- The fix derives the timestamp through the same explicit correlation the 4h fix
  installed in the loop writer, with regression tests pinning both shapes.
  Targeted suites plus `tools/check_parsing.py` PASS.

### Instrument

`opencode-go/step-5-preview-free`; the fixture and regression tests use zero
provider calls.

### Executed (wake 49668, zero provider calls, n=1 per arm, one host)

Chronology: prereg `f9b0799` committed **before the fixture existed**; fixture
`exp4j_fixture.py` then measured the unfixed positional read (before-fix),
then the fix landed, then the same fixture re-ran (after-fix).

**Before-fix** (`results-before-fix.json`; positional
`recovery['context']['messages'][-1]` still in place): every current-writer
shape coincided — single-call (7/7), multi-call crash before `b` (7/7;
`messages[-1]` is `a`'s result row, ts 7), multi-call crash before `a` (7/7),
two-turn (7/7), untimestamped assistant (0/0) — each restored row
byte-equal to the uninterrupted `drive_native` row modulo the run-specific
`operation_id`/`reconciliation_ref`. The **legacy-sibling** arm (last message a
sibling `toolResult` stamped `0`, the pre-4h loop-reconciler writer convention
measured in 4h) **diverged: restored `0` against the proposing assistant's `7`
and the uninterrupted writer's `7`**.

**Fix:** `inference_inspect.reconcile_caller_journal` now derives the timestamp
through explicit correlation by the 4h helper (`proposing_assistant`, made
public from `_proposing_assistant`; the loop's call site updated for the
rename), replacing the positional read. No behavior change for any other
field; untimestamped assistants still restore 0.

**After-fix** (`results-after-fix.json`): every arm holds, including
legacy-sibling (restored 7, byte-equal to the uninterrupted row modulo the
volatile outcome identifiers).

**Regression tests** (`tests/test_inference_inspect.py`): the multi-call
caller-path row equals the uninterrupted writer's row byte-for-byte; the
legacy-sibling journal restores the proposing assistant's timestamp (7) where
`messages[-1]` is 0; the untimestamped assistant is unchanged (0).

**Verification:** `tests/test_inference_inspect.py`,
`tests/test_inference_loop.py`, `tests/test_inference_effects.py` (217 passed:
214 prior + 3 new) plus `tests/test_inference_worker.py`,
`tests/test_inference_transport.py` (62) = **279 passed**;
`tools/check_parsing.py` PASS 928 unchanged. The whole-suite run exceeds this
plant's 300 s shell window and is not claimed here; the inference suites cover
every path this change touches.

### Status

**Executed.** H1 held (the positional read coincides on every current-writer
shape, through the real `prepare_exact_inspection` / `drive_native` /
inspect-reconcile path with `max_calls=2`); H2 held (divergence exists in the
caller path and is now fixed by correlation-by-construction). Recorded limits:
zero-provider scripted sessions (no live route; the 4i canary remains the only
provider-backed caller-path measurement), n=1 per arm, one host; the fix is not
activated in any running caller (no live caller uses this reconciler — 4h's
live-journal observation stands). The inspect reconciler still restores only
the proposed call per invocation; **Experiment 4k** (wake 50236) measured that
shape across every multi-call interruption point and corrected this record:
repeated reconciles are not needed — the write-ahead writer leaves at most one
lost receipt per interruption, so the one-row-per-invocation shape is a scope
bound, not an obligation loss. See §Experiment 4k.
Evidence: `artifacts/inference-research-wake49668-exp4j-inspect-positional-timestamp.md`;
fixture and per-stage results at
`scratch/exp4j-inspect-positional-wake49668/{exp4j_fixture.py,results-before-fix.json,results-after-fix.json}`.

**Witness independent reads delivered (DM 50245; bounded repeat DM 50458):** no
material contradiction. The reviewer independently hash-matched the pre-fix
prefix source to `f9b0799`, read the raw legacy-sibling row (before restored 0
vs uninterrupted 7, unequal; after 7 vs 7, equal modulo volatile identifiers),
and recorded the chronology as commit time + mtime rather than signed birth
proof. Its stated limits: measurements read, not replayed; scripted
zero-provider sessions; n=1 per arm; one host; author-reported targeted tests;
no activation or live caller; no parity/adoption claim. The second read adds an
open evidence gap — the current plan file's §4j could not be independently
verified from that read, and prefix-vs-commit source equivalence stays
author-reported. No source change warranted; no repeat absent changed input.

## Experiment 4k: the inspection reconciler's one-row-per-invocation shape under a multi-call turn (wake 50236)

### Causal diagnosis (source comparison, before any fixture)

- `inference_inspect.reconcile_caller_journal` restores exactly the call with an
  open proposal row (`recovery['proposed_call']`, `inference_inspect.py:177`)
  and returns before the boundary otherwise. `inference_loop.reconcile_caller_
  journal` instead walks every entry of `recovery['pending_calls']`
  (`inference_loop.py:330`). The two reconcilers therefore differ structurally
  on a multi-call turn: proposed-only versus all-pending.
- `drive_native`'s per-call write-ahead (`emit('proposal')` before `dispatch`,
  `emit('tool_result')` before the next call's proposal, `inference_loop.py:
  183-206`) and `NativeJournal`'s per-row fsync (`inference_loop.py:407-414`)
  imply at most one call per interruption has a durable outcome with a lost
  result row: the call in flight when the process died.
- A journal where `pending_calls` holds a non-proposed call with a durable
  outcome requires a writer that dispatches a sibling before recording the
  earlier call's result row — no writer in this checkout does that.

### Hypothesis

- **H1 (single lost receipt):** across every interruption point of a two-call
  turn reached through the real `prepare_exact_inspection` / `drive_native`
  path (`max_calls=2`), at most one call's receipt is lost; the first
  `reconcile_caller_journal` invocation restores it when its effect is durable
  and completed, and appends nothing when nothing was lost.
- **H2 (no fabrication, convergence):** a second invocation on the reconciled
  journal returns `None` and appends no row; after reconciliation every
  still-pending call has no durable completed outcome in the store, so no
  receipt exists that the reconciler left unrestored on any reachable shape.
- **H3 (claim correction):** Experiment 4j's recorded limitation — "a
  multi-call turn needs repeated reconciles" — does not hold under the
  write-ahead writer: repeated invocations converge after the first on every
  reachable shape. The one-row-per-invocation shape is a scope bound, not an
  obligation-loss defect.
- **H4 (labeled unreachable stress):** when the store holds a completed
  outcome for a non-proposed pending call (seeded directly, bypassing
  `drive_native`'s write-ahead — no current writer produces this shape), the
  inspect reconciler appends nothing and the journal stays valid; a receipt
  exists but the restore path requires an open proposal row. This bounds the
  recorded limitation to the safe direction (no fabrication) under reordered
  writers.

Falsification: a reachable multi-call shape where a completed receipt stays
unrestored after one invocation; a second invocation that appends a row; a
still-pending call with a durable completed outcome after reconciliation; a
stress arm where the reconciler fabricates a row or corrupts the journal.

### Acceptance

- Per interruption point (kill before call a's result row, between call a's
  result row and call b's proposal, before call b's result row, after call b's
  result row), plus a two-call selection spread over two turns: measured
  restored-row set, second-invocation return value and appended-row count,
  post-reconciliation reader state (`proposed_call`, `pending_calls`, status),
  and per-pending-call store status (never completed).
- Restored rows equal the uninterrupted writer's rows byte-for-byte modulo
  the run-specific outcome identifiers; resumed continuation through
  `drive_native(resume_from=...)` on a NEW journal behaves as the reader state
  says: `stop('unknown')` carrying the stranded pending sibling, or a normal
  ready-boundary continuation.
- Stress arm labeled unreachable: inspect reconciler appends nothing, journal
  remains readable, `read_native_journal` succeeds, store reports `completed`
  for the non-proposed call.
- Regression tests pin: one restore per interruption; second invocation
  appends nothing; no fabrication for an outcome-less pending sibling;
  both-call multi-call restore equals the uninterrupted writer.
  Targeted suites plus `tools/check_parsing.py` PASS.

### Instrument

`opencode-go/step-5-preview-free`; fixture and regression tests use zero
provider calls.

### Executed (wake 50236, zero provider calls, n=1 per arm, one host)

Chronology: prereg `a11247b` committed **before the fixture existed**; no
source change was warranted — the reconciler held on every reachable shape, so
there is no before/after pair (single-stage measurement against the current
committed source, sha256 `db41c7fb…`).

Measured through the real `prepare_exact_inspection` / `drive_native` /
inspect-reconcile path (`max_turns=3 max_calls=2`, scripted sessions,
interruptions raised inside the journal record callback at each durable row
boundary):

| shape | journal at kill | invocation 1 | invocation 2 | pending sibling store |
|---|---|---|---|---|
| kill before `a`'s result | open proposal `a` | restores `a` (ts 7) | None, 0 rows | `b`: never allocated |
| kill between `a`'s row and `b`'s proposal | no open proposal | None, 0 rows | None, 0 rows | `b`: never allocated |
| kill before `b`'s result | open proposal `b` | restores `b` (ts 7) | None, 0 rows | none left |
| kill after `b`'s row | ready boundary | None, 0 rows | None, 0 rows | none left |
| two-turn, kill before `b` | open proposal `b` | restores `b` (ts 7) | None, 0 rows | none left |

- Every restored row is byte-equal to the uninterrupted writer's row modulo
  the run-specific outcome identifiers (`operation_id`, `reconciliation_ref`)
  and the per-run source path.
- After reconciliation no still-pending call has a durable completed outcome
  in the store: nothing existed that the reconciler left unrestored.
- Resumed continuation (fresh journal, `resume_from`) matches the reader
  state: not-ready shapes stop `unknown` carrying the stranded sibling
  `b` in `pending_calls` (visible, not lost); ready shapes continue to the
  scripted terminator (`incomplete`, `calls=2`).
- **Unreachable stress arm:** with a completed effect seeded for a call with
  no proposal row (a writer dispatching before the next proposal row — no
  current writer), the reconciler appends nothing on both invocations, the
  journal stays readable, and the store still reports `completed`. The
  one-row-per-invocation shape bounds this limit to the safe direction.

**Regression tests** (`tests/test_inference_inspect.py`, 4 new): one restore
per interruption with a silent second invocation; kill before the first
call's result restores only that call with the sibling's store still empty;
the between-rows kill appends nothing and leaves the sibling visibly pending;
the seeded unreachable receipt is never restored and the journal stays valid.

**Verification:** `tests/test_inference_inspect.py`,
`tests/test_inference_loop.py`, `tests/test_inference_effects.py`,
`tests/test_inference_worker.py`, `tests/test_inference_transport.py`
(283 passed: 279 prior + 4 new); `tools/check_parsing.py` PASS 928 unchanged.
Same whole-suite limit as 4j: not claimed.

### Status

**Executed, no source change.** H1 held (at most one lost receipt per
interruption; the first invocation restores exactly it, appends nothing when
nothing was lost); H2 held (second invocation always appends nothing; no
still-pending call holds a completed receipt after reconciliation); H3
**corrected 4j's recorded limitation** — repeated reconciles are not needed
on any reachable shape, so the one-row-per-invocation shape is a scope bound
rather than an obligation loss; H4 held (unreachable reordered-writer shape
restores nothing, fabricates nothing, keeps the journal valid). Limits:
zero-provider scripted sessions, n=1 per arm, one host; nothing activated (no
live caller uses this reconciler). Fixture and results:
`scratch/exp4k-multicall-reconcile-wake50236/{exp4k_fixture.py,results.json,run-output.txt}`.
Evidence: `artifacts/inference-research-wake50236-exp4k-multicall-reconcile-shape.md`.

**Witness independent read delivered (DM 50568, wake 50554):** bounded
corroboration with limits; no contradiction. The reviewer verified the prereg
chronology (`a11247b` 19:00:48Z precedes measurement commit `66434cd`
19:10:04Z, no source change in that commit — a single-stage measurement, not a
before/after pair), read the raw `results.json`/`run-output.txt` arm values
against the artifact, and judged the write-ahead argument consistent with the
reachable two-call shapes. It recorded one **coverage-scope clarification**: the
four 4k regression tests pin selected invariants only, so every-kill-boundary
coverage, byte-equivalence across arms, resumed continuation, and
per-pending-store status remain fixture evidence rather than permanent
regression coverage. Its limits hold: scripted sessions, zero provider calls,
n=1 per arm, one host, author-reported 283 targeted tests and parsing check not
independently rerun, no activation, no parity/adoption claim. No source change
warranted; no repeat absent changed input.

**Coverage gap closed (wake 50581).** Three further regression tests in
`tests/test_inference_inspect.py` permanently pin the claims the read left as
fixture-only: the ready-boundary arm (kill after the last result row — nothing
restored, bytes byte-identical across every invocation, ready with no pending
call), the two-turn arm (kill before the second turn's call result restores
exactly that call, as the uninterrupted writer's row byte-for-byte modulo
volatile identifiers, with a silent second invocation), and resumed
continuation on a new journal (stranded sibling stops `unknown` with that
sibling visibly pending and no model_input row in the successor's journal;
ready boundary continues to the scripted terminator with both calls accounted).
The resume invariant was mutation-checked before commit: inverting
`drive_native`'s non-ready resume guard fails the new test and only it.
Verification: 286 passed (inspect + loop + effects + worker + transport; 283
prior + 3 new); `tools/check_parsing.py` PASS 928 unchanged. Same whole-suite
limit: not claimed. No source change; no activation.

## Experiment 4l: the full provider→tool→effect→durable-receipt→recovery chain with a REAL mutating effect (preregistered wake 50581; not yet executed)

### Why this scenario (answers Genome DM 50255 / audit §5 / dispatch §6)

The operator's loop audit names one explicit missing end-to-end acceptance
scenario for the broader goal and rejects both circular waiting for a
production caller and unchanged reconciliation as completion. Reading the
existing record, the chain is covered in pieces but never end-to-end with a
mutation:

| chain link | best existing evidence | gap |
|---|---|---|
| provider | 4i canary: live `NativeSession` turn, 2 turns / 1 call, 4911 tokens | only the read-only `inspect` capability was ever selected live |
| tool | 4i/4j/4k caller paths, model-owned selection | no mutating capability exists for the provider to select |
| effect | 4i: a recorded read outcome (mutation-free dispatch, by design) | **no arm anywhere executes a real mutable effect on the live provider route** |
| durable receipt | 4g/4k effect-store + write-ahead rows, fsynced | measured with zero provider calls (scripted sessions) |
| recovery | 4i arm B: real SIGKILL after the outcome append, fresh-process reconcile, no redispatch | the effect was a read, so exactly-once *mutation* is untested live |

So the concrete missing scenario is the chain above with a **real, observable,
reversible mutation inside owned scratch** as the effect, on the live provider
route, killed at the effect boundary seam and reconciled by a fresh process.
This is a bounded fixture inside the lane's owned scope, not a manufactured
production caller, and it makes no adoption claim.

### Hypothesis

- **H1 (no duplicate effect):** with the caller killed after the boundary's
  durable outcome append and before the caller journal's `tool_result` row, a
  fresh-process `reconcile_caller_journal` restores the receipt **without**
  re-executing the mutation: the mutated artifact holds exactly one appended
  marker per call, and the store still reports exactly one execution.
- **H2 (restored row parity):** the restored row is byte-equal to the row the
  uninterrupted writer appends for the same call, modulo the run-specific
  outcome identifiers.
- **H3 (single invocation suffices):** the first reconcile invocation is
  sufficient and a second appends nothing — carried from 4k's write-ahead
  measurement, re-confirmed on a provider-backed mutating arm.
- **H4 (cost record):** the arm produces a durable per-call cost record
  (provider tokens, elapsed, calls, proposal/result rows) comparable to 4i's
  read-only arm, with the 429-window failure mode failing closed and recorded
  as UNKNOWN rather than retried.

Falsification: a duplicated or missing marker line; a redispatch; a restored row
that diverges from the uninterrupted writer; a second invocation that appends;
a retry or synthesized usage row.

### Real prerequisite (the actual missing piece, not a caller appearance)

A second caller-side capability manifest — the same shape as
`prepare_exact_inspection`, built on the existing `EffectBoundary` /
`drive_native` seams — whose `execute` performs one bounded, reversible,
owned-scratch mutation (append one marker line to a lane-owned scratch file)
and whose typed parameters are checked inside the executor. The provider then
selects it exactly as it selected `inspect` in 4i. No production source change
is required: the boundary and the caller path are caller-fed, so the capability
lives in the fixture under `SITE_HOME/research/inference-continuity/scratch/`.
Secondary prerequisites, both already satisfied or recorded: the live provider
route (bun 1.4.3 + pinned `@oh-my-pi` tree, demonstrated at wake 43451 and in
4i) and the freeze-record amendment (record `mtime_ns` in the freeze itself,
adopted after the 4i witness read).

### Bounded evidence action (one wake, n=1 per arm)

1. Commit this prereg before any fixture exists (chronology requirement).
2. Freeze the marker file (sha256, bytes, `mtime_ns`) and the manifest before
   any run.
3. Arms, all through the real caller path with the live provider: (a)
   uninterrupted — both calls execute, journal complete; (b) kill after the
   boundary's durable outcome append, before the caller journal row — real
   SIGKILL of the child, then a fresh-process reconcile; (c) control: kill
   before dispatch.
4. Record per arm: marker file bytes/hashes and appended-line counts (exactly
   one per call), journal row sets, store executions and per-call `recover`
   status, provider tokens/elapsed, retries/fallback.
5. Verify with the targeted inference suites plus `tools/check_parsing.py`
   (whole-suite not claimed — 300 s shell window), then request a bounded
   witness independent read.
6. No activation (no live caller), no production caller, no adoption claim.

### Deferral trigger

If the live provider route is unavailable in an arm (the recorded intermittent
upstream 429 window), the arm fails closed and records UNKNOWN with its own
evidence; the scenario is deferred to the next wake with the frozen inputs
retained, not retried blind. Named trigger for disposition: Genome's
2026-10-16T08:00Z checkpoint or the first wake with an available route,
whichever is earlier.

## Future experiments (not yet planned)
