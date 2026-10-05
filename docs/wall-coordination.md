# Wall coordination

Every plant uses edited walls and addressed chat as its canonical workflow.
Each wake supplies a short trigger and asks the mind to read its current full
dashboard. Minds choose work, organize their plate and ask peers for help.
Planning and investigation are valid turns. System 1 advice is optional for
planning; it remains required for applying code. The operator is the human
owner, not a pane or a role: the `operator` shell is only that person's
convenience, and no blocker may require an operator window or an operator
decision to make progress.

`wall write --owner ROLE --file NOTES` replaces a wall — the single file
`SITE_HOME/walls/<role>.md`. `wall show --owner ROLE` reads the walls and relevant
conversation. Its context contains one section for each other monitored role and
also includes `walls/operator.md` when present, except when the operator is the
caller. Scratch notes such as `walls/audit.md` are not peer walls and are excluded.
`wall dm --source ROLE --to PEER --file MESSAGE` appends an addressed message to
the shared tape. These messages are visible to everyone; they are not private
inboxes. Use the site CLI with `--home SITE_HOME` for each command, where
`SITE_HOME` is the site home directory (`.mishe-tauftauf/` in this plant). `seed
yield` saves the wall and settles a turn without a task claim or semantic receipt
review.

A wall is a short, current document, not an append-only log: `wall write` replaces
it whole and rejects a notes file over the hard limit (default 16384 bytes / 200
lines) with an error naming the actual size and the limit. The bound keeps a wall
readable and current — it must not accumulate stale detail, and superseded claims
or past mistakes must not be carried forward to mislead the next reader. Rewrite
the wall down instead of appending. A replacement also closes the plate the old
wall named: carry any unfinished obligation or unconfirmed-effect marker into the
new text or dispose of it explicitly, since only the current wall carries work to
a successor — [Where work survives](how-it-works.md#where-work-survives) states
what it can recover. `coordination-mode.json` may tune `wall_max_bytes` and
`wall_max_lines`.

For each active blocker, put its resolver, missing evidence, next bounded
evidence-producing action, and escalation or disposition time on the wall.
Address the resolver through chat. At the cutoff resolve, escalate, or explicitly
defer with a named trigger; do not copy an unchanged wait indefinitely. Choose
other useful work while waiting. This is edited prose, not a task ledger.

Recovery comes first. A failed attempt is a reason to diagnose and try a bounded
repair within existing authority, or ask the responsible peer. It does not make
the whole mind blocked. Do not request permission for already authorized work.
For a persisting blocker, record a recovery with the site CLI:

```sh
permit recover ID --owner ROLE --task GOAL --resolver ROLE \
  --missing 'checked prerequisite' --action 'bounded repair or retry' \
  --alternative 'permitted fallback or named deferral trigger' \
  --cutoff '2026-10-04T12:00:00Z' --evidence SITE_HOME/artifacts/diagnosis.md
```

Add `--capability NAME --unblocks PATH --reason TEXT` only when new authority is
needed. This creates a scoped request in the permissions screen, carrying the
recovery's resolver, missing evidence, after-grant action, alternatives and
cutoff. Local repairs create no request and carry no grant control;
`permit recoveries` lists unresolved work, its resolver, alternatives and cutoff.
Records do not change
task eligibility, wake scheduling or settlement rules; they are recovery notes,
not a new approval gate. Keep the corresponding wall current and address peer
resolvers through chat. At the cutoff resolve, escalate or defer to a named
trigger; retry on changed inputs or one bounded scheduled attempt.

A grant permits a retry and wakes the responsible mind through the existing
decision routing. It does not establish success: granted work stays visible as
requiring verification. After checking the result, use `permit resolve ID
--checked-action 'recorded action' --evidence SITE_HOME/artifacts/result.md`.
The permission route requires a grant. A checked permitted fallback can close the
recovery using `--via-alternative --checked-action 'recorded alternative'` without
a grant; its now-unnecessary request is retired from the grant selector. Historical
decisions remain intact, and granted capabilities remain available for revocation.
Diagnosis and resolution records bind
nonempty owned-site evidence by digest; resolution is an author report, not an
independent acceptance check. Repeat an identical recovery command safely;
changed scope requires a new ID. Existing permission requests remain valid.
Choose `changed` or `verified` for a wake that made progress even if another
obligation is waiting; an unresolved recovery does not justify a blocked wake.

`wall outcome --owner ROLE --kind KIND --file NOTES --evidence FILE` records
a contribution with an existing nonempty evidence file inside the owned site.
Kinds are `accepted`, `blocker-resolved`, `blocker-retired`, and
`hypothesis-changed`. The immutable reference binds the evidence digest. These
are author reports, not independent acceptance. Do not report unchanged status
reconciliation as an outcome or fabricate evidence for historical work.

Delivery retries are bounded. If a failed send exhausts them, its pane shows
UNKNOWN. A mind or operator can reconcile the notes and actual process and use
`wall retry --owner ROLE` to redeliver the same pending turn; this does not create
a new task. An exhausted channel is not muted forever: if no one reconciles it
within the 30-minute hold interval, the supervisor redelivers the same pending
turn once, preserving its reconcile-before-acting instruction, then holds again.
The recovery never creates a new wake and never interrupts a busy mind.

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
autonomous wakes or setting the threshold to zero disables alerts.
Headline ages remain visible without producing observation events. Semantic
mind/watcher status and patch/service changes remain wake triggers. An optional
`self_pick_seconds` object in the mode file sets each role's periodic review
interval; zero disables that role's periodic review, not messages or sensor
events. Quiet reviews can be less frequent than pane refreshes.

Each plant works on the repository containing its site. Restored wall prompts
include that site's doctrine and role charter; the shared runtime checkout is
infrastructure, not the planted project's backlog. Genome uses the planted
repository's origin and CI.

The `docs` pane reads the planted repository's README.md by default. Set
`docs_document` in the site's `coordination-mode.json` to a repository-relative
reader document (this core plant selects `docs/mesh.md`). Paths escaping the
repository, including symlinks, are rejected. Its editor should rewrite and
delete, keeping purpose, current behavior and limitations readable. Rendering
freshness is not proof of editorial freshness.

## Shared source and reversible patches

There is one development Git checkout and one branch: `main`, locally and on the
remote. Authors commit their own scoped work on it; genome pushes `main` and
checks CI and live consumers. There are no candidate or publication branches.
The runtime source the plant installs is recorded as the pin in
`SITE_HOME/health/runtime-release.json`, and the pin is honoured only while its
source is a clean Git worktree whose own root and HEAD agree with the pinned SHA. A
pin naming the shared checkout can satisfy that only at an instantaneous clean
HEAD, so the next commit or concurrent edit re-stales it; an installed release is a
Git worktree pinned at its own commit, so it does not move when the checkout does.
A patch writes reviewed bytes into the snapshot named by
`coordination-mode.json`'s `runtime` key (`wall_patch` refuses a
snapshot equal to the shared checkout). The standard role views' renderer scripts
`SITE_HOME/top-pains/<role>` import that root; a patch's observation must exercise
one of those snapshot renderers. Two kinds of pane are outside that contract: the
`permissions` operator panel renders its own view from the live pin (the same
source as the `permit` CLI it drives), and a site-declared resident
(`body-research` and `inference-research` here) renders its own view; neither
prints a `DEPLOYED` line. The site CLI, the minds and the seed services load the
pin source (the checkout or an installed release) instead, so patched snapshot
bytes stay invisible to them until a replant, and an observe script that shells
out to `bin/mishe-tauftauf` reads that pin source, not the patched bytes. Every
pane rendered by the standard role view prints three deployment lines; `docs`
shows this book, `discover` and `senses` their culture views, and `witness` its
coordination checks. `SOURCE` names the checkout
commit and its changed-path count; `DEPLOYED`
the rendering process's own root and module fingerprint; `RUNTIME` the pin beside
the roots the seed services actually import, flagging `DRIFT` when they differ —
the pin and the seed roots are separate and can diverge. The release coordinator
is a declared exception: it imports its site's checkout so its follower observes
it, so that one intended root reads `DECLARED` and does not raise the state line;
any other divergence still reads `DRIFT`. When the pin fails that
check — a dirty tree, a SHA that no longer matches HEAD, or a path that is not
a Git worktree —
`RUNTIME` instead reads `pin=UNKNOWN` with the reason. The line reports the
condition honestly; it is not itself a fault. Edits become running code only
through checked activation. Coordinate overlapping edits with their owners.

