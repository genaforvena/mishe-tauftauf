---
field: probe-process-group-bounds
angle: misread
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Linux man-pages 6.7, `prctl(2)`, 2024-03-03: https://man7.org/linux/man-pages/man2/prctl.2.html. Its general description says prctl operations affect the calling thread or process; the `PR_SET_PDEATHSIG` text details the parent-death signal's thread-scoped behavior. The same distinction and its implications for this supervisor were already read and documented in the landed 2026-11-01 review pair.

## What this plant already does

The existing 2026-11-01 reviews already distinguish thread-scoped `PR_SET_PDEATHSIG` from process lifetime and propose an inherited lifeline pipe; they also record the remaining supervisor-death caveat. This cell's current foundational-source reread adds no separate mechanism.

## What is new here

Nothing distinct: the thread-versus-process scope is already represented in the landed review and proposed design.

## Discard

Discarded because this repeat reading adds no distinct, test-backed application beyond the existing landed reviews.
