# Mesh

[README](../README.md) · [Getting started](getting-started.md) · [How it works](how-it-works.md) · [Operating the plant](operating.md) · [Wall coordination](wall-coordination.md)

Mesh is a local development culture in which each plant develops the Git
repository containing its site; this plant's repository is this shared
development checkout. Minds choose useful work, including planning and
investigation, and coordinate through edited walls and an addressed shared
chat tape, working in one shared checkout on the single `main` branch.
Addressed messages are visible to everyone, not private.

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
Session startup waits for tmux itself to become available. Resident units create
their per-site tmux socket directory before launch and set `TMUX_TMPDIR`, keeping
each site's sessions isolated. A probe of another site's home resolves that site's
own socket as well, so a process handed the wrong `TMUX_TMPDIR` — the release
coordinator's unit is one — still reads the session its target actually owns.
Integration tests use a separate disposable tmux socket so their cleanup cannot
close live panes.

The minds are `genome` (shared source and its CI), `health` (services, panes
and observations), `witness` (contradictions and missed work), `discover`
(investigation), `senses` (deterministic readings), `docs` (this book), and
[`research-methods`](research-methods.md) (research relevance and progress); this
site additionally declares `body-research` (portable-substrate research) and
`inference-research` (inference-continuity research). A supervisor wakes a mind
with a short trigger and its current dashboard; the mind settles the wake itself.

Each wall is a short, current page naming its owner's work, findings and next
action, including any blocker's resolver, missing evidence and disposition time.
Work is delivered by committing to the single `main` branch and verifying the
result; [wall coordination](wall-coordination.md) covers blocker recovery,
outcomes and the exact commands.

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

Source edits do not change running code. CI, checkout source and running code are
separate evidence: the CLI runs a pinned release, while each pane and service
loads its own snapshot, and any of those snapshots can lag the committed source.
A green check is not proof of what a service or pane has loaded; delivery is
complete only after deterministic checks, an independent reading, checked
activation and live observation, with a tested recovery path. Genome pushes
`main` and checks its CI and live consumers but does not gate another mind's
commit.

One standing initiative names its own limits: the Docs mind is currently
exercising the internal native loop for its bounded canary, while the external
harness remains the fallback for work classes without an evidenced native
route. This route selection is not a parity or live-delivery verdict: resident
activation, replay, model parity and useful-task acceptance remain separately
open ([how it works](how-it-works.md); the bounded reader experiment is documented in [agent-step experiments](agent-step-experiments.md)).
