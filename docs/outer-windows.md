# Outer windows: registry

Read-only tmux windows in the outer `plants` session. Each is a `tools/*_view.py` renderer,
except `budget`, whose renderer lives in the site (`SITE_HOME/operator/budget/`, gitignored, not in git):
plain words first (state, why it matters, next action), numbers second, UNKNOWN when not measured.
Each must stay dependency-free so Mishe still works with the window closed, and must fit the
pane it draws in: 41 rows, and 170 columns for the health view, which is the widest.

Terms used below:

- **plants session**: the tmux session named `plants` that holds the outer windows. A **window** is one tab in it; a window can hold several **panes** (side-by-side or stacked terminal areas), and each pane runs one command.
- **pin**: the `runtime/current` link naming the release that is running. Only the delivery path moves it; a pane keeps the pin it loaded until restarted, as [the mesh overview](mesh.md) explains.
- **registry**: the file operator/window-registry.json in the site home, the list of windows and the `restore` flag the restorer reads (see "Restoring missing windows" below).
- **duty**: a scheduled check listed in `duties.json`, run on its own interval. A duty wakes nobody by itself; [How a mind chooses](how-a-mind-chooses.md) explains the difference from a mind.
- **UNKNOWN**: the reading when a check could not look. It is never shown as a pass.

| window | tool | owner | purpose |
| --- | --- | --- | --- |
| health | tools/health_view.py | steward | verdict of the tending pass (guard, units, wakes, signals, delivery) |
| senses | tools/household_view.py | senses | the apartment map and what Mishe senses |
| senses-map | tools/map_view.py | senses | terminal tile map of the apartment (rooms from floorplan.json, best-guess people from map-state.json), read-only; `--once` prints one frame; needs tools/map_render.py run first |
| network | tools/network_view.py | network | home network and connection |
| delivery | tools/delivery_view.py (looped each minute by the window command) | genome | checked-to-live time per promotion: check stage, wake fence, drain, review |
| wake-view | tools/wake_view.py (looped each 5 min by the window command) | steward | where engine wall time goes across retained wakes |
| house | tools/house_view.py (looped each minute by the window command) | senses | forecasts about the house written before each hour and scored after; see [House predictions](house-predictions.md) |
| budget | operator/budget/budget_view.py (site-local) | budget | provider quota and spend |

The delivery window summarizes how long commits waited for their promotion to start. The
interval between delivered promotions runs from the previous finish to the next
start and can include time before new commits and time spent on failed attempts. A long
gap alone is not slow dispatch: when no new commit landed in the interval it measures idle
time, and a short gap can follow commits already waiting before the last delivery finished.
The per-commit wait row is split into a busy queue and an idle cadence share: a commit
landing while another promotion held the single path waits for that run to finish and then
for the next cadence tick; one landing with no run in flight waits only for the tick and
settle bound. Read that split, not the combined mean, for dispatch latency — a large mean
can be mostly commits waiting on a busy path, so the lever is promotion cycle time, not the
cadence tick. The split gives a failed run its best recorded end (a rollback end, or its
check stamp as a lower bound), so that run still counts as occupying the path. Look for an
UNKNOWN record inside the gap before blaming a failed attempt.
The `waiting for wakes (fence)` row is broken out below into quiet gaps (under a second),
polled fences where the gap fell inside the quiet poll, and hold-waits that wait for a
running wake to release the shared dispatch lock. The overlap, wake-start and
holder-attribution lines divide those hold-waits by which wakes held the lock. A fence the
routing ledger cannot cover reads UNKNOWN and is left out of the mean — it is not zero
overlap and not evidence of a failed attempt. For the measured cost and why the quiet poll
is short, see [Wall coordination](wall-coordination.md).

The `fence-holder wake lengths` line is a regression check, not a fault count. `over cap+5s`
counts holder spans beyond the 35-minute engine hard cap with a five-second recording slack:
wakes cut at the cap routinely log their outcome one second late, and a real escape (the old
MiniMax era ran past 50 minutes) is far beyond that slack. The `>=20m00s` share is the diagnostic:
it reports the fence seconds donated by near-cap holders even when no wake breached the cap, so a
long fence can be explained without an alarm. With no paired wake span the length line is absent
and the attribution above reads UNKNOWN, never a zero overlap.

