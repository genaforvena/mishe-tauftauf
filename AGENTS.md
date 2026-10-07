@/home/mesh-home/.codex/RTK.md

# Working in mesh

This owned repository is running a wall-coordination trial. These instructions
supersede the old task-ledger and isolated-branch workflow here. Other sites are
outside this trial; preserve their installed instructions and services.

Start from the chat trigger and read your full current top pane through
`mishe-tauftauf --home SITE_HOME pain read ROLE --launcher dashboard`, where
`SITE_HOME` is the site home directory (`.mishe-tauftauf/` in this plant). The
dashboard is the evidence input; tmux and its lease establish presentation
liveness.
Missing, failed or stale observations remain UNKNOWN or RED.

Minds choose and organize useful work. Planning, investigation, docs editing and
handoff are valid turns. Keep your plate, findings and next action in your edited
wall under `SITE_HOME/walls/ROLE.md`. Read other walls or older chat when
needed. Ask peers through addressed messages on the shared chat tape. DMs are
visible, not private. System 1 advice can help choose an approach; it is not a
planning gate.
For each active blocker name the resolver, missing evidence, one bounded action
to produce it, and an escalation or disposition time. Address the resolver; at
the cutoff resolve, escalate, or explicitly defer to a named trigger. Do useful
independent work while waiting. The operator is the human owner, not a pane or a
role; never park work waiting on an operator window or an operator decision. The
`operator` shell exists only for that person's convenience. Record evidenced
outcomes with `wall outcome`; unchanged status reconciliation is not itself an
outcome.

Use one shared Git checkout. Commit your own scoped work on the single `main`
branch; there are no other branches, locally or remotely. Coordinate overlapping
edits and preserve others' work. Genome pushes `main` and checks CI and live
consumers; it neither authors nor gates another mind's commit, so do not park
reviewed work uncommitted waiting on it. Applying a patch to running code still
requires deterministic checks, an independent reading, observable activation
with visible failures, and a tested revert path. Running code comes from a
snapshot so unfinished source edits do not silently become live. See
[the patch commands](docs/wall-coordination.md).
`applied` alone is incomplete delivery. Use the reviewed `verify` recovery
exercise before claiming verified delivery; preserve older incomplete records.

No fix without causal investigation, and no resolution without a continuing way
to observe its failure. Preserve the original failure evidence, state the causal
diagnosis and its limits, and name how recurrence will be detected and routed to
a resolver within a bounded interval. Exercise that detection with a controlled
failure; also check that missing, stale or broken monitoring stays UNKNOWN or
RED. Removing a warning, retrying, or obtaining one successful sample does not
resolve the cause. A bounded recovery may restore work while the diagnosis stays
open. Replace a retired detector with tested equivalent coverage before removing
it; never make a patch appear successful by narrowing observation or erasing
unresolved evidence.

Docs are a first-class, edited book. Keep [the introduction](docs/mesh.md) current,
concise and understandable without internal machinery or chat history. Delete
stale passages rather than accumulating them.

Append chat through the canonical CLI. Keep local notes, chat, experiments and
runtime snapshots in gitignored `.mishe-tauftauf/`; never commit them. Settle only
the actual wake using `seed yield` with your notes; `--continue` requests useful
follow-up work. A settlement is a transport fact, not proof a patch succeeded.
Respect the owned scope. Do not touch other nodes, accounts or devices.
