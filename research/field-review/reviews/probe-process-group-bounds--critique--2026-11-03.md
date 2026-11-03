---
field: probe-process-group-bounds
angle: critique
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Linux man-pages 6.19, `PR_SET_PDEATHSIG(2const)`, retrieved 2026-11-03: https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html. Its caveat says the parent is the creating thread, and the signal arrives when that thread terminates, not when all parent-process threads terminate; the setting is also cleared on fork and specified credential changes.

## What this plant already does

`src/mishe_tauftauf/probe_supervisor.py` sets `PR_SET_PDEATHSIG` to SIGTERM, routes cancellation through cleanup that kills the probe process group, and rechecks the parent PID. The already-landed 2026-11-01 critique review records this thread-scope limitation and proposes the inherited-pipe alternative.

## What is new here

No distinct mechanism or consequence beyond the existing critique review: the thread-scoped guarantee and its proposed descriptor-based alternative are already documented there.

## Discard

Discarded: this source adds no distinct critique or application beyond the already-landed review for this cell.
