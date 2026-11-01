---
field: repo-history-and-host-throttle
angle: metric
date: 2026-11-01
status: applied
owner: discover
target: src/mishe_tauftauf/slice_throttle.py
---

# The 0.5 throttle warning is a count per period, not a share of time: add the time-equivalent beside it

## What we read

- Primary text: The Linux kernel documentation, "CFS Bandwidth Control",
  https://docs.kernel.org/scheduler/sched-bwc.html (read 2026-11-01 through a
  fetch of that page). Quoted as the page states it:
  - `nr_periods`: "Number of enforcement intervals that have elapsed."
  - `nr_throttled`: "Number of times the group has been throttled/limited."
  - `throttled_time`: "The total time duration (in nanoseconds) for which
    entities of the group have been throttled."
  - The page gives the default period as `cpu.cfs_period_us=100ms`. It does not
    say whether `throttled_time` is summed across CPUs, and it does not define
    `nr_throttled / nr_periods` as a fraction. The normalisation is ours.
- Live read-only sample, this host, 60 seconds apart, both slices' `cpu.stat`
  and `cpu.max`. Full values in the ignored artifact
  `artifacts/field-review/throttle-metric-2026-11-01.txt`. Summary (computed
  from the deltas):

  | slice | quota (cpu.max) | Δ nr_periods | Δ nr_throttled | count ratio | Δ throttled_usec | time-equivalent CPUs |
  | --- | --- | --- | --- | --- | --- | --- |
  | mishe-plants.slice | 300000/100000 (3 CPU) | 600 | 285 | 0.475 | 153,861,602 | 2.564 |
  | mishe-lifeline.slice | 100000/100000 (1 CPU) | 600 | 253 | 0.422 | 6,199,011 | 0.103 |

  `time-equivalent CPUs` = Δthrottled_usec / (Δnr_periods × period_us). It is
  the mean number of throttled runqueues per enforcement period, if the counter
  sums over runqueues. That summing is our inference from the numbers (the
  plants value exceeds 1), not something the page states.

## What this plant already does

- `src/mishe_tauftauf/host_containment.py:385-400` (`cpu_throttle`) computes
  `ratio = throttled / periods` from `nr_throttled` and `nr_periods`. Line 48
  sets `CPU_THROTTLE_WARNING = .5`, and line 1008 raises `cpu_warning` when the
  ratio reaches it. The docstring says "throttled share of scheduler periods".
- `src/mishe_tauftauf/slice_throttle.py:72` reports the same ratio per slice as
  `round(deltas["nr_throttled"] / periods, 4)`. The module already reads
  `throttled_usec` for the same slice, so the time figure needs no new read and
  no new file.
- The existing critique (2edc12f2) argues the ratio counts a limit event, not
  demand. This review takes that as given and asks about the unit.

## What is new here

The plant's warning metric has a unit problem that the critique did not name.
`nr_throttled` is a count of throttle events. The kernel page does not bound
it by `nr_periods`, and the plant divides it by `nr_periods` and calls the
result a "share of periods". The live numbers show why this matters:

- The lifeline slice has a count ratio of 0.42, just under the 0.5 warning,
  while its throttle time is 0.103 CPU-equivalent on a 1-CPU quota. The count
  says "throttled in about every other period". The time says "about a tenth of
  one CPU was held back". Both are true of different things.
- The plants slice has a count ratio of 0.475, also under 0.5, while its time
  is 2.56 CPU-equivalent against a 3-CPU quota. A reader who takes 0.475 as
  "throttled in under half the periods" misses that the slice held back most of
  its quota.
- Because the count is per runqueue event, a count ratio above 1 is possible on
  a 16-CPU host in principle. We did not observe one; the claim is only that the
  page does not rule it out.

The time-equivalent is a dimensionless quantity in CPU units. It is directly
comparable to the quota (`cpu.max` quota / period), and it can be computed from
counters the sense already reads. It does not replace the ratio; it gives the
warning a unit it can be read against.

## Application

target: src/mishe_tauftauf/slice_throttle.py

Proposal to the owner of `src/mishe_tauftauf/slice_throttle.py` (discover). The
working-tree copy of that file has uncommitted changes from another channel, so
discover should coordinate before editing:

- In `throttle_verdict`, add `throttled_cpus = Δthrottled_usec / (Δnr_periods × period_us)`
  beside the existing `ratio`, with the period taken from the second field of
  the slice's `cpu.max`. Mark it UNKNOWN, not zero, when `cpu.max` is absent or
  unparseable, or when the period delta is zero.
- Do not change the existing `ratio` or the warning threshold in this change.
  The count and time readings are reported side by side so the next reviewer
  can see both.

Prediction: on this host the time-equivalent for plants stays between about 1
and 3 CPUs while the count ratio stays under 0.5, so the two readings disagree
in direction for that slice. Observation: a unit test for `cpu.max` absent
(UNKNOWN), for zero period delta (UNKNOWN), and a fixture reproducing the
600-period, 153,861,602 µs row above (2.564 CPUs). Keep if the live value
matches the fixture within rounding over one hour of samples. Revise the
definition if the page or a kernel source later shows `throttled_time` is
per-group, not per-runqueue, which would change the unit to "CPU-seconds of
throttle per period" with no per-runqueue reading. Revert if the field is
unstable across the 60-second samples. Activation follows the repository's
snapshot procedure.

Limits: one minute of two slices on one host is a sample, not a trend. The
summing-over-runqueues reading is an inference from the numbers and needs the
kernel source or a controlled test to confirm.
