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

## Future experiments (not yet planned)

- Named identity test
- Perspective test
- Role/identity removal (separate test per brief)