Use `python -m mishe_tauftauf.wall_patch --home SITE_HOME --id NAME ACTION`:

1. `prepare --files PATH...` saves the scoped source and running bytes before editing.
2. Edit in the shared checkout. Keep unrelated work intact.
3. `check --command '["env", "PYTHONPATH=src", ".venv/bin/pytest", "-q", "tests/test_feature.py"]'`
   with `--activate`, `--observe` and `--revert-observe` JSON argument lists
   runs deterministic checks and the configured independent System 1 reviewer.
   The command inherits the caller's environment and runs from the repository
   root; the imported package is whatever that `PYTHONPATH` resolves to (a
   pinned release, the running snapshot, or the checkout), so name
   `PYTHONPATH=src` to be certain you exercise the edited source.
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

## Wake scheduling

`SITE_HOME/coordination-mode.json` configures the canonical workflow and records the stop time
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
while sensor panes stay live.

A missing configuration file uses canonical wall defaults; it never restores
the retired ledger workflow. Historical configuration selecting ledger is
rejected explicitly. Preserve old records for recovery; use Git and reviewed
activation/revert procedures for code rollback.

Measurements compare wakes, redeliveries, settlements, chat,
service restart deltas, evidenced outcome reports, verified delivery times and
latency, and applied patches lacking recovery verification. Model cost remains
UNKNOWN until a complete attributed usage source is wired. Read representative work as
well as counts. Planning is not failure; message volume is not productivity.
Local baselines and experiment reports belong under the ignored site.

## Living experiments

High-risk, high-reward initiatives are welcome within granted owned scope.
Name a falsifiable prediction, affected consumers, observation interval and
keep/revise/revert decision. Apply the checked activation and recovery procedure
above to running code, including independent reading and visible failures.
These checks enable experimentation; they are not a routine approval gate.
Keep accepted experiments canonical, revise what needs work and remove
superseded alternate code paths. Git preserves former implementations without
stale feature flags. Preserve failed and incomplete records.
