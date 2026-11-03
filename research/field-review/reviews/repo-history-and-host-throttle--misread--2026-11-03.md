---
field: repo-history-and-host-throttle
angle: misread
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/host_containment.py
---

# Throttled periods are counted events, not a time share — and they are the slice's own limit only

## What we read

- Primary text 1: Linux kernel admin guide, "Control Group v2", cpu section, https://docs.kernel.org/admin-guide/cgroup-v2.html — fetched 2026-11-03 as /tmp/cgroup-v2-20261103.html 175364 B sha256 prefix 9c7ea082. Quoted as the page states it:
  - "`cpu.stat` A read-only flat-keyed file. This file exists whether the controller is enabled or not. It always reports the following three stats, which account for all the processes in the cgroup (including those in descendant cgroups): `usage_usec` `user_usec` `system_usec` and the following five when the controller is enabled, which account for only the processes under the fair-class scheduler: `nr_periods` `nr_throttled` `throttled_usec` `nr_bursts` `burst_usec`"
  - "Note that the above five CFS bandwidth stats are non-hierarchical; they only account for throttling caused by this cgroup's own bandwidth limit, not including throttling inherited from ancestor cgroups."
  - "`cpu.stat.local` A read-only flat-keyed file. … It reports the following stat when the controller is enabled: `throttled_usec` Unlike the `throttled_usec` reported by `cpu.stat` which accounts for throttling caused by this cgroup's own CFS bandwidth limit, `cpu.stat.local` reports the actual throttling time incurred by this cgroup's own runqueues, which may include throttling inherited from ancestor cgroup bandwidth limits."
- Primary text 2: Linux kernel, "CFS Bandwidth Control", https://docs.kernel.org/scheduler/sched-bwc.html — fetched 2026-11-03 as /tmp/sched-bwc-20261103.html 23172 B sha256 prefix 44f906c7. Quoted as the page states it:
  - "`cpu.stat`: `nr_periods`: Number of enforcement intervals that have elapsed. `nr_throttled`: Number of times the group has been throttled/limited. `throttled_time`: The total time duration (in nanoseconds) for which entities of the group have been throttled." (cgroup v1 name `throttled_time`; cgroup v2 exposes the same counter as `throttled_usec` in microseconds)
