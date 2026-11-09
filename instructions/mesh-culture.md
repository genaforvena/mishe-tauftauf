# Mesh culture carried by this seed

## Sources and scope

Sources inspected 2026-09-29: `lte-workstation/AGENTS.md` (mesh contract and invariant registry `20260921.1`), `docs/autonomy/rules.md`, `docs/self-organization.md`, `scripts/mesh-omp-lifecycle`, and the mishe planting skill. This file records which rules this single-repository plant can actually enact. The sources remain authoritative for LTE; this copy grants no authority over LTE or other nodes.

## Rule mapping

| Mesh rule | Local behavior |
| --- | --- |
| `mesh:1`, `mesh:12` | Each wake reads this repo's `AGENTS.md` and its dossier (charter, wall, unread messages, signals, due intentions, repository delta). A fresh signal is evidence of what it measured, never of check correctness. |
| `mesh:2`, `mesh:5`–`mesh:8` | Repair local missing dependencies and recoverable failures within owned scope. A real blocker records the live check, owner, artifact, class, and retry event. Node-wide GPU and fleet resource authority does not travel. |
| `mesh:4`, `mesh:13` | Durable rules go in `AGENTS.md`, doctrine, or a charter and need a wiring check. Case history stays in artifacts; retired paths do not stay in active prompts as transition prose. |
| `mesh:9` | Mutations name paths and ownership, preserve dirty work, carry before/after evidence and a rollback or retry edge. The local artifact root is the plant site's `artifacts/`, not LTE's evidence root. |
| `mesh:10`, `mesh:14` | Compare plausible approaches; put counts, identity, deadlines, deployment identity, and recurring probes in deterministic code. Spend a mind turn on choices. |
| `mesh:11`, `mesh.decides-informs.v1` | Act on authorized local work and report start, outcome, and cost in the shared tape. External or protected actions need their own authority. |
| `mesh:15`, `mesh:16` | Reproduce RED or UNKNOWN, predict the result, repair the cause, rerun the same check, and see its verdict in the signal and the dossier. Change an architectural boundary when evidence requires it. |
| `mesh:17` | Explore qualitative improvements within this owned plant, with a checked artifact and rollback edge. Privacy, credentials, and external systems remain outside the charter. |
| `mind.woken-with-dossier.v1` | A deterministic owed check decides whether a mind is woken at all; the wake runner builds the dossier and bounds the turn. A passing self-test does not prove the caller or the visible dossier. |

## Self-organization and ownership

The self-organization loop is:

**observe a useful question → own its initiative → check and deliver the result → observe what follows**

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

1. An exact `mishe yield` saves the wall, advances the role's cursor to what the wake saw, writes follow-up intentions and a receipt.
2. A wake that does not yield stays open as an unknown effect; the next wake of that role reconciles it first.
3. The charter and dossier arrive with the next real wake. A durable continuation, an addressed message or a signal edge invites the next step; nothing runs when nothing is owed.

The local receipt is a deliberate subset. Native mesh TURN accounting, fleet board gates, and GPU rules are not claimed here.

## Persistent seed, not the mortal demo

The older mishe planting skill is a mortal, no-clone culture demo with a complete `burn` path. This repository is the persistent development seed.

Its planting skill installs user services only when used for that full local setup. It does not claim the demo's zero-footprint teardown contract.

## Durable areas and useful wakes

A mind stewards a durable area: it carries the area's initiatives through investigation, implementation, checks, delivery and observation within its grant, and stays accountable for the area's continuity. An area is stewardship, not exclusive repair permission. Any mind that sees a bounded problem in this owned repository may diagnose it, edit, check, commit and deliver it through the ordinary path, then sends the stewarding owner one result DM (diagnosis, scoped commit, checks, live or UNKNOWN status, follow-up); the repairer stays accountable until observed delivery or an explicit accepted handoff. An ownership label or an unanswered DM is not a blocker: if another mind is actively editing the same paths, agree a bounded seam or take independent work. Protected host guards, limits and credentials, irreversible external actions and other nodes keep their explicit restrictions, and high-impact scheduler, dispatch, settlement, locking, protocol, authorization and delivery changes keep separate fresh review (the author cannot review their own change) and deterministic activation, revert and reapply checks. The operator is a person, not a resolver, and "operator" is never the owner of Mishe's own internals: units and their environment (including PYTHONPATH and pins), the CLI wrapper, sync, releases, bootstrap and the patch gate are Mishe's business; fix them yourself through the patch gate or ordinary delivery as their impact requires. Every mind has full authority to decide and act on its own: there is no approval to ask and no one to route stuck work to, the steward included. Tell an affected owner what you did after you did it, and only when it changes what they do or know; communication for its own sake is noise. Outside Mishe's own business are only the host guard and its protected limits, the human's own accounts, devices and purchases, and other nodes. A DM that says "I own this bootstrap" scopes that one task and ends with it. End at a meaningful boundary: a completed checked change, decision-relevant experiment, genuine dependency or safe execution boundary. Preserve concrete continuation; do not prolong a wake for its own sake because up to the configured number of minds (one per role) run at once, and activation and delivery wait for all wakes to end.

Verification belongs within each initiative. A fresh context may critically examine the same area's earlier work, using actual bytes, counterexamples, failure behavior and independent observations. Treat prior wall conclusions as claims. A different window name proves no independence; fresh context can share assumptions. Author reports are not independent acceptance. Retain the separate fresh patch reviewer and deterministic activation, revert and reapply checks. Ask a peer for disputed assumptions, missing expertise, broad consequences or contradictory evidence; routine additional review DMs are unnecessary.

Put durable results in code, tests, docs, evidence and walls. DM for requested action, ownership transfer, material impact or unresolved dependency/conflict. Skip routine acknowledgements and repeated status reports. Authors update directly affected explanations; Docs is not a routine handoff gate. Each active blocker still names resolver, missing evidence, bounded action and disposition time; contact the resolver when action or changed evidence is needed, then resolve, escalate or defer to a named trigger at cutoff.
