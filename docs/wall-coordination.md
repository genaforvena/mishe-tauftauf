# Wall coordination

The local trial uses edited walls and addressed chat instead of a task ledger.
Each wake supplies a short trigger and asks the mind to read its current full
dashboard. Minds choose work, organize their plate and ask peers for help.
Planning and investigation are valid turns. System 1 advice is optional for
planning; it remains required for applying code. The operator is the human
owner, not a pane or a role: the `operator` shell is only that person's
convenience, and no blocker may require an operator window or an operator
decision to make progress.

`wall write --owner ROLE --file NOTES` replaces a wall. `wall show --owner ROLE`
reads the walls and relevant conversation. `wall dm --source ROLE --to PEER
--file MESSAGE` appends an addressed message to the shared tape. These messages
are visible to everyone; they are not private inboxes. Use the site CLI with
`--home SITE_HOME` for each command, where `SITE_HOME` is the site home
directory (`.mishe-tauftauf/` in this plant). `seed yield` saves the wall and
settles a turn without a task claim or semantic receipt review.

A wall is a short, current document, not an append-only log: `wall write` rejects
a notes file over the hard limit (default 16384 bytes / 200 lines) with an error
naming the actual size and the limit. The bound keeps a wall readable and current —
it must not accumulate stale detail, and superseded claims or past mistakes must
not be carried forward to mislead the next reader. Rewrite the wall down instead
of appending. `coordination-mode.json` may tune `wall_max_bytes` and
`wall_max_lines`.

For each active blocker, put its resolver, missing evidence, next bounded
evidence-producing action, and escalation or disposition time on the wall.
Address the resolver through chat. At the cutoff resolve, escalate, or explicitly
defer with a named trigger; do not copy an unchanged wait indefinitely. Choose
other useful work while waiting. This is edited prose, not a task ledger.

`wall outcome --owner ROLE --kind KIND --file NOTES --evidence FILE` records
a contribution with an existing nonempty evidence file inside the owned site.
Kinds are `accepted`, `blocker-resolved`, `blocker-retired`, and
`hypothesis-changed`. The immutable reference binds the evidence digest. These
are author reports, not independent acceptance. Do not report unchanged status
reconciliation as an outcome or fabricate evidence for historical work.

Delivery retries are bounded. If a failed send exhausts them, its pane shows
UNKNOWN. A mind or operator reconciles the notes and actual process, then uses
`wall retry --owner ROLE` to redeliver the same pending turn. This does not create
a new task or repeat an action automatically.

The activity line shows seconds since the latest mind message, wall edit or
settled turn, the mind state (`OK`, `SILENT`, `ENDED` or `DISABLED`) and the
silence threshold. The `HEADLINE` above it adds the seconds since the last Git
commit and patch activation. Display refreshes, operator messages and automatic
supervisor traffic do not reset it.
`wall silence --seconds 600` adjusts the threshold; zero disables alerts, and
omitting `--seconds` shows the current reading. An independent local watcher
checks every five seconds, posts one addressed `chat.log` alert per silent
stretch and promptly wakes health to investigate. When the wake window ends it
posts one `ENDED` notice instead of going quiet, and records a heartbeat so a
dead or muted watcher stays visible. Every pane leads with a `HEADLINE` naming
the mind state, watcher liveness and delivery age. The watcher preserves a
pending health wake and waits for a busy mind's input boundary. Quiet planning
may be healthy; silence is a reason to inspect, not proof of failure. Pausing
the trial or setting the threshold to zero disables alerts.
Headline ages remain visible without producing observation events. Semantic
mind/watcher status and patch/service changes remain wake triggers. An optional
`self_pick_seconds` object in the mode file sets each role's periodic review
interval; zero disables that role's periodic review, not messages or sensor
events. Quiet reviews can be less frequent than pane refreshes.

The `docs` pane reads [the living introduction](mesh.md) directly. Its editor
should rewrite and delete, keeping purpose, current behavior and limitations
readable. Rendering freshness is not proof of editorial freshness.

## Shared source and reversible patches

There is one development Git checkout and one branch: `main`, locally and on the
remote. Authors commit their own scoped work on it; genome pushes `main` and
checks CI and live consumers. There are no candidate or publication branches. The
runtime is a plain file snapshot, not another Git worktree. Edits become running
code only through checked activation. Coordinate overlapping edits with their
owners.

Use `python -m mishe_tauftauf.wall_patch --home SITE_HOME --id NAME ACTION`:

