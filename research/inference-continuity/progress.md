# Inference Continuity — Progress

## Current status (2026-10-11)

- Programme files reconstructed from charter context. Original `brief.md`, `experiment-plan.md`, `progress.md`, and `research/shared/continuity-and-body.md` absent from checkout. Committed as `5e927fb` on 2026-10-11.

## Stage 0 — Wake causes, context sources, persistent state

### Wake causes

- **Chat trigger** — addressed DM on shared chat tape (canonical CLI `wall dm`)
- **Periodic/addressed wakes** — supervisor or peer sends a message
- **Seed yield** — settlement lock serializes turns; `--continue` requests follow-up work

### Context sources

- **Dashboard** — `mishe-tauftauf --home SITE_HOME pain read ROLE --launcher dashboard`
- **Wall** — edited notes under `SITE_HOME/walls/ROLE.md`
- **Chat tape** — `chat.log` and in-dashboard previews
- **Sensors** — live evidence-binding, drift, CPU, renderer coverage
- **Git** — single shared checkout, `main` branch only

### Persistent state

- **Wall** — overwritable plate; durable evidence belongs in artifacts
- **Artifacts** — `artifacts/` directory; per-outcome evidence files
- **Git** — commits on `main`; genome pushes to origin
- **Permits** — `CLI permit recover/resolve` records for blockers

### Effects

- **Wall edits** — `CLI wall write --owner ROLE --file NOTES`
- **DMs** — `CLI wall dm --source ROLE --to PEER --file MESSAGE`
- **Outcomes** — `CLI wall outcome --owner ROLE --kind KIND --file NOTES --evidence FILE`
- **Settlement** — `CLI seed yield --slug ROLE --wake N --file NOTES --result changed|verified|blocked`

### Stopping causes

- **Settlement** — `seed yield` with result
- **Blocker** — named resolver, missing evidence, bounded action, cutoff
- **Handoff** — `--continue` for useful follow-up work

### Successor requirements

- Read dashboard, wall, addressed messages
- Reconcile prior effects before acting
- Choose useful work (planning, investigation, implementation)
- Record meaningful outcomes with evidence
- Settle only the actual wake

## Stage 1 — Candidate experiment

CI result for an already published commit. Baseline and transient paths use identical recorded inputs with only one able to act.

**Hypothesis:** shared world/evidence/obligations can retain or improve autonomy while inference sessions become transient.

**Test:** measure whether a fresh session can reconstruct enough context from wall + artifacts + chat to continue useful work without the prior session's in-memory state.

**Status:** not yet executed.

## Acceptance criteria

- Measured evidence of context reconstruction quality
- Comparison of baseline vs transient path outcomes
- No fabrication of results; negative results welcome

## Next action

Execute Stage 1 candidate experiment or a smaller bounded step toward it.

## Stage 1 — First bounded step (2026-10-11)

**Experiment:** CI result for already-published commit (80cef9d), baseline vs transient reconstruction.

- **Baseline:** full-context session records CI result to artifact
- **Transient:** fresh scout with only persistent state reconstructs same result
- **Result:** zero discrepancy; persistent state alone sufficient
- **Evidence:** `artifacts/stage1-baseline-80cef9d-ci-20261011.md`, `artifacts/stage1-result-baseline-vs-transient-20261011.md`

**Limitation:** reconstruction of already-recorded result; does not test acting on new information without prior context. Interruption tests remain for Stage 1 continuation.

## Next action

Stage 1 continuation: interruption tests (before effect, after effect before receipt, during external waits).
