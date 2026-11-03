---
field: host-capacity-anon-reading-and-memory-stat
angle: recent
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Primary source: Zhongkun He, Linux kernel mailing-list discussion, “Re: [External] Re: [PATCH V3 1/3] mm: add swappiness=max arg to memory.reclaim for only anon reclaim,” 2025-04-09, https://lkml.rescloud.iu.edu/2504.1/04308.html. The author proposes `swappiness=max` as an explicit mode for proactive reclaim of anonymous folios only, including under MGLRU; the reply says existing MGLRU logic already includes value 201 and the patch would make the intent clearer.

## What this plant already does

`host_capacity_reading.py` reports anon and file memory as separate Signals, and `memory_stat_reading.py` is a read-only shadow reader. Neither reader executes proactive reclaim; the recent proposal concerns an actuator and does not demonstrate a new passive-reading result.

## What is new here

The kernel discussion distinguishes measuring anonymous memory from commanding reclaim of anonymous folios. It supplies no result that changes what this plant can infer from its current read-only readers or their replay, so there is no evidence-based application for this role to land. The owning role must decide separately whether that actuator belongs in the controller.

## Discard

Discarded: the source concerns an anon-only reclaim actuator, not a recent result applicable to this role's read-only measurement; proposing controller behavior would exceed this role's read-only boundary.