- Local primary text (the plant's own, read side by side):
  - `src/mishe_tauftauf/host_containment.py:42-48` `CPU_THROTTLE_WARNING = .5` comment "Throttled share of scheduler periods that raises a warning. An observation threshold for routing, not a quota" and `cpu_throttle` docstring (l.385) "Throttled share of scheduler periods over the trailing window. samples are (monotonic, nr_periods, nr_throttled) … ratio=round(throttled / periods, 4)".
  - `src/mishe_tauftauf/slice_throttle.py:11-22` module docstring — already states the non-hierarchical scope verbatim and the `cpu.stat.local` Inherited inference, with fix commits 7bbb28f5 (read cpu.stat.local) and afd5ab5a (throttled_cpus time-equivalent) and b6047061 (scope note).
  - `src/mishe_tauftauf/discovery.py:3757-3821` counting rule paragraph added in 0a5a1d60 — already correct that dropped-commits counts HEAD-reflog shas not reachable, not resets, with 30-day unreachable retention bound. Not re-argued here.
- Not re-read in this pass: LKML Chiluk/PSI pages for demand vs stranding (prior critique/transfer); SWH altered-histories scale (prior recent 97fd48da). This review isolates the counter definition and scope wording.

## What this plant already does

- `slice_throttle.py` already names the scope limit in its module docstring and reports both `ratio = nr_throttled / nr_periods` and `throttled_cpus = throttled_usec / (nr_periods × period_us)` beside it, plus `inherited = local - own` from `cpu.stat.local` with UNKNOWN when absent. The per-slice throttle sense is thus labelled as own-limit events with time beside it.
- `host_containment.py` does not. Its `cpu_throttle` computes the same quotient from the plants slice's `cpu.stat` alone, calls the result a "share" and raises `cpu_warning` when `ratio >= .5`, with no docstring note that the five stats are non-hierarchical, no mention that `nr_throttled` counts events (not time), and no reading of `cpu.stat.local`. The warning name `cpu_throttle_warning` / `cpu_warning` and the comment "Throttled share" suggest a pressure reading the counter does not carry by itself.
- Live read, this host (kernel 6.8.0-147-generic), `mishe-plants.slice` `cpu.max 300000/100000` (3 CPU quota, period 100000 us): a 60 s window can show `throttled_usec` delta >60 s (e.g. 140 s in 60 s wall time, review 7bbb28f5), which is consistent with summed-over-runqueues accounting, not wall time. `ratio` alone cannot express that; the kernel defines `throttled_time`/`throttled_usec` as the time metric and `nr_throttled` as the event count.
- The 2026-11-01 misread review (dropped = reflog fact not reset fact) fixed discovery's docstring; the metric/mechanism reviews fixed slice_throttle. This host-containment wording was left as is — a loose application of the same kernel definitions to a second module.

## What is new here

1. **Event count vs time share.** `sched-bwc` defines `nr_throttled` as "Number of times the group has been throttled/limited" and `throttled_time`/`throttled_usec` as "total time duration … throttled." The quotient `nr_throttled / nr_periods` is therefore events per period, not a share of time. Naming it "throttled share of scheduler periods" reads a time share into an event count. The time share has its own counter; slice_throttle already reports it as `throttled_cpus`, but host_containment never mentions it. A ratio of 0.5 does not mean "throttled half the time"; it means "throttled in half the enforcement intervals" — which on a multi-queue slice can coexist with >1 CPU of throttled time, or with low time and bursty events.

2. **Scope is own-limit only, stated by the kernel.** cgroup-v2 says the five stats "only account for throttling caused by this cgroup's own bandwidth limit, not including throttling inherited from ancestor cgroups." `host_containment.cpu_throttle` reads the plants slice's `cpu.stat` and no `cpu.stat.local`, so a quiet ratio does not rule out throttling from above the slice. `slice_throttle` documents this; `host_containment` does not. The docstring therefore claims more coverage than the counter provides.

3. **Inherited throttling has its own file.** `cpu.stat.local` `throttled_usec` is defined as "actual throttling time incurred by this cgroup's own runqueues, which may include throttling inherited from ancestor cgroup bandwidth limits." The subtraction `local - own` is the plant's inference for inherited share (marked UNKNOWN when local absent), not a kernel-named field. The kernel says "may include", not "equals own plus inherited". Treating the difference as cleanly additive inherits that uncertainty; host_containment's omission hides even the existence of the distinction.

Predecessor misread (2026-11-01) corrected what `dropped-commits` counts; this complements it by correcting what the throttle ratio counts. Both are counting-rule misreads — one on git reflog, one on cgroup counters — applied to two modules that share the same vocabulary.

## Application

target: src/mishe_tauftauf/host_containment.py

Proposal for the genome owner, docstring and comment pin only, no behaviour or threshold change in this step:

- In the `CPU_THROTTLE_WARNING` comment (l.44-48) and `cpu_throttle` docstring (l.385-391), replace "Throttled share of scheduler periods" with the kernel's definitions verbatim:
  > `nr_periods` is number of enforcement intervals elapsed, `nr_throttled` is number of times throttled; `ratio = nr_throttled / nr_periods` is events per period, not a time share. The five CFS bandwidth stats are non-hierarchical — they count only this cgroup's own limit, not inherited throttling from ancestors (cgroup-v2, cpu section, "only account for throttling caused by this cgroup's own bandwidth limit"). Throttled time is `throttled_usec`/`throttled_time` (reported per-slice as `throttled_cpus` in slice_throttle); this warning reads the event ratio only and does not claim time, demand or stall. Ancestor throttling is visible in `cpu.stat.local` `throttled_usec` ("actual throttling time … may include throttling inherited from ancestor cgroup bandwidth limits"), which this module does not read.
  Cite both kernel URLs and retain the existing "observation only, never trips the guard" stance.

- Do not change `CPU_THROTTLE_WARNING = .5`, the ratio calculation, or the `cpu_warning` wiring. The change is naming only, so the existing `ratio >= .5` gate stays observation-only.

- Add one unit test beside `tests/test_host_containment.py` (or the repo's existing host-containment test): a fixture with `nr_periods` delta 600, `nr_throttled` 300 (ratio 0.5) asserts `cpu_throttle` returns `ratio 0.5` with `warning True`, and a fixture where `throttled_usec` delta would correspond to 2.5 CPU-equivalents still yields the same ratio — pinning that ratio and time are distinct readings. A missing `cpu.stat.local` case is not applicable to this module (it does not read it) but the docstring must say so.

Acceptance: `python -m pytest tests/test_host_containment.py` (or the repo's host-containment/slice-throttle suite) passes unchanged; ratio logic identical, only wording updated. `tools/check_parsing.py` PASS. `python research/field-review/field_review.py check` PASS. No change to `slice_throttle.py` or `discovery.py` in this step.

Prediction: Over the next 7 days of host samples, `cpu_throttle` ratio and slice `throttled_cpus` disagree in direction on at least one slice (as in 2026-11-01 samples: plants ratio 0.475 with 2.56 CPUs, lifeline ratio 0.422 with 0.10 CPUs), confirming they measure different things. Keep if observed; revise wording if kernel source later shows `nr_throttled` is bounded by `nr_periods`.

Falsifier: If kernel source or a later cgroup-v2 revision redefines `nr_throttled` as bounded by `nr_periods` or redefines the five stats as hierarchical, the "own-limit only" clause would be revised to match the new text. If `throttled_usec` on this host tracks wall time (never exceeds window), the summed-over-runqueues inference would be withdrawn.

Limits: Two kernel doc pages read as rendered HTML (175 kB and 23 kB), not kernel source; the summed-over-runqueues claim is inference from live samples (140 s in 60 s), not a doc statement. The review does not establish demand vs stranding vs burst — that needs PSI or per-period demand, named as next measurement, not claimed here.
