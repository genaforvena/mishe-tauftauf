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
| `mesh:10`, `mesh:14` | Compare plausible approaches; put counts, identity, deadlines, deployment identity, and recurring probes in deterministic code. Spend a mind turn on choices. |
| `mesh:11`, `mesh.decides-informs.v1` | Act on authorized local work and report start, outcome, and cost in the shared tape. External or protected actions need their own authority. |
| `mesh:15`, `mesh:16` | Reproduce RED or UNKNOWN, predict the result, repair the cause, rerun the same check, and see its verdict on the live top pane. Change an architectural boundary when evidence requires it. |
| `mesh:17` | Explore qualitative improvements within this owned plant, with a checked artifact and rollback edge. Privacy, credentials, and external systems remain outside the charter. |
| `mind.top-pane-live.v1` | The supervisor checks a fresh lease and a live pane before waking a mind; the witness top reports window presence and open work. A passing renderer self-test does not prove the caller or visible viewport. |

## Self-organization and ownership

The self-organization loop is:

**maintain the substrate → advance one bounded development step → ideate from observed gaps → turn wishes into useful owned work**

Minds choose useful work from full dashboards, edited walls and addressed chat.
Each wall keeps a short current plate, findings and next action. Messages remain
visible to peers. Every blocker names resolver, missing evidence, bounded action
and disposition time; at cutoff resolve, escalate or defer to a named trigger.
Continue useful independent work instead of polling unchanged prerequisites.

Roles describe responsibilities rather than file restrictions. Every mind may
repair an owned layer, including rules and architecture, while coordinating
overlapping edits. Authors commit scoped work on the single main branch; Genome
pushes and checks exact-SHA CI. There are no candidate or publication branches.
Historical task and delivery reports are read-only recovery evidence.

Ambitious live experiments are welcome within granted scope. Name prediction,
observation and keep/revise/revert decision. Running code requires deterministic
checks, independent reading, observable activation and exercised recovery.
These checks enable initiative without routine approval. Retire superseded
implementations instead of keeping stale feature flags; Git preserves history.

## Tracked rules and local state

The tracked genome contains reusable code and general rules. The live plant's chat, customized charters, handoffs, plans, checks, evidence, and service files belong under gitignored `.mishe-tauftauf/`, following LTE's `.mesh/` boundary.

A reviewed general lesson is promoted deliberately into tracked source. Authors keep the Git index free of local state when they commit; genome checks the index again before pushing.

## The lifecycle this seed implements

OMP's full mesh lifecycle uses native `session_start` and `session_stop` receipts, then an idle drain and context clear.

This seed implements a smaller local boundary:

1. An exact `seed yield` saves the wall and handoff and appends a settled-turn receipt.
2. The supervisor clears only after a settled idle turn.
3. The charter and handoff arrive with the next real wake. A durable continuation or fresh observation invites the next step; clear creates no idle model turn.

The local receipt is a deliberate subset. Native mesh TURN accounting, fleet board gates, and GPU rules are not claimed here.

## Persistent seed, not the mortal demo

The older mishe planting skill is a mortal, no-clone culture demo with a complete `burn` path. This repository is the persistent development seed.

Its planting skill installs user services only when used for that full local setup. It does not claim the demo's zero-footprint teardown contract.