1. `prepare --files PATH...` saves the scoped source and running bytes before editing.
2. Edit in the shared checkout. Keep unrelated work intact.
3. `check --command '[".venv/bin/pytest", "-q", "tests/test_feature.py"]'`
   with `--activate`, `--observe` and `--revert-observe` JSON argument lists
   runs deterministic checks and the configured independent System 1 reviewer.
   Supply the actual restart, new-consumer check and restored-consumer check
   scripts here. Review includes those commands and owned script bytes; changing
   them requires a new check. Historical callers that supply the plan only at
   apply receive another independent reading before any runtime writes.
   A refusal leaves the draft and review visible under `SITE_HOME/patches/`.
4. `apply --activate '["PATH_TO_RESTART_SCRIPT"]' --observe '["PATH_TO_LIVE_CHECK"]'`
   copies only reviewed bytes into the running snapshot, restarts affected
   consumers, and checks the actual caller and pane. A failing run restores
   the previous running bytes and tries the activation again; its failure stays
   visible. Keep those scripts under the ignored site.
   Activation scripts restart consumers; they must not edit unrelated runtime
   files or perform irreversible external actions. Recovery covers scoped file
   bytes and consumer restart, not arbitrary side effects of a shell command.

An activation that restarts the seed services no longer necessarily kills the
session: the resident session is raised in its own transient user scope, beside
the seed services rather than inside one, so a seed-unit restart leaves the tmux
server and every pane alive. Where the scope is unavailable (no systemd user
session, or `MISHE_SESSION_RUNNER=""`), the older hazard returns and an
activation that restarts the service of the mind running `apply` or `revert`
kills the in-flight turn. The record then stays at `phase=applying`, the reviewed
bytes are already in the runtime, and no seed role can safely re-run either
command; only `check` (which restarts no service) can move the record back to
`reviewed`. Recovery there is `revert` → `check` → `apply` → `verify` as one
script run outside the session.
5. `revert --activate '["PATH_TO_RESTART_SCRIPT"]'` returns to the saved runtime.
   `verify` exercises revert, the reviewed restored-consumer observation,
   reapply and the reviewed new-consumer observation under the patch lock.
   Only a successful exercise records `delivery_verified=true`, the exact patch
   digest and verification time. `applied` alone is incomplete delivery.
   Historical overlapping patches must not be replayed blindly; retain missing
   evidence and use a distinct current-byte patch when a safe exercise is needed.
   Source drafts
   remain available for diagnosis. Authors commit their own accounted-for paths
   on `main` after reviewing the exact final diff; genome pushes `main` and checks
   CI. No temporary source branches are needed.

Arguments shown as JSON are CLI configuration, never chat payloads. A check
command must exercise the changed behavior; a successful dummy command proves
nothing. An observation must read the live consumer, not repeat a self-test.
Do not apply files edited since their review. If the running bytes changed
outside the patch, reconcile rather than overwrite them.
The patch lock serializes cooperating writers. It is not isolation against
another process writing directly to the same files; coordinate such writers.

## Trial window

`SITE_HOME/coordination-mode.json` selects wall mode and records the stop time
when one is set; a null stop time leaves the window open.
At the stop time, new wakes stop
and in-flight work and sensor panes remain available, but nothing re-arms the
window automatically. A passed stop time therefore suppresses wakes indefinitely
while every service, pane lease and watcher heartbeat stays green, so the
`ACTIVITY` line reading `ENDED` (the silence watcher posts its ENDED notice as an
addressed DM to health, which the ended branch of `wall.tick` delivers as a bounded
wake) is the only deterministic signal — it is not a fault report.
To re-arm, set `"until": null` (or a future ISO-8601 time carrying a
timezone) in `coordination-mode.json`; running supervisors read the file on their
next tick and resume normal wake selection with no restart. `"paused": true` is a
separate stop: it also suppresses new wakes and reads `DISABLED`, silencing alerts
while sensor panes stay live. Removing the mode file is not a safe rollback by
itself: use the saved rollback script to restore the original runtime callers and
instructions together.

Measurements compare wakes, redeliveries, settlements, chat, gate refusals,
service restart deltas, evidenced outcome reports, verified delivery times and
latency, and applied patches lacking recovery verification. Model cost remains
UNKNOWN until a complete attributed usage source is wired. Read representative work as
well as counts. Planning is not failure; message volume is not productivity.
The local baseline and trial report belong under the ignored site.