The wrong-pin count includes failed promotions whose cause was an active or queued
consumer on the wrong pin, including cases where the rollback probe failed. An
unreadable promotion record makes the total UNKNOWN. Any wrong-pin failures
confirmed by readable records still appear below it.

The `wake-view` window's `in flight` count is not a count of engines running now. It counts
distinct wakes whose first `select` is inside the seven-day outcome window and whose id has no
`outcome` anywhere in the retained 40000-line ledger tail. A missing outcome stays in the count
until its select ages out, even if the wake wrote a yield receipt. Finished wakes enter the
window by outcome time and missing finishes by select time. A duration pairs the first select
with the outcome. This starts after dispatch admission and can include routing fallbacks.
An outcome with no retained select has an UNKNOWN duration.

The seven-day `hard cap` class pools recorded timeout reasons across cap settings. For a
recent comparison, run `python3 tools/wake_view.py --window 86400`. For a specific outlier,
read that wake's outcome reason in `engine/routing-ledger.jsonl` under the site home to
find the cap recorded for its attempt. Compare it with the paired duration and, when present,
the monotonic `engine_seconds` in `wakes/<wake>.work.json`. The work receipt measures the
whole router call, including any retries and cleanup, so check for multiple selects before
attributing an overage to one attempt.

The `house` window shows the prediction log's scoreboard (`tools/house_view.py`, refreshed each minute). Its
`LIVE` table counts only forecasts written before their hour; the `REPLAY` table is a retrospective walk-forward
over stored history and is never mixed into it. A scoreboard older than 45 minutes prints `STALE`; a missing one
prints `UNKNOWN` (exit 3). The `house-predictions` duty (`duties.json`, every 900 s, resolver senses) writes it.
The renderer reads the terminal height on each refresh and leaves a row for its final newline, which
keeps the header and freshness summary in view at the wide pane's 170-column layout. The cut notice
reports when the visible frame ends before `REPLAY`. Redirected output uses the 41-row cap.

Two words in that window need no machinery to read. `hash chain OK` means every stored forecast line still
carries the hash of the line before it; `hash chain BROKEN at line N` means line N was edited or removed after
it was written. `unobserved` counts forecasts whose hour was not measured; they are never counted as a zero error.

The `budget` window runs `operator/budget/budget_pane.sh` (site-local, under `SITE_HOME`), a loop that calls
`budget_view.py` every 20 s and clears the screen between frames, so the registry's
command and the table's tool name the same view.

The `senses` window's `LISTENER` line reports the audio listener process from its own heartbeat,
not from the audio log. A fresh heartbeat names the process state and coverage (live pane,
2026-11-20 17:19Z: `LISTENER: LISTENING, coverage GREEN (heartbeat 7s old)`); a missing, malformed
or more than two minutes old heartbeat reads `UNKNOWN` with the reason. Read that line separately
from the `SOUND` rows below it: a stale sound row can mean a dead microphone or a listener that
never started, and the `LISTENER` line tells the two apart. It carries derived state only, never
audio content.

The `senses-map` window has a renderer behind it: the `map-render` duty in
`duties.json` runs `tools/map_render.py` every 180 s as the
`mishe-producer-map-render.timer` producer unit, and the `map-render` signal
carries the verdict (resolver: senses). Producer units are rendered from the
served release's own `duties.json`, so the timer appears when a release carrying
the duty is promoted — it was not present under release `35748377`. The viewer
flags a render older than 30 minutes as stale, so a fresh render keeps the pane
from reading STALE.

