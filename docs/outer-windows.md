# Outer windows: registry

Read-only tmux windows in the outer `plants` session. Each is a `tools/*_view.py` renderer:
plain words first (state, why it matters, next action), numbers second, UNKNOWN when not measured.
Each must stay dependency-free so Mishe still works with the window closed, and must fit 172x41.

| window | tool | owner | purpose |
| --- | --- | --- | --- |
| health | tools/health_view.py | steward | verdict of the hourly tending pass (guard, units, wakes, signals, delivery) |
| senses | tools/household_view.py | senses | the apartment map and what Mishe senses |
| senses-map | tools/map_view.py | senses | best guess per person from the apartment map, read-only (needs tools/map_render.py run first) |
| network | tools/network_view.py | network | home network and connection |
| budget | operator/budget/budget_view.py | budget | provider quota and spend |

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
  It never types into a pane and never touches wake, hold or guard windows.
- A run with nothing to do prints `nothing to restore`. A run right after a successful apply
  should act on nothing, unless a restorable pane has died again.

What is not in place yet (UNKNOWN until it is):

- No timer runs the restorer. It is run by hand.
- `senses-map` is marked `restore: true` and its viewer now loops every 30 s instead of
  exiting after one render, so a periodic restore would not flap it. The renderer
  behind it has a schedule in the committed tree: the `map-render` duty in
  `duties.json` runs `tools/map_render.py` every 180 s, and the generated unit
  `mishe-producer-map-render.timer` fires that service. Producer units are rendered
  from the served release's own `duties.json`, so the timer starts when a release
  carrying the duty is promoted. Observed 2026-11-11 at 18:11Z, the served release
  (`35748377`) predates the duty, no such unit is installed, and the pane reads
  "rendered 16:26Z, 105 min ago: STALE" — the viewer flags a render older than
  30 minutes as stale.
- Many inner `mind-view` windows are `pending-decision`; they are not restored until someone
  classifies them as per-duty or drops them.
- The registry's `budget` window runs `operator/budget/budget_pane.sh`, while the table above
  points at `budget_view.py`. Those are the same view, not two: the pane script is a loop
  that runs `budget_view.py` every 20 s and clears the screen between frames. Observed
  2026-11-11 at 18:31Z, the live pane's text is that view's output, timestamp apart.

## Crash drill (not implemented)

A periodic crash-only drill (stop or crash the plant, confirm nothing continues on its own,
then confirm recovery resumes, including the tmux windows coming back) was proposed on
2026-10-31. As of 2026-11-11 there is no drill code in the repository and no timer for one.
Until a drill runs through the real detectors and recovery path, nothing shows that crash
recovery works end to end. The restorer above is a manual tool, not a drill.
