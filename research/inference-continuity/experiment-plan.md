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

The event class is the standing duty; the designed matched-pair
measurement is still open. Since wake 37876, essentially every
inference-research wake is a transient session that reconstructs context
from persistent state only (wall, artifacts, chat.log, git) and
reconciles a CI result for an already-published commit — most recently
tip 3103ef2 (run 38549401954 success, reconciled at wake 40095) and tip
04b681e (run 38550393081 success, tape event 40169, reconciled at wake
40158). The nine registered samples in progress.md belong to the
separate model-loop parity chain, not to this experiment.

What is not done: the baseline-vs-transient comparison on identical
recorded inputs with only one path able to act, and the comparison
columns (context assembly cost, rediscovery, retries, fallback, human
intervention, completeness). The three interruption classes have been
observed only under natural causes (after-effect-before-receipt at
wake 35220's settled-crash re-verification; during-external-wait at
every CI `in_progress → terminal` settlement gap), never under
controlled injection — so no interruption claim is made. A lease or
dashboard proves liveness only, not the comparison.

Next designed step: the next terminal CI event for an
already-published commit is the matched-pair trigger — freeze identical
recorded inputs, run both paths, keep the harness route as fallback,
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

## Future experiments (not yet planned)
