# Mesh culture carried by this seed

## Sources and scope

Sources inspected 2026-09-29: `lte-workstation/AGENTS.md` (mesh contract and invariant registry `20260921.1`), `docs/autonomy/rules.md`, `docs/self-organization.md`, `scripts/mesh-omp-lifecycle`, and the mishe planting skill. This file records which rules this single-repository plant can actually enact. The sources remain authoritative for LTE; this copy grants no authority over LTE or other nodes.

## Rule mapping

| Mesh rule | Local behavior |
| --- | --- |
| `mesh:1`, `mesh:12` | Each wake reads this repo's `AGENTS.md`, its charter and handoff, the full live top pane, and relevant `chat.log` entries. A lease is liveness, never check correctness. |
| `mesh:2`, `mesh:5`–`mesh:8` | Repair local missing dependencies and recoverable failures within owned scope. A real blocker records the live check, owner, artifact, class, and retry event. Node-wide GPU and fleet resource authority does not travel. |
| `mesh:4`, `mesh:13` | Durable rules go in `AGENTS.md`, doctrine, or a charter and need a wiring check. Case history stays in artifacts; retired paths do not stay in active prompts as transition prose. |
| `mesh:9` | Mutations name paths and ownership, preserve dirty work, carry before/after evidence and a rollback or retry edge. The local artifact root is the plant site's `artifacts/`, not LTE's evidence root. |
| `mesh:10`, `mesh:14` | Compare plausible approaches; put counts, identity, deadlines, task state, and recurring probes in deterministic code. Spend a mind turn on choices. |
| `mesh:11`, `mesh.decides-informs.v1` | Act on authorized local work and report start, outcome, and cost in the shared tape. External or protected actions need their own authority. |
| `mesh:15`, `mesh:16` | Reproduce RED or UNKNOWN, predict the result, repair the cause, rerun the same check, and see its verdict on the live top pane. Change an architectural boundary when evidence requires it. |
| `mesh:17` | Explore qualitative improvements within this owned plant, with a checked artifact and rollback edge. Privacy, credentials, and external systems remain outside the charter. |
| `mind.top-pane-live.v1` | The supervisor checks a fresh lease and a live pane before waking a mind; the witness top reports window presence and open work. A passing renderer self-test does not prove the caller or visible viewport. |

## Self-organization and ownership

The self-organization loop is:

**maintain the substrate → advance one bounded development step → ideate from observed gaps → process wishes into owned tasks**

The shared tape uses `[task] <id> owner=<role>`, `[taking] <id>`, `[done] <id>`, and `[dropped] <id> — reason`. Witness keeps unfinished IDs visible and checks artifacts. Genome owns reviewed source landing.

Every mind may implement an owned, evidence-backed repair across Mishe's layers, including its rules and architecture. A role is not a file restriction. The operator's standing instruction to encourage these changes is carried in doctrine and both agent contracts. Ownership, acceptance checks, independent review, and external boundaries still apply.

A long task keeps one ID across handoffs and uses `seed yield --continue` until its next checked step is complete.

## Task prerequisites and helper offers

Task prerequisites have a deterministic boundary:

- `task step` records progress, evidence, and the next step.
- `task wait` names an exact event or deadline.
- `task event` publishes the changed condition.

The supervisor records a consumed attempt tied to the wake and observation. It refuses an unchanged waiting task on quiet picks or continuation. A new pane observation still invites investigation; it does not make a waiting task actionable.

With `task offer`, an owner names suitable helper charters. The shared seed lock and durable pending wake reserve an offered ready step before delivery, preventing another helper from taking it.

These are local scheduling and cooperative ownership checks. They grant no authority over arbitrary editor writes or external fleet resources.

## Protected landing capacity

`task landing` registers an explicit genome delivery with a source-bound evidence digest. Its first registration sequence, rather than its latest progress sequence, determines landing order. The selector chooses the oldest eligible registered genome delivery before ordinary work, preserving active-wake reservations and one-shot prerequisites. Registration does not certify review or landing, change task readiness, or preempt a running mind. `task landing-status` exposes debt; `task production-check` returns nonzero while any registered genome delivery remains open, including a blocked delivery. Producers then help delivery or pursue local observations rather than adding optional source candidates. Essential incident repairs require an explicit justification.

The guaranteed boundary is selection of the next available idle turn, conditional on actionable task state. Eventual delivery also requires finite verified steps, a responsive mind, review and test capacity, and an available remote. Production admission remains cooperative because this seed has no authority over arbitrary editor writes. Queue priority cannot make an unowned diff safe to commit, turn failed CI green, or justify unrelated changes. Evidence and charter wiring must be verified at the live caller after rollout.

Priority follows open preparation children and concrete producer prerequisites, including independent review in another owner's queue. Registered delivery work cannot create an event-only wait: it needs a scheduled producer task or an external retry deadline. This puts locally missing deliverables back into production while preserving one-shot attempts, dependency-cycle checks and active reservations. Existing historical waits remain readable and require explicit reconciliation.

The source-delivery and deployment gates run in causal order: isolated candidate/manifest, independent byte-bound review, verification, commit/push, exact-SHA CI, clean release deployment, then consumer convergence. The deployment report uses the installed release pin when present and fails closed on a corrupt pin. The linked follower checks detached release integrity before and after refresh, rather than requiring the independent development checkout to be clean. Target ownership and live caller checks remain mandatory; deployment convergence cannot be its own prerequisite.

## Tracked rules and local state

The tracked genome contains reusable code and general rules. The live plant's chat, customized charters, handoffs, plans, checks, evidence, and service files belong under gitignored `.mishe-tauftauf/`, following LTE's `.mesh/` boundary.

A reviewed general lesson is promoted deliberately into tracked source. Genome checks the Git index for local state before committing and pushing.

## The lifecycle this seed implements

OMP's full mesh lifecycle uses native `session_start` and `session_stop` receipts, then an idle drain and context clear.

This seed implements a smaller local boundary:

1. An exact `seed yield` archives the handoff and writes a `[work]` receipt.
2. The supervisor clears only after a settled idle turn.
3. The charter and handoff arrive with the next real wake. A durable continuation or fresh observation invites the next step; clear creates no idle model turn.

The local receipt is a deliberate subset. Native mesh TURN accounting, fleet board gates, and GPU rules are not claimed here.

## Persistent seed, not the mortal demo

The older mishe planting skill is a mortal, no-clone culture demo with a complete `burn` path. This repository is the persistent development seed.

Its planting skill installs user services only when used for that full local setup. It does not claim the demo's zero-footprint teardown contract.

## Managed goals and productive waits

The task lifecycle now shares one projection for display and scheduling. Managed tasks use evidence-backed `task add`, `task finish`, and `task reopen`; attaching a child protects its legacy parent from prose completion. A child finish keeps the overall goal open. Owner/evidence checks and completion-cycle checks are cooperative local boundaries, not protection against arbitrary direct file writes. Structured control tags are reserved to the CLI and validated before append.

Work admission and result acceptance are separate. A checked waiting goal may name an independently admissible child with `--alternative`; missing locally owned deliverables become production steps. `task wait --producer ROLE --retry-task ID` wakes review after completed producer evidence, including a producer already completed when the wait is recorded. A stable checked waiting backlog receives one independent-work decision, then stays quiet until its inputs change. Progress/outcome comparison rejects repeated steps that merely replace an artifact hash. These mechanisms do not grant a new training budget or external authority.
