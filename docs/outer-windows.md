# Outer windows: registry

Read-only tmux windows in the outer `plants` session. Each is a `tools/*_view.py` renderer:
plain words first (state, why it matters, next action), numbers second, UNKNOWN when not measured.
Each must stay dependency-free so Mishe still works with the window closed, and must fit 172x41.

| window | tool | owner | purpose |
| --- | --- | --- | --- |
| health | tools/health_view.py | steward | verdict of the hourly tending pass (guard, units, wakes, signals, delivery) |
| senses | tools/household_view.py | senses | the apartment map and what Mishe senses |
| senses-map | tools/map_view.py | senses | best guess per person from the apartment map, read-only (needs senses/map/render_map.py run first) |
| network | tools/network_view.py | network | home network and connection |
| budget | operator/budget/budget_view.py | budget | provider quota and spend |

Create a window: `tmux new-window -t plants -d -n NAME 'env MISHE_TAUFTAUF_HOME=... python3 tools/X_view.py'`.
Never type into another plant's panes. Keep this table current when a window is added or retired.
