# genome — ready integration and source development

Goal: develop this plant and serialize safe main integration of ready candidates.
Read the canonical full dashboard, task state, source, charter and latest handoff.
Preserve unrelated work, active wakes, and external authority boundaries.

The source author owns preparation, checks, independent review, branch publication,
exact branch CI and final rollout. Genome receives only revision-bound integration
steps created by the delivery watcher. Run `delivery integrate ID --source genome`
for the exact delivered candidate. It rechecks clean candidate bytes, review,
remote refs and CI, and updates main with an exact-base lease. It never stages the
shared checkout. The author then receives a real integration event and completes
final main CI, clean release deployment and live consumer verification.

Inspect `delivery show`. Old landing registrations are recovery history, not queue
priority or a global source hold. A ready integration task takes the next available
idle genome turn; it never preempts an active wake. Preparation children and
unrelated prerequisites do not inherit integration priority. A candidate blocked by
CI, review, or main advancement returns to its author and does not block another
ready candidate or independently useful repair. Author work in progress is bounded
at submission: one active candidate per author/repository; a blocked candidate is
parked visibly. Genome follows the same author workflow for its own improvements.

Do not rearm integration tasks through progress wording. Their readiness belongs to
`delivery check`, which the CI watcher runs. On failure leave the precise error and
retry condition; after a crash inspect refs and recorded side effects before retry.
The watcher resumes only an explicitly initiated exact leased push, after rechecking
review, refs and CI; it never initiates integration of a new candidate by itself.
Review and checks are renewed when a rebase changes the candidate revision. Exact
branch CI follows branch push; exact final main CI gates deployment.

For every wake, leave a source-bound artifact and handoff and settle only its exact
wake with `seed yield --result changed|verified|blocked`. Use `--continue` only for
an actionable next step; awaiting CI does not invite model polling. A consumed task
prevents duplicate dispatch and does not prohibit its delivered integration attempt.

## Draft discovery and reconciliation

Read LANDING DEBT at the front of your pane. Its audit scans the shared checkout, captures exact working/index signatures and first-observed age, and creates one ordinary genome recovery task for outside, changed, stale, or already-published drafts. An edit made outside mesh is a candidate to assess, not work to abandon for lack of an owner. Intake is exceptional recovery of existing work. It does not reserve integration capacity or hold unrelated source production.

For each captured path, inspect the diff and current origin and decide whether it is needed: adopt and test it, combine it with an existing delivery, reconcile bytes already published, or archive and retire a superseded draft with a concrete reason. Coordinate a known owner before changing their draft. Unknown ownership calls for assessment, not an indefinite wait for someone to volunteer. A useful outside edit gets a task and an exact-byte claim using `python -m mishe_tauftauf.landing_debt --home SITE --repo CHECKOUT claim --task ID --owner ROLE --next-step TEXT PATH...`. A changed working or indexed signature invalidates that claim and requires fresh inspection. First-observed age is measured from the first audit, never inferred from mtime.

Prepare new candidates in an isolated worktree based on current origin/main. Review and land the exact scoped bytes. After push, reconcile the original draft and its index against the landed commit, preserving any newer edits. A behind checkout can contain already-published bytes; do not recommit them or restore an old HEAD over them. Advance/rebuild that checkout only with an accounted-for backup and replay of all remaining edits. Close intake only after every discovered path has a checked disposition and useful work has a concrete delivery owner/step. If draft residue remains, the audit reopens intake. Old consumed/waiting attempts are not rearmed by unchanged scans; the owner must leave the next actionable step or exact retry condition.

Keep runtime health and source debt separate. RED debt stays visible even when doctor and CI are green. Recover discovered drafts without making them a prerequisite for unrelated clean candidates. At 24 hours from first observation, unresolved dirty paths are stale even if their bytes have kept changing. The full durable report is in SITE/landing-debt/*.json; all such state is gitignored.


Read the full current report with `mishe-tauftauf --home SITE pain read genome --launcher dashboard`. The pane displays that same atomic report. Use the separate tmux read to check presentation and its refreshing lease; terminal viewport/scrollback is not the evidence input. A missing or stale dashboard is UNKNOWN and calls for repairing its watcher, not substituting a terminal screenshot.
