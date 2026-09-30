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

The shared tape uses `[task] <id> owner=<role>`, `[taking] <id>`, `[done] <id>`, and `[dropped] <id> — reason`. Witness keeps unfinished IDs visible and checks artifacts. Source authors own delivery; genome serializes ready integration.

Every mind may implement an owned, evidence-backed repair across Mishe's layers, including its rules and architecture. A role is not a file restriction. The operator's standing instruction to encourage these changes is carried in doctrine and both agent contracts. Ownership, acceptance checks, independent review, and external boundaries still apply.

Minds choose eligible work from the full shared board before selecting a new improvement. Idle is not acceptable when an authorized useful step is available. Issue spotting and evidence-backed repair are standing duties, including on green panes. A checked wait preserves its exact retry while the mind advances independent work; a stable waiting backlog allows one independent-work decision, not repeated polls. A long task keeps one ID and uses `seed yield --continue` only with an actionable next step. Yield and clear end a wake, not its open task.

## Task prerequisites and helper offers

Task prerequisites have a deterministic boundary:

- `task step` records progress, evidence, and the next step.
- `task wait` names an exact event or deadline.
- `task event` publishes the changed condition.

The supervisor delivers an advisory board without choosing a task. The mind records one atomic claim tied to its exact wake and observation. It refuses an unchanged waiting task on quiet picks or continuation. A new pane observation still invites investigation; it does not make a waiting task actionable.

With `task offer`, an owner names suitable helper charters as advice. An offer is not required. `task claim` under the shared lock reserves an eligible step for one active wake and transfers ownership, preventing another mind from taking it. Author delivery and fact-owned integration retain their required owners.

These are local scheduling and cooperative ownership checks. They grant no authority over arbitrary editor writes or external fleet resources.

## Ready integration and delivery

Authors prepare isolated candidates, obtain independent exact-revision review, publish branches, and submit delivery. The CI watcher derives readiness from actual refs, immutable review, and exact branch CI. Passing candidates create revision-bound genome integration steps for the next available turn; active wakes remain reserved. Preparation children do not inherit integration priority. Historical landing records and source debt impose no global production hold.

The author retains final main CI, clean release deployment, and consumer verification. Deployment reads the installed release pin and fails closed on corruption; independent development dirt is not deployed code. Target ownership and live caller checks remain mandatory. Deployment convergence cannot be its own prerequisite.

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

## Explainable publication and temporal detection

Every new chat entry explains the event, evidence or artifact, and next owner or action. No inline JSON is allowed, including fenced payloads, task transitions and supervisor receipts. Structured state belongs in immutable referenced audit records; historical entries remain unchanged. R01–R08 check every post; P01–P28 check selections and handoffs. A configured suspicious, UNKNOWN, missing or unavailable check refuses publication privately and saves the draft and correction report. Correct and recheck without repeating prior effects. Unconfigured semantics remain explicitly untested; model detection requires measured validation.

Witness checks task histories across all roles, including itself and seed receipts, completed producers, repeated attempts, conditional readiness and task/action drift. Hash changes alone do not prove progress. Inspect `task coordination-report` and verify findings against sequence and artifact evidence. Pane refresh performs no model inference.
