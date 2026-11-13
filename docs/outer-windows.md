# Outer windows: registry

Read-only tmux windows in the outer `plants` session. Each is a `tools/*_view.py` renderer,
except `budget`, whose renderer lives in the site (`SITE_HOME/operator/budget/`, gitignored, not in git):
plain words first (state, why it matters, next action), numbers second, UNKNOWN when not measured.
Each must stay dependency-free so Mishe still works with the window closed, and must fit the
pane it draws in: 41 rows, and 170 columns for the health view, which is the widest.

| window | tool | owner | purpose |
| --- | --- | --- | --- |
| health | tools/health_view.py | steward | verdict of the tending pass (guard, units, wakes, signals, delivery) |
| senses | tools/household_view.py | senses | the apartment map and what Mishe senses |
| senses-map | tools/map_view.py | senses | best guess per person from the apartment map, read-only (needs tools/map_render.py run first) |
| network | tools/network_view.py | network | home network and connection |
| budget | operator/budget/budget_view.py (site-local) | budget | provider quota and spend |

The `budget` window runs `operator/budget/budget_pane.sh` (site-local, under `SITE_HOME`), a loop that calls
`budget_view.py` every 20 s and clears the screen between frames, so the registry's
command and the table's tool name the same view.

The `senses-map` window has a renderer behind it: the `map-render` duty in
`duties.json` runs `tools/map_render.py` every 180 s as the
`mishe-producer-map-render.timer` producer unit, and the `map-render` signal
carries the verdict (resolver: senses). Producer units are rendered from the
served release's own `duties.json`, so the timer appears when a release carrying
the duty is promoted — it was not present under release `35748377`. The viewer
flags a render older than 30 minutes as stale, so a fresh render keeps the pane
from reading STALE.

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
- Only entries with `"restore": true` and a command are touched. Entries marked
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

What is not in place yet (UNKNOWN until it is):

- No timer runs the restorer. It is run by hand.
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
recovery works end to end. The restorer above is a manual tool, not a drill.
