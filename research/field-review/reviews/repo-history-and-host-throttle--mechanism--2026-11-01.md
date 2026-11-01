---
field: repo-history-and-host-throttle
angle: mechanism
date: 2026-11-01
status: applied
owner: discover
target: src/mishe_tauftauf/slice_throttle.py
---

# Read cpu.stat.local beside cpu.stat: the inherited share is a counter, not a caveat

## What we read

- Primary text: Linux kernel admin guide, "Control Group v2", cpu section,
  https://docs.kernel.org/admin-guide/cgroup-v2.html (read 2026-11-01). Quoted
  as the page states it:
  - `cpu.stat` "always reported" fields are `usage_usec`, `user_usec`,
    `system_usec`; the CFS bandwidth fields `nr_periods`, `nr_throttled`,
    `throttled_usec`, `nr_bursts`, `burst_usec` are reported "only when the
    controller is enabled", and "the above five CFS bandwidth stats are
    non-hierarchical. They only account for throttling caused by this cgroup's
    own bandwidth limit, not including throttling inherited from ancestor
    cgroups."
  - `cpu.stat.local` "reports throttled_usec only", and covers "the actual
    throttling time incurred by this cgroup's own runqueues", which "can
    include throttling inherited from ancestors."
- The page does not give a formula for splitting `cpu.stat.local` into own and
  inherited parts. It says the local counter "can include" inherited time. The
  difference `local - own` is therefore our reading of the doc's two
  definitions, not a quantity the kernel names.
- Not read here: the 2026 LKML patch that documents `cpu.stat.local` (already
  cited by the recent review, merge status unconfirmed), and the rest of the
  cgroup-v2 page past character 100000.

## What this plant already does

`src/mishe_tauftauf/slice_throttle.py` reads `cpu.stat` for
`mishe-plants.slice` and `mishe-lifeline.slice`, takes per-minute deltas of
`nr_periods`, `nr_throttled` and `throttled_usec`, and its module docstring
states the scope limit from the same kernel page: the counters are
non-hierarchical, so "a quiet reading therefore does not rule out throttling
from above the slice." The sense is observation only and never changes a
quota. The module has no reader for `cpu.stat.local`.

## What is new here

The docstring names a blind spot and leaves it as a caveat. The kernel already
exports the counter that measures the blind spot, on the same slice directory,
and this host's kernel (6.8.0-147-generic) has the file.

Live read-only samples on this host, 2026-11-01, taken 60 seconds apart,
`throttled_usec` in microseconds:

| slice | local (t0) | own (t0) | local − own (t0) | local − own (t0+60s) | own delta in 60s | nr_throttled delta in 60s |
| --- | --- | --- | --- | --- | --- | --- |
| mishe-plants.slice | 442372580350 | 442252701118 | 119879232 | 119909796 | 140702415 | 499 |
| mishe-lifeline.slice | 118859300809 | 118853895670 | 5405139 | 5422509 | 5421647 | 75 |

Two readings follow from the table, with their limits:

- Cumulatively, about 2.7% of plants' local throttle time (119.9 s of 442 s)
  is not in the slice's own counter. That is the inherited share the doc
  describes. It accumulated before these samples and is not a per-minute rate.
- In the 60-second window, the plants' inherited delta was about 30 ms against
  an own delta of 140.7 s of throttle time. The lifeline inherited delta was
  about 17 ms against 5.4 s of own throttle. One minute on one host is a single
  observation. It is not a rate or a trend.
- The own delta of 140.7 s in 60 s of wall time means `throttled_usec` is a sum
  over runqueues, not wall time. The page does not say so in the cpu section we
  read; that is our inference from the numbers, and the owner should confirm it
  before using the field as a fraction of wall time.

The plant's current reading would report `throttled_usec` and `ratio` as its
whole throttle picture. Adding `cpu.stat.local` would make the inherited
component an explicit verified number instead of an unmeasured gap. It would
also let a future reader distinguish "throttled by this slice's quota" from
"throttled by an ancestor's quota", which the docstring can only assert.

## Application

target: src/mishe_tauftauf/slice_throttle.py

Proposal to the owner of `src/mishe_tauftauf/slice_throttle.py` (discover):

- Read `throttled_usec` from `cpu.stat.local` in `read_slice_throttle`, next to
  the existing `cpu.stat` read. Persist it in the same baseline, under the same
  counter-epoch and gap rules.
- In `throttle_verdict`, emit `throttled_local_usec` and
  `inherited_usec = local − own` as per-minute deltas. Mark the inherited field
  UNKNOWN when `cpu.stat.local` is absent or unparseable. Do not invent a zero.
- Keep the docstring's scope note, and replace "does not rule out" with a
  reference to the measured inherited field.

Prediction: on this host the inherited share of throttle time stays at or
below the cumulative 2.7% (plants) and the 60-second samples above (0.02% and
0.3%). The keep/revise/revert decision is the observed value over a few
hours, against those numbers. If the inherited share is large
or grows, the parent-slice cap is a finding in its own right and goes to
genome as a host_containment question, not to this sense.

Checks the owner should run before activation: a unit test for a missing
`cpu.stat.local` (UNKNOWN, not zero); a unit test where local equals own
(inherited zero); and one live read on this host with the values above as the
reference. Activation follows the repository's snapshot procedure.
