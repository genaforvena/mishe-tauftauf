# Mesh

[README](../README.md) · [Getting started](getting-started.md) · [How it works](how-it-works.md) · [Operating the plant](operating.md) · [Wall coordination](wall-coordination.md)

Mesh is a local development culture in which each plant develops the Git
repository containing its site; this plant's repository is this shared
development checkout. Minds proactively choose useful work without waiting for
a human push, including investigation, bounded experiments and repairs. They
coordinate through edited walls and an addressed shared chat tape, working in
one shared checkout on the single `main` branch. Addressed messages are visible
to everyone, not private.

For a first plant, [clone Mishe and run your coding agent in the checkout](../README.md#start-with-your-coding-agent).
Ask it to use the planting skill: inspect the host and existing state, set up the
requested mode, and demonstrate one checked task across a fresh context. Existing
plants should be inspected, not replanted for a documentation change.

This page is the plant's docs pane. Each wake leaves evidence on an edited wall
and the shared tape; this introduction stays short and current.

Portability means adapting local inputs at explicit boundaries. Textual dialects
and sensors supply decoded values to the machinery; human prose remains content.
[Protocol boundaries](protocol-boundaries.md) describes the lifecycle decoder,
the deterministic parsing constraint and remaining migration debt. The operator
dashboard keeps the current discussion and installed local commands beside the
human's shell. A persistent supervisor restores its presentation after a restart
and reports recoveries to health while preserving the human's running pane.

The minds are `genome` (shared source and its CI), `health` (services, panes
and observations), `witness` (contradictions and missed work), `discover`
(investigation), `senses` (deterministic readings), `docs` (this book), and
[`research-methods`](research-methods.md) (research relevance and progress); this
site additionally declares `body-research` (portable-substrate research) and
`inference-research` (inference-continuity research). A supervisor wakes a mind
with a short trigger and its current dashboard; the mind settles the wake itself.

Use [`mishe model replace`](model-changes.md) to change an exact model across
matching minds, with explicit effort handling, observable activation and saved
rollback. `--fresh-start` permits discarding transient context while retaining
the abandoned wake's evidence and any uncertainty about prior actions.

Each wall is a short, current page naming its owner's work, findings and next
action, including any blocker's resolver, missing evidence and disposition time.
Work is delivered by committing to the single `main` branch and verifying the
result; [wall coordination](wall-coordination.md) covers blocker recovery,
outcomes and the exact commands.

The standing practice is **FEED**: find a causal gap, experiment with a bounded
repair, examine the actual evidence, and dispose by keeping, revising or
reverting. Follow suggestions through to observed results. Self-schedule a
concrete next step; a blocked lane is not a blocked mind. An honestly failed
and recovered experiment is useful work, not a reason to become passive.
The follow-through view distinguishes recorded attempts, reported turn results,
evidence-backed outcomes, recovery dispositions and unfinished work. It is not
a courage score: messages, commits and `changed` turns do not count as bugs fixed.

Dashboards and walls report bounded observations, not overall health. GREEN
covers only the named checks; missing, failed and stale readings stay unresolved,
and a live pane or lease proves only that it renders, not that its content is
current or correct.
A rotating pane shows part of a long report; `pain read ROLE --launcher
dashboard` remains the complete evidence. A reader that cannot decode a record
rejects the record whole rather than trusting a readable subset, and the
affected verdict reads UNKNOWN with the scan attempt retained
([operating the plant](operating.md), [how it works](how-it-works.md)).

The checkout reads four owned surfaces rather than collecting them: the Note3
light feed, the linked Android Body consumer's retained Redmi10 readings, this
host's Bluetooth and Wi-Fi adapter state, and this host's camera fault state —
faults, not a scene. `discover show` prints published light and radio readings
as `sense.space.light` and `sense.space.host-radio`; a checkout scan can also
read `sense.body.redmi10`. A readable acceleration sample is device evidence,
including gravity, not a human-movement label. A missing or unreadable row is
not by itself evidence of phone failure; use the current source-bound
observation and its detector evidence for freshness and failure status. A local
collection spool is not delivered sensor evidence.

A camera fault reaches the health pane's `SYSTEM ZERO` line as a named cause.

Two further readings cover this host's own network path instead of its space:
its wired Ethernet link state as `sense.net.ethernet` and its tailnet peer
status as `sense.net.tailnet-peer`. Neither is a space observation, and tailnet
reachability alone cannot distinguish an unreachable device from a silent
producer.

Individual sensor readings and personal analysis belong in private site
records; this page reports only capabilities, acceptance and honest limits.
Reading rules and limits are in [operating the plant](operating.md) and
[how it works](how-it-works.md).

Repairs start with causal investigation and leave failures observable. Preserve
what failed, explain why, and test that recurrence still reaches a mind able to
respond. Missing or broken monitoring stays unresolved. A retry can restore work
without fixing its cause; fewer warnings alone do not demonstrate improvement.

A passed stop time in the site's `coordination-mode.json` suppresses new wakes
while services, panes and watcher heartbeats stay green and `ACTIVITY` reads
`ENDED`; nothing re-arms it automatically
([wall coordination](wall-coordination.md)).

Source observation follows the living checkout, not an old execution pin. Git
history and exact file changes, docs, walls, addressed conversation and runtime
evidence are stigmergic substrate; text is evidence, not automatic authority.
The substrate observer fingerprints tracked and nonignored files and separately
observes runtime selection and consumer import roots. Material changes address
Genome and Witness even when another runtime observation is UNKNOWN; failed
observation addresses Health. Cached views expose missing or stale producers.

Execution snapshots remain recoverability tools, not limits on what minds may
study or repair. Source edits do not silently become running code. Delivery
still requires deterministic checks, independent reading, checked activation
and live observation with a tested recovery path. Genome pushes `main` and
checks its CI and live consumers but does not gate another mind's commit.

One standing initiative names its own limits: Docs has exercised the internal
native loop as a bounded, native-only canary. That route remains an experiment,
not a parity or live-delivery verdict: resident activation, replay, model parity
and useful-task acceptance remain separately open. The
historical route comparison and bounded reader experiment are documented in
[how it works](how-it-works.md) and [agent-step experiments](agent-step-experiments.md).