Several lines in that pane are easy to misread. `activity living` and `activity bedroom` give the age of the newest usable 15-minute motion bucket, and their `(fresh)`/`(stale)` tag comes from the producer's own `current`/`historical` verdict (a bucket older than 30 minutes), so the map text and `map-state.json` cannot disagree; the bar uses that same 30-minute scale. A healthy camera can read an 18-minute-old bucket at a draw just before the next bucket completes — bucket cadence, not a camera failure. The separate `camera frame` line gives the newest frame's own age, and says `camera frame UNKNOWN` when the producer published no dated frame, so a reader can tell the two clocks apart. That is a different freshness from the 30-minute render staleness above. The `phone Redmi10` bar is not a reliability score: it measures only the newest collector row's age, so a phone that reported seconds ago reads a full bar however often it was silent before. The `phone history` line is the reliability reading — drop onsets and the share of fresh rows over seven days from the same collector rows the senses window uses (live pane: `phone history: 6 drop onsets in 7d, fresh in 18% of 4506 samples; bar is newest-row age only`); with no usable history it says `phone history UNKNOWN` instead of claiming a quiet zero. Under the `PHONES` heading, a `signal` line per phone that is not current gives the reason from the household-phones signal detail (for example the Redmi10 row age against its limit); a phone that is ok prints no line, and a missing or unreadable detail prints `signal household-phones detail: UNKNOWN`, never ok. The `night prior 21-05Z` line checks the assumed local-night window against measured living-room daylight and activity: it says `matches` only when 21Z is both the darkest and quietest hour, prints the measured values when it is `unvalidated`, and stays `timing UNKNOWN` when a curve is missing or older than 30 minutes, or the renderer publishes a different window. The legend's person lines date the last label instead of counting its hours, for example `? Ilya: UNKNOWN, last known bedroom (label on 2026-11-11, 8.7d ago)`: the date comes from the label's own timestamp when it is a valid ISO date, a missing or malformed timestamp falls back to `h old` rather than claiming a day it cannot support, and on a guess line the date still belongs to the last label, which may be in another room. A last label in a room the floorplan does not draw is named with `(not drawn on this map)` and gets no sprite, so a recorded place is never reported as `no place recorded`. The `rssi` line is one Wi-Fi feature, not a room claim: its `nearest` or `tie between` wording only names the closest labelled room in dBm, and when every label reads the same dBm the line appends `(all 3 labels -60 dBm, no room spread)`, because equal values cannot rank rooms. The leave-one-out count against the majority baseline is what says whether the feature places anyone at all; a missing phone table or stale live vector reads `rssi UNKNOWN`, never a guess.

The `audio livingroom` line is a derived freshness, not a person or transcript claim. It reads a bounded tail (200 KB, about 70 minutes) of the living-room audio log and prints the age of the newest speech row alongside the newest listener row, or `no speech in last X` when no speech row is present; `speech X ago` counts a row whose status is `HINT` or `NO_HINT` (`NO_HINT` means speech was heard without a hint word), and the line ends `(not a person claim)`. A listener row older than 150 seconds, a missing log, a malformed or partial row, or a log with no dated living-room row reads `audio livingroom UNKNOWN` with the reason. This is separate from the `LISTENER` line in the `senses` window above, which is the listener process's own heartbeat; neither draws audio content.

The `night prior 21-05Z` line checks the assumed sleep window against measured hourly living-room light and camera movement. It reads `matches` only when both the darkest and quietest hours are 21Z; otherwise it prints `unvalidated` with the measured hours and numbers. It prints `timing UNKNOWN` when either hourly curve is missing, older than 30 minutes, or lacks the needed 21Z hour, and `UNKNOWN (renderer publishes a different window)` when a renderer marker carries a different sleep window — so no window is judged that nobody agreed on. The saved map file and the terminal `senses-map` pane draw their derived status lines from one shared builder, so a reader sees the same lines and the same fail-closed UNKNOWNs in both.

The `network` window is watched by its own duty: `network-window`
(`duties.json`, every 300 s, `max_age` 900 s, resolver `network`) runs
`checks/network-care.py window`. It is RED when the `plants:network` window is
missing, when its pane is dead or is not running `tools/network_view.py`, when the
pane's live process no longer matches the view's recorded pid and start time, or
when the running copy of `tools/network_view.py` and `tools/network_boundary.py`
differs by content from the files on disk (it then prints the exact
`tmux respawn-pane` command to use). It is UNKNOWN when the tmux server cannot be
reached, the pane process cannot be read, or the view files cannot be read — a check
that cannot look is UNKNOWN, never a quiet pass. Comparing by content means
committing unchanged code is not a false RED.

Every other release-pinned view window is watched by the `outer-pane-code` duty
(`duties.json`, every 300 s, resolver `genome`), which runs
`checks/outer-pane-code.py`. A long-lived pane keeps the code it started from:
promoting the pin does not restart it. The check reads the pane's live cwd,
finds the release it started in, and compares that view's bytes with the script
at the current pin; RED names the window and the exact respawn command. An
unchanged promotion is not a false RED, and a pane restored from the working
tree is not release-pinned so it is not judged here. A missing or dead window
belongs to `view-window-coverage`. If tmux or the pane evidence is unreadable,
the check exits 3 and the producer logs the reason without refreshing the last
reading. That reading expires 900 seconds after the last successful poll, and
the expiry edge routes to Genome. To see the most recent poll outcome, run
`journalctl --user -u mishe-producer-outer-pane-code.service -n 12`.

