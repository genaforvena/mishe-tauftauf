---
field: recovery-observations-and-sessions
angle: recent
date: 2026-10-31
status: discarded
owner: field-review
---

## What we read

Searched for a 2023–2026 primary source on crash-recovery session detection and recovery boundaries (one standard and one extended web search, 2026-10-31).

- Read the abstract of arXiv 1811.05007, "You Only Live Multiple Times: A Blackbox Solution for Reusing Crash-Stop Algorithms In Realistic Crash-Recovery Settings", Kozhaya, Maric, Pignolet, OPODIS 2018 — <https://arxiv.org/abs/1811.05007>. The abstract states that "nodes as well as communication links both crash and recover" and proposes a transformation that lets crash-stop algorithms run in that model. The fetched page does not describe the transformation's mechanism (no incarnation numbers, epochs or stale-message handling are visible). The full text was not read.
- Listed, not read: arXiv 2311.11851 "Crash-Stop Failures in Asynchronous Multiparty Session Types" (crash-stop only, so out of scope for a crash-recovery boundary) — <https://arxiv.org/abs/2311.11851>.

The extended search found no 2023–2026 paper that combines a crash-recovery failure detector with session liveness or a recovery boundary. Those results were not read beyond their titles and abstracts.

## What this plant already does

`src/mishe_tauftauf/session_restarts.py` assesses restart cadence from caller-supplied epoch timestamps and journal messages; the comment at line 36 says a rebirth "killed the prior incarnation", which is an informal use of the word. The `session_*` and `activity` modules carry no incarnation number, lease epoch or generation token (grep of `session_watch.py`, `session_receipts.py`, `session_restarts.py` and `activity.py` for incarnation, epoch and generation finds only the restart-cadence code). The doctrine already states that tmux and its lease establish presentation liveness only, and that missing or stale evidence stays UNKNOWN or RED.

## What is new here

Nothing that changes the plant. The one readable source is a 2018 abstract whose mechanism is not visible in what was fetched. A crash-recovery incarnation token is a candidate idea, but this review cannot ground it in a source it read, and the 2023–2026 window is not met.

## Discard

No readable 2023–2026 primary source on crash-recovery session detection was reachable; the 2018 abstract gives no transferable mechanism, so this cell is discarded for that reason.
