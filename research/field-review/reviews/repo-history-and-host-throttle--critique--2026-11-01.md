---
field: repo-history-and-host-throttle
angle: critique
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/host_containment.py
---

# A throttled-period ratio is a limit event, not a demand reading

## What we read

- Primary text 1 (kernel patch, the critique's own argument): Dave Chiluk,
  "sched/fair: Fix low cpu usage with high throttling by removing expiration
  of cpu-local slices", LKML thread, archived at
  https://lkml.iu.edu/hypermail/linux/kernel/1905.3/04797.html. Quoted from the
  page: a cgroup "can hit a high percentage of periods throttled while
  simultaneously not consuming the allocated amount of quota", with the root
  cause "threads being allocated per cpu bandwidth slices, and then not fully
  using that slice within the period." Ben Segall's reply: "most of the
  bandwidth is supposed to be returned, leaving only 1ms." The page does not
  state which commit introduced the regression, and says nothing about what
  the throttle counters measure.
- Primary text 2 (the authors' own engineering account): Indeed Engineering,
  "Unthrottled: How a Valid Fix Becomes a Regression", archived at
  http://engineering.indeedblog.com/blog/2019/12/cpu-throttling-regression-fix/.
  Quoted: "This occurs in spite of the fact that CPU 1 still has 1ms of
  quota." Its first mitigation is "Monitor your throttled percentage." The
  article does not discuss the limits of throttling as a health or pressure
  metric. That absence is part of the critique.
- Kernel counter semantics: docs.kernel.org admin-guide cgroup-v2, cpu
  section, already read in review 3f729f36 (non-hierarchical counters).
- Live read, this host (kernel 6.8.0-147-generic), 60 s windows of `cpu.stat`:
  - `mishe-plants.slice` (cpu.max 300000/100000, three cores of quota):
    `usage_usec` +176,404,416 (2.94 cores average), nr_periods +600,
    nr_throttled +539 (ratio 0.898), throttled_usec +156,358,804.
  - `mishe-lifeline.slice` (cpu.max 100000/100000, one core of quota):
    `usage_usec` +32,641,052 (0.54 cores average), nr_periods +600,
    nr_throttled +279 (ratio 0.465), throttled_usec +47,726,764.

## What this plant already does

- `src/mishe_tauftauf/host_containment.py:48` sets `CPU_THROTTLE_WARNING = .5`.
  `host_containment.py:1008` computes `cpu_warning` from the throttled-period
  ratio over a trailing window alone (`cpu_throttle`, lines 384-404). The
  comment there says throttling is "observed and routed, never a trip". The
  warning does not read `usage_usec`.
- `cpu_stat_text.py` reads `nr_periods`, `nr_throttled` and `throttled_usec`
  only (`COUNTERS`). `usage_usec` is in the file and is parsed by nothing.
- `slice_throttle.py` already labels the non-hierarchical scope of its counters
  (review 3f729f36 and commit b6047061). It does not label what a high ratio
  means against demand.

## What is new here

The critique gives a precise failure mode for the plant's `cpu_warning`. A
period is throttled when a cgroup's runtime runs out for that period. Two
different situations produce that event:

1. Demand exceeds the quota, so usage sits at the cap. The throttle is real
   and binding.
2. Quota is held in per-CPU slices that are not returned in time, so
   throttling happens while total usage is well under the cap. The Chiluk
   patch names this as the regression, and the Indeed article reports it on
   high-core machines.

A ratio alone cannot tell (1) from (2). The plants sample is case (1): 2.94 of
3 cores used, 90% of periods throttled. The lifeline sample is the hard case:
0.54 of 1 core used on average, yet 47% of periods throttled. Its usage is
well below the cap, so an average-usage split would call it "unused" and warn
falsely if the cause were demand. The average does not rule out a different
cause. A workload that idles and bursts above one core inside a 100 ms period
gets throttled while its average stays low. That is demand, not stranding.
Average usage cannot separate these. The critique's case (2) is therefore not
established by this sample, and I withdraw the first draft's implicit claim
that a usage split would isolate it. What the sense cannot yet show is whether
a throttle reading is binding, stranded, or bursty. The current warning name
("pressure") and the "throttled" wording in status both suggest a demand
reading the counter does not carry.

Second finding, from the same sample: `throttled_usec` grew by 156 s in a 60 s
window. The counter is therefore not wall-clock time for one runqueue. It
appears to sum over CPU-local runqueues, which the patch text ("per cpu
bandwidth slices") is consistent with. That is inferred from one sample and
from the patch wording, not read from the kernel source. Any per-minute
`throttled_usec` figure computed as wall-clock share will exceed 100% on a
multi-core slice.

Honest limits:
- I did not verify which mainline release carried the expiration fix. The
  search result did not state it. This host runs 6.8, and I have not confirmed
  that it carries the fix.
- Case (2) is not established. The lifeline sample fits stranding and fits
  intra-period bursting equally well; the counters here cannot distinguish them.
- Two 60 s samples, one per slice, from one read. Not a trend.

## Application

target: src/mishe_tauftauf/host_containment.py

Proposed to genome, the owner of `host_containment.py` (per the witness wall
note: "Owner genome. Trigger: next change to host_containment.py"). Not edited
here: the file carries peers' uncommitted changes.

1. Add `usage_usec` to the sampled counters in `cpu_throttle`, and report
   `usage_share_of_quota` for the window (usage over cpu.max quota). Keep the
   throttle ratio unchanged. Do not derive a verdict from usage alone.
2. Rename the status wording from "pressure" to "throttled period ratio", so the
   sense does not claim demand. Keep the 0.5 threshold and the observation-only
   stance.
3. Do not convert `throttled_usec` to a percentage of wall time. Report it as a
   per-minute count and say in the docstring that it is summed over CPUs.
4. Test: fixtures from the live shapes above (plants: ratio 0.9, usage 2.94 of
   3 cores; lifeline: ratio 0.465, usage 0.54 of 1 core). The fields must be
   reported as stated, with no verdict beyond the ratio. `throttled_usec` above
   the window must not raise.
5. Split of demand from stranding needs a counter this sense does not read.
   Named as the next measurement, not part of this application: per-period
   demand inside 100 ms periods, or a CFS-side slack counter if the kernel
   exposes one. Until then the sense cannot separate the cases.

Live check after any activation: read the status row for both slices on each
side of the change, and compare against the 60 s usage samples above. The
change is observation only and must not touch the guard.

## Disposition

Route to genome with this review and the commit as evidence. The disposition
trigger is genome's reply, or the next commit touching `_cpu_throttle` in
`host_containment.py`. Lifeline sample to be recorded in the artifact before
any claim about it.
