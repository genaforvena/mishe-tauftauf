# Inference Continuity — Experiment Plan

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

Still not done: internal-loop arms (bun absent); the three interruption
classes, still natural-cause observations only — no interruption claim
is made. A lease or dashboard proves liveness only, not the comparison.
Not adoption.

Next designed step: a fourth pair only if a materially new variable
appears (a different work class, a non-docs commit, or an internal-loop
route becoming available); otherwise stop growing n and record. Keep
the harness route as fallback and record UNKNOWN where the measurement
does not reach. No blind retry.

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

## Future experiments (not yet planned)
