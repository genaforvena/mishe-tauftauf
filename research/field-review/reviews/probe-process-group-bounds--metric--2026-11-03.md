---
field: probe-process-group-bounds
angle: metric
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Linux kernel documentation, “The /proc Filesystem,” current documentation: https://docs.kernel.org/_sources/filesystems/proc.rst.txt

The primary source defines `/proc/<pid>/status` `PPid` as the parent process ID and explicitly warns that a `/proc/<pid>` descriptor does not prevent PID reuse after exit. This provides process-lineage observations but not a stable descendant census once ancestry changes.

## What this plant already does

The 2026-11-01 metric review already proposed a `/proc` PPid-chain census for `src/mishe_tauftauf/probe_supervisor.py`, and records the limits after orphan reparenting and sampling. The deterministic selector confirms this cell is an exhausted-grid revisit, not a new uncovered angle.

## What is new here

No new metric or distinct measurement is supported: the source's PPid field is the same lineage signal already evaluated in the landed metric review, and the PID-reuse warning does not establish a more reliable count.

## Discard

Discarded: this revisit duplicates the existing PPid-lineage metric proposal, with no distinct application to offer from this read-only role.
