---
field: repo-history-and-host-throttle
angle: recent
date: 2026-11-01
status: applied
owner: discover
target: src/mishe_tauftauf/slice_throttle.py
---

# Parent-slice throttle counters: what cpu.stat's throttled_usec covers

## What we read

- LKML patch "[PATCH v3] cgroup/cpu: document cpu.stat.local and clarify cpu.stat behavior" (the cgroup v2 CPU controller documentation change, posted in the 2026 cycle; merge status not confirmed here): https://lkml.iu.edu/2606.3/07487.html
- Read through the page's own text. Its key claims, quoted as the patch states them:
  - the three base usage stats (usage_usec, user_usec, system_usec) "include descendant cgroups";
  - "the five CFS bandwidth stats are non-hierarchical" and "they only account for throttling caused by the cgroup's own bandwidth limit";
  - for the new file, throttled_usec is "the actual throttling time incurred by the cgroup's own runqueues," which "may include throttling inherited from ancestor cgroup bandwidth limits". The patch contrasts this with cpu.stat, whose throttled_usec covers only the cgroup's own limit.
- Limit of the reading: the patch names the five CFS stats as a group but names only nr_bursts and burst_usec in the visible diff. That nr_periods, nr_throttled and throttled_usec belong to the non-hierarchical group is implied, not stated. The reading is a single patch under review, not the merged kernel documentation. The kernel tree's cgroup-v2.rst was not read here, and the Linux kernel documentation is the primary text that must confirm this before it is relied on.
- Local primary text: src/mishe_tauftauf/cpu_stat_text.py (COUNTERS = nr_periods, nr_throttled, throttled_usec) and src/mishe_tauftauf/slice_throttle.py (module docstring: "Per-service cpu.stat cannot see a quota enforced on the parent slice, so this reads the slices that carry the quota directly").

## What this plant already does

The slice-throttle sense already embodies the non-hierarchical reading, without naming it. It reads the two parent slices that carry the quota (mishe-plants.slice, mishe-lifeline.slice) and not the per-service cgroups, because a child's cpu.stat does not see a quota set on its parent. Its verdicts are per-minute deltas with an epoch check and a gap window, and an unreadable or unbaselined slice is UNKNOWN. The docstring gives the reason for reading parents; the counters' scope is not stated.

## What is new here

The docstring gives the reason for reading parents but not the scope of the numbers. If the patch's reading holds, the sample for a slice means "this slice's own limit throttled these runqueues" and says nothing about throttling caused by an ancestor's limit. The sensor id `sense.cgroup.<slice>.throttle` and the sample text read as though they report all throttling the slice experienced. A reader of that sample could therefore take a quiet parent slice as "not throttled", when the throttle came from above it. Writing the scope into the reading makes the sample honest about what it can and cannot see. This is a claim about the kernel's documented semantics. The plant's own code only encodes the parent-slice choice, so the scope note is a documentation change until the kernel text confirms it.

## Application

target: src/mishe_tauftauf/slice_throttle.py

Proposal for the discover owner: extend the module docstring of read_slice_throttle's module with one paragraph stating the counter scope: the three counters read from a slice's cpu.stat are that slice's own CFS bandwidth limit, not ancestors' limits; a quiet reading therefore does not rule out throttling from above. Add no behaviour change. Verify with a test in tests/test_slice_throttle.py that a fixture with a non-zero throttled counter on the slice itself yields a verified reading, so the docstring is pinned to the code path it describes. Before merging the paragraph, read the merged cgroup-v2.rst in the running kernel's tree and cite its text; if the patch is not merged or its wording differs, revise or drop the paragraph. Detector: the existing test module; interval: each commit touching slice_throttle.py.