**Rule: a view has a window.** Every `tools/*_view.py` renderer has a row in this table, an entry in the
site window registry (`restore: true`) and a live window in the `plants` session running it. A view
without a window is unfinished: the author adds the window and the row in the same change. The
`view-window-coverage` duty (`checks/view-window-coverage.py`, every 300 s, resolver steward) is RED for a view
with no registry entry or no live window, and UNKNOWN when tmux or the registry cannot be read. A report tool that only
prints once (such as `delivery_view.py`) runs in a looped window command, as `budget` does.

Create a window: `tmux new-window -t plants -d -n NAME 'env MISHE_TAUFTAUF_HOME=... python3 tools/X_view.py'`.
Never type into another plant's panes. Keep this table current when a window is added or retired.

## Restoring missing windows

`tools/tmux_restore.py` brings back outer and inner windows that are gone or whose panes died.
It reads the registry at `$MISHE_SEED_HOME/operator/window-registry.json` (site-local, not tracked):
each window names its session, its command and a `restore` flag.

The restorer uses `src/mishe_tauftauf/plant_tmux.py` for command-line arguments and
tmux pane-row decoding, and `src/mishe_tauftauf/state_files.py` for registry JSON.
Malformed pane rows or invalid JSON abort the run; failed-command stderr is printed
verbatim. These existing protocol boundaries are declared in `parsing-policy.json`;
the planner consumes decoded values.

- Dry run is the default. It prints the `DRY` actions it would take. `--apply` acts.
- Only entries with `"restore": true` and either a `command` or a `panes` list are touched. A
  window with a `panes` list gets each pane's own command (and `env`, falling back to the window's
  command). The first pane creates the window; each further pane is split in below it, so a
  multi-pane window comes back with its panes, not one. Entries marked
  `pending-decision`, `observed-only`, or `empty-shell` are skipped, and so are windows
  the registry does not list.
- A missing window is created; a dead pane (remain-on-exit) of a restorable window is respawned.
  It never types into a pane. It touches only registry entries with `restore: true`. In the
  registry as of 2026-11-13 those are the outer health, senses, senses-map, network and budget
  views and the inner chat, git and wakes tape views. No hold or guard window is registered, so
  the restorer cannot reach one. The `wakes` tape view is restorable, so the restorer can
  create or respawn it.
- A run with nothing to do prints `nothing to restore`. A run right after a successful apply
  should act on nothing, unless a restorable pane has died again.

The `tmux-restorer` duty is in place (every 300 s, `max_age` 900 s, resolver steward). It runs
`tools/tmux_restore.py --apply`, so a missing or dead restorable window comes back without a hand. It
exits 2 (RED) when a restore command failed and 3 (UNKNOWN) when the registry or its sessions cannot be
read. Still open:

- Many inner `mind-view` windows are `pending-decision`; they are not restored until someone
  classifies them as per-duty or drops them.

## A decisions window (proposed, not built)

A window that shows what the plant decided and why, rather than what it observed. It was
asked for on 2026-11-11. There is no renderer for it, no entry for it in the window
registry and no duty behind it, so nothing about it runs today; until one does, a reader
must take the decision records — the routing ledger, the sense labels, the dispositions —
as the evidence, and treat the absence of a decision view as UNKNOWN rather than as
"nothing was decided".

Two things have to be true before such a window means anything. It has to cover the
decisions the plant actually made, which the existing routing records do not: they capture
which candidate was selected, not the reasoning behind it. And it has to say what it does
not cover, so a gap in the record reads as a gap instead of as silence.

## Crash drill (not implemented)

A periodic crash-only drill (stop or crash the plant, confirm nothing continues on its own,
then confirm recovery resumes, including the tmux windows coming back) was proposed on
2026-10-31. As of 2026-11-11 there is no drill code in the repository and no timer for one.
Until a drill runs through the real detectors and recovery path, nothing shows that crash
recovery works end to end. The restorer runs on its 300 s duty, but that only restores windows;
it does not stop the plant and check that nothing continues on its own, so it is not a drill.
