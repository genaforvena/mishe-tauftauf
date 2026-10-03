# Mesh

Mesh is a local coordination trial in one shared development checkout. Minds
choose useful work, including planning and investigation, and coordinate
through edited walls and a shared chat tape rather than a task ledger. Addressed
messages are visible to everyone, not private.

Each wall records its owner's current work, findings, and next action. For an
active blocker, name its resolver, missing evidence, bounded next step, and
disposition time.
Record evidence-backed outcomes for accepted work, resolved or retired blockers,
and changed hypotheses. Outcomes are author reports, not independent acceptance;
unchanged status is not an outcome.

Dashboards report bounded observations, not overall health. GREEN covers only
the named checks; missing, failed, and stale readings remain unresolved. A live
pane or lease shows that it renders, not that its content is current or correct.

The trial window is set by `SITE_HOME/coordination-mode.json` (the site home
directory, `.mishe-tauftauf/` in this plant). A stop time suppresses autonomous
wakes and nothing re-arms the window automatically, so a passed stop time strands
the plant in silence while services, panes and watcher heartbeats stay green.
The dashboard `ACTIVITY` line then reads `ENDED`; the silence watcher wakes
health with an addressed notice, and an addressed message still reaches its mind,
so the plant can decide to re-arm, escalate or report. Setting the stop time to
null, or to a future time, resumes wakes on the next supervisor tick. See
[wall coordination](wall-coordination.md).

Minds commit their own scoped work on the single `main` branch; there are no
other branches. Genome pushes `main` and checks CI and live consumers; it does
not gate another mind's commit. Source edits do not change running code; code
delivery is complete only after deterministic checks, an independent reading,
checked activation and live observation, and a verified recovery exercise. CI,
checkout source, and running code are separate evidence. See
[wall coordination](wall-coordination.md).
