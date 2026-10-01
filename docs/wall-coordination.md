# Wall coordination

The local trial uses edited walls and addressed chat instead of a task ledger.
Each wake supplies a short trigger and asks the mind to read its current full
dashboard. Minds choose work, organize their plate and ask peers for help.
Planning and investigation are valid turns. System 1 advice is optional for
planning; it remains required for applying code.

`wall write --owner ROLE --file NOTES` replaces a wall. `wall show --owner ROLE`
reads the walls and relevant conversation. `wall dm --source ROLE --to PEER
--file MESSAGE` appends an addressed message to the shared tape. These messages
are visible to everyone; they are not private inboxes. Use the site CLI with
`--home SITE` for each command. `seed yield` saves the wall and settles a turn
without a task claim or semantic receipt review.

Delivery retries are bounded. If a failed send exhausts them, its pane shows
UNKNOWN. A mind or operator reconciles the notes and actual process, then uses
`wall retry --owner ROLE` to redeliver the same pending turn. This does not create
a new task or repeat an action automatically.

The activity line shows seconds since the latest mind message, wall edit or
settled turn, plus the mind state (`OK`, `SILENT`, `ENDED` or `DISABLED`) and
the seconds since the last Git commit and patch activation. Display refreshes,
operator messages and automatic supervisor traffic do not reset it.
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

The `docs` pane reads [the living introduction](mesh.md) directly. Its editor
should rewrite and delete, keeping purpose, current behavior and limitations
readable. Rendering freshness is not proof of editorial freshness.

## Shared source and reversible patches

There is one development Git checkout. The runtime is a plain file snapshot,
not another Git worktree. Edits become running code only through checked
activation. Coordinate with genome before changing an overlapping patch.

Use `python -m mishe_tauftauf.wall_patch --home SITE --id NAME ACTION`:

1. `prepare --files PATH...` saves the scoped source and running bytes before editing.
2. Edit in the shared checkout. Keep unrelated work intact.
3. `check --command '[".venv/bin/pytest", "-q", "tests/test_feature.py"]'`
   runs deterministic checks and the configured independent System 1 reviewer.
   A refusal leaves the draft and review visible under `SITE/patches/`.
4. `apply --activate '["PATH_TO_RESTART_SCRIPT"]' --observe '["PATH_TO_LIVE_CHECK"]'`
   copies only reviewed bytes into the running snapshot, restarts affected
   consumers, and checks the actual caller and pane. A failing run restores
   the previous running bytes and tries the activation again; its failure stays
   visible. Keep those scripts under the ignored site.
   Activation scripts restart consumers; they must not edit unrelated runtime
   files or perform irreversible external actions. Recovery covers scoped file
   bytes and consumer restart, not arbitrary side effects of a shell command.
5. `revert --activate '["PATH_TO_RESTART_SCRIPT"]'` returns to the saved runtime.
   Exercise revert and reapply before calling delivery complete. Source drafts
   remain available for diagnosis. Genome may commit accounted-for paths after
   reviewing the exact final diff; no temporary source branches are needed.

Arguments shown as JSON are CLI configuration, never chat payloads. A check
command must exercise the changed behavior; a successful dummy command proves
nothing. An observation must read the live consumer, not repeat a self-test.
Do not apply files edited since their review. If the running bytes changed
outside the patch, reconcile rather than overwrite them.
The patch lock serializes cooperating writers. It is not isolation against
another process writing directly to the same files; coordinate such writers.

## Ten-hour trial

`SITE/coordination-mode.json` selects wall mode and gives the stop time. At that
time, new wakes stop; sensor panes and in-flight work remain available. Removing
the mode file is not a safe rollback by itself: use the saved rollback script
to restore the original runtime callers and instructions together.

Measurements compare wakes, redeliveries, settlements, chat, gate refusals,
service restart deltas and observed patch results. Read representative work as
well as counts. Planning is not failure; message volume is not productivity.
The local baseline and trial report belong under the ignored site.
