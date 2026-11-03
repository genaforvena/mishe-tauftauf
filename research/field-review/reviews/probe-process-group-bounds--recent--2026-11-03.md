---
field: probe-process-group-bounds
angle: recent
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Linux man-pages, `PR_SET_PDEATHSIG(2const)`, current man-pages 6.19 text: https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html

## What this plant already does

`src/mishe_tauftauf/probe_supervisor.py` uses `PR_SET_PDEATHSIG`; the landed 2026-11-01 recent review records that the signal is tied to the creating thread and already proposes an inherited lifeline pipe as a process-scoped alternative.

## What is new here

No distinct finding: the current primary-source text confirms the same thread-versus-process limitation and existing proposed lifeline-pipe application.

## Discard

The recent-source revisit adds no distinct test-backed application beyond the already-landed proposal in the 2026-11-01 review.
