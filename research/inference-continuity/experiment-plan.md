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

Not yet executed.

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

Planned, not yet executed.

## Future experiments (not yet planned)
