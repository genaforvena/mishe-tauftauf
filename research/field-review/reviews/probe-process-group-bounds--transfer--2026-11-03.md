---
field: probe-process-group-bounds
angle: transfer
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Erlang/OTP System Documentation v29.1.2, “Processes,” sections “Monitors” and “Sending Exit Signals”: https://www.erlang.org/doc/system/ref_man_processes.html

A monitor is a one-way observation relation: termination yields a `DOWN` signal to the monitor, without propagating termination back to the observer. This is distinct from a link, whose exit signal can terminate a non-trapping linked process.

## What this plant already does

`src/mishe_tauftauf/probe_supervisor.py` independently observes the direct child's exit with `child.poll()` and performs process-group cleanup in a `finally` block. Child completion does not itself trigger a new supervisor termination path.

## What is new here

The Erlang monitor/link distinction clarifies that observing a child and propagating its failure are separate relations. The supervisor already separates those actions; the distinction adds no uncovered behavior or measurable application in this file.

## Discard

Discard: the monitor/link separation is already embodied by the supervisor's child polling and independent cleanup, leaving no distinct transfer to apply.
