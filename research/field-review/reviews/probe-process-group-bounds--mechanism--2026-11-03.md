---
field: probe-process-group-bounds
angle: mechanism
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Linux kernel documentation, “Control Group v2,” current documentation (7.4.0-rc1), authoritative design and user-visible interface description: https://docs.kernel.org/admin-guide/cgroup-v2.html

The cgroup v2 interface describes a tree of process groups; the cgroup.kill mechanism can kill the target tree including descendants, with concurrency and process migration handled by the kernel. Threaded cgroups reject this operation with EOPNOTSUPP.

## What this plant already does

`src/mishe_tauftauf/probe_supervisor.py` starts each probe in a separate session, enforces a deadline, and kills the probe process group in cleanup. The landed mechanism review from 2026-11-01 already measured a `setsid` escape and proposed a delegated child-cgroup reaper, while explicitly leaving actual recovery-probe escape and production cgroup delegation unverified.

## What is new here

The current kernel specification confirms the tree-kill semantics and boundaries already captured by that review; it supplies no new evidence that production probes escape their group or that their service has a delegated child cgroup. Repeating the same proposal would not establish a new change.

## Discard

No new application: the only concrete mechanism repeats the unresolved cgroup-reaper proposal already routed to Health, and the necessary production-boundary evidence remains absent.
