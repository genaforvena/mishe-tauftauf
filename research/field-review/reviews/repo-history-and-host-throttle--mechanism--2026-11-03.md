---
field: repo-history-and-host-throttle
angle: mechanism
date: 2026-11-03
status: applied
owner: discover
target: src/mishe_tauftauf/slice_throttle.py
---

# CFS bandwidth burst as bounded borrowing: the throttle counters the plant does not read

## What we read

- Primary text 1: Linux kernel admin guide, "Control Group v2", cpu section, https://docs.kernel.org/admin-guide/cgroup-v2.html — fetched 2026-11-03 as `/tmp/cgroup-v2-20261103b.html` 175364 B sha256 `9c7ea0827bdc8cf0007b26acac105111c210cea7a34b9b00e7e07ad17805165d` (html, Sphinx 5.3.0). Read in this pass: §cpu.stat block and §cpu.max / §cpu.max.burst / §cpu.pressure blocks. Quoted as the page states it:
  - "`cpu.stat` A read-only flat-keyed file. This file exists whether the controller is enabled or not. It always reports the following three stats … `usage_usec` `user_usec` `system_usec` and the following five when the controller is enabled, which account for only the processes under the fair-class scheduler: `nr_periods` `nr_throttled` `throttled_usec` `nr_bursts` `burst_usec`"
  - "Note that the above five CFS bandwidth stats are non-hierarchical; they only account for throttling caused by this cgroup's own bandwidth limit, not including throttling inherited from ancestor cgroups."
  - "`cpu.max.burst` A read-write single value file which exists on non-root cgroups. The default is '0'. The burst in the range [0, $MAX]."
  - "`cpu.stat.local` … reports the actual throttling time incurred by this cgroup's own runqueues, which may include throttling inherited from ancestor cgroup bandwidth limits." (already cited by 2026-11-01 mechanism, retained here only to keep scope distinct — this review isolates burst)
- Primary text 2: Linux kernel, "CFS Bandwidth Control", https://docs.kernel.org/scheduler/sched-bwc.html — fetched 2026-11-03 as `/tmp/sched-bwc-20261103b.html` 23172 B sha256 `44f906c778a1a2b8b6df159276774a7709458879f809176b0ce2a2dfebc6e39e` (html). Quoted as the page states it:
  - "CFS bandwidth control is a CONFIG_FAIR_GROUP_SCHED extension which allows the specification of the maximum CPU bandwidth available to a group or hierarchy."
  - "Within each given 'period' (microseconds), a task group is allocated up to 'quota' microseconds of CPU time. That quota is assigned to per-cpu run queues in slices as threads in the cgroup become runnable. Once all quota has been assigned any additional requests for quota will result in those threads being throttled. Throttled threads will not be able to run again until the next period when the quota is replenished."
  - "Burst feature" §: "This feature borrows time now against our future underrun, at the cost of increased interference against the other system users. All nicely bounded." and "The burst feature observes that a workload doesn't always executes the full quota; this enables one to describe u_i as a statistical distribution. For example, have u_i = {x,e}_i, where x is the p(95) and x+e p(100) (the traditional WCET)." and "That is, suppose we have 2 tasks … p(95)*p(95) = 90.25% … p(5)p(5) = 0.25% … worst case deadline miss, will be Sum e_i; that is, there is a bounded tardiness"
  - "Quota, period and burst are managed within the cpu subsystem via cgroupfs. Note: The cgroupfs files described in this section are only applicable to cgroup v1. For cgroup v2, see Documentation/admin-guide/cgroup-v2.rst"
- Local primary text (the plant part this mechanism maps onto):
  - `src/mishe_tauftauf/slice_throttle.py` (200 lines, read 2026-11-03 at HEAD 489215af): module docstring names the three non-hierarchical counters `nr_periods`/`nr_throttled`/`throttled_usec` and the inherited inference via `cpu.stat.local`; `throttle_verdict` computes `ratio`, `throttled_usec_per_min`, `throttled_cpus = throttled_usec / (nr_periods × period_us)`, `inherited = local − own`; `read_slice_throttle` persists `local_throttled_usec` + `period_us` + the three counters in `discovery/slice-throttle-prev.json`. No read of `nr_bursts`, `burst_usec`, or `cpu.max.burst`; `cpu_stat_text.CPU_MAX` parsing ignores burst.
  - `src/mishe_tauftauf/cpu_stat_text.py` (88 lines): `COUNTERS = ("nr_periods", "nr_throttled", "throttled_usec")` + `parse_cpu_stat` requiring all listed counters; `parse_cpu_max_period` reads `$MAX $PERIOD` from `cpu.max`; no `cpu.max.burst` reader, no `nr_bursts`/`burst_usec` support.
  - `src/mishe_tauftauf/host_containment.py` l.42-48 `CPU_THROTTLE_WARNING = .5` comment "Throttled share of scheduler periods" — wording already flagged by 2026-11-03 misread as events-per-period, not time share; no burst mention.
  - Live read-only samples, this host, kernel 6.8.0-147-generic (same fetch host), 2026-11-03:
    - `mishe-plants.slice`: `cpu.max 300000 100000` (quota 3 CPU, period 100000 us), `cpu.max.burst 0`, `cpu.stat` `nr_periods 2405422 nr_throttled 2022244 throttled_usec 1486614407036 nr_bursts 0 burst_usec 0`, `cpu.stat.local throttled_usec 148702…` (inherited delta ~44 ms over prior fetch)
    - `mishe-lifeline.slice`: `cpu.max 100000 100000` (1 CPU), `cpu.max.burst 0`, `cpu.stat` `nr_periods 2932077 nr_throttled 1470933 throttled_usec 249359157374 nr_bursts 0 burst_usec 0`
    - Per-slice PSI exists: `/sys/fs/cgroup/…/mishe.slice/mishe-plants.slice/cpu.pressure` readable `some avg10=18.79 avg60=23.86 avg300=31.38 total=107773283173 / full avg10=18.62 …` and `lifeline` slice has separate file; system `/proc/pressure/cpu` `some avg10=24.13 … full avg10=0.00` (host-wide). Prior 2026-11-01 transfer reported this file absent on that date — now present.
  - Not re-read: kernel source `kernel/sched/fair.c` bandwidth accounting for burst carry, LKML burst patch rationale beyond the sched-bwc summary, cgroup v2 burst controller enablement conditions; PSI per-cgroup vs system split beyond the two live files above.

## What this plant already does

- The slice throttle sense is observation-only and already distinguishes three readings of the same quota: an event ratio (`nr_throttled/nr_periods`), a time quantity (`throttled_usec` and its CPU-equivalent `throttled_cpus`), and an inherited share (`local − own` via `cpu.stat.local`). All three are derived from the three CFS bandwidth counters plus `cpu.stat.local` and `cpu.max` period. State remains UNKNOWN when the gap is <20 s or >900 s or counters go backwards — the sense never imputes calm.
- The 2026-11-01 mechanism added `cpu.stat.local` and `throttled_cpus`; the 2026-11-03 misread clarified `ratio` is events per period, not a time share, and that the five stats are own-limit only. Both deliberately left burst absent: `cpu.max.burst` stays at kernel default 0 on both slices, and `nr_bursts = 0 burst_usec = 0` on every live sample, so the plant's current picture is complete for the current config — but blind to the mechanism that would change it.
- The discovery side of this field (head-rewinds) was paired in two REVISIT transfers: 2026-11-01 PSI `some` vs count for throttle half, 2026-11-03 CT STH/consistency for history half. Burst is the throttle half's *second* operational knob — not another pressure reading, but a bounded-borrowing control that alters whether throttling occurs at all.
- `host_containment` already reads system PSI but only `memory`/`io` `full` `avg10`; CPU PSI is not a guard. Per-slice `cpu.pressure` now exists and is readable but unread.

## What is new here

The kernel already defines the missing knob, its budget rule, and its two counters — none of which the plant decodes:

1. **Borrowing against future underrun, with bounded tardiness.** `sched-bwc` does not present burst as "extra quota". It is time borrowed now against future underrun, at the cost of interference, with "worst case deadline miss will be Sum e_i" (sum of each task's excess over its p95). The cgroup-v2 file `cpu.max.burst` is that knob, default 0, range [0, $MAX], pinned on this host at 0 for both slices. A quiet throttle ratio therefore has two distinct explanations the plant cannot separate: genuinely under quota, or over quota but within borrowed burst that masked the throttle. Without reading `cpu.max.burst` the config side is unobserved.

2. **Two new counters that name how often and how much burst was used.** `cpu.stat`'s five CFS stats are the three the plant reads plus `nr_bursts` "number of times burst was used" and `burst_usec` "total burst time consumed" (names from the field list order; the cgroup-v2 page lists the names without per-field prose, counts as listed). They are non-hierarchical like the other three — own-limit burst only — and 0 on this host only because `cpu.max.burst` is 0. Reporting them beside `ratio`/`throttled_cpus` would let a future reader tell "no burst configured, so no throttling was masked" from "burst configured and consumed, throttling was deferred". The plant currently reports no burst field at all, so the distinction is unmeasured.

3. **PSI exists per slice and names the stall that throttling without burst would produce.** `cpu.pressure` "shows pressure stall information for CPU … this file accounts for all the processes in the cgroup" (cgroup-v2 cpu.pressure block) and the live host now exposes it per slice (`mishe-plants.slice/cpu.pressure` some/full beside `mishe-lifeline.slice/cpu.pressure`). Prior transfer read only `/proc/pressure/cpu` and noted the cgroup file absent; now the per-slice reading is available as a check that burst actually reduced stall. Burst is the action, PSI is the effect — the page defines both, the plant reads neither for CPU.

Honest limits: one cgroup-v2 html fetch + one sched-bwc html fetch as rendered docs, not kernel source; `nr_bursts`/`burst_usec` prose is inferred from field-name ordering and sched-bwc's burst narrative, not from per-field kernel doc sentences (the upstream page lists the five names but details only throttling's non-hierarchical scope in prose); live zeros are a config artifact (burst=0), not a trend; PSI ↔ throttling causation is not established here — burst could reduce throttle time without reducing PSI stall proportionally, depending on workload CDFs the page says determine interference. The plant must read the numbers and not assume burst helps.

Predecessor relation: 2026-11-01 mechanism closed `cpu.stat.local` inherited scope; 2026-11-03 misread closed events-vs-time wording. Both left `nr_bursts`/`burst_usec`/`cpu.max.burst` and per-slice `cpu.pressure` as the remaining unread operational mechanism for this field's throttle half. History half has its own mechanism proposals (CT STH consistency) and is not re-argued here.

## Application

target: src/mishe_tauftauf/slice_throttle.py

Proposal to the owner of `src/mishe_tauftauf/slice_throttle.py` (discover) — observation-only, no guard or quota change:

1. **Decode the two burst counters.** In `cpu_stat_text.py` add `BURST_COUNTERS = ("nr_bursts", "burst_usec")` and a helper `parse_burst_stat(text) -> dict | None` mirroring `parse_cpu_stat` but over the burst pair; in `slice_throttle.py` import it. Or extend `COUNTERS` to five and keep the three-period logic intact — choose whichever preserves the existing `throttle_verdict` delta math for the known three.

2. **Read and persist burst beside throttle.** In `read_slice_throttle`, after the existing `cpu.stat`/`cpu.stat.local`/`cpu.max` reads, read `cpu.max.burst` (integer or None when absent/unparseable) and the burst pair from the same `cpu.stat` body. Store `burst_max`, `nr_bursts`, `burst_usec` in the per-slice `current` dict and thus in `discovery/slice-throttle-prev.json` under the same epoch/gap rules. No new file.

3. **Report burst as its own UNKNOWN-vs-verified field.** In `throttle_verdict`, compute `burst` as:
   ```python
   def _burst_verdict(previous, current, minutes) -> dict:
       # per-minute burst consumed and burst events; UNKNOWN when cpu.max.burst absent
       # or burst counters absent or going backwards; never zero-imputed.
   ```
   returning `{"state": "unknown", "reason": "burst counters unavailable"}` when `cpu.max.burst` or either burst counter is None or went backwards, else `{"state": "verified", "burst_max": <int>, "nr_bursts": delta, "burst_usec": delta, "burst_usec_per_min": round(delta/minutes,1), "bursts_per_min": round(bursts/minutes,1)}`. Attach as `"burst": _burst_verdict(…)` beside existing `"inherited"` and `"throttled_cpus"`. Do not fold burst into `ratio` or `throttled_cpus`; keep the docstring's "observation only: this never changes a quota" stance and extend it to "and never changes cpu.max.burst".

4. **Docstring pin.** Extend the module docstring's Counter scope paragraph to name the full five: "`nr_periods`, `nr_throttled`, `throttled_usec`, `nr_bursts`, `burst_usec` are non-hierarchical (own-limit only); `burst` counts borrowing against future underrun with bounded tardiness Sum e_i (sched-bwc Burst feature, https://docs.kernel.org/scheduler/sched-bwc.html) and is measured via `cpu.max.burst` + `nr_bursts`/`burst_usec`; `cpu.stat.local throttled_usec` covers ancestor throttling as before. A quiet burst field means either no burst configured or none consumed — read `burst_max` to tell them apart."

Keep `slice_sense_id` and sample text additive: append `burst_max=<v> bursts/min=<v> burst_usec/min=<v>` or `burst=unknown: <reason>` after the inherited suffix, so existing substring checks stay green. Do not add a per-slice `cpu.pressure` read in this step — the slice file's presence is verified above and is the natural follow-up, but burst accounting is the standalone operational mechanism and must land first.

Acceptance: `python -m pytest` touching throttle/slice tests passes unchanged when burst counters are 0 and `cpu.max.burst=0` — existing fixtures yield `burst verified {burst_max 0 nr_bursts 0 …}` not UNKNOWN. New unit tests: missing `cpu.max.burst` → `burst unknown`; `nr_bursts` backwards → unknown; fixture with `nr_bursts` delta 12 over 60 s → `bursts_per_min 12.0`; live read on this host matches the zeros above. `tools/check_parsing.py` PASS. `python research/field-review/field_review.py check` PASS.

Prediction: With `cpu.max.burst=0` held on this host, `burst` stays verified-zero for both slices over the next 7 days of samples, and `nr_bursts`/`burst_usec` never exceed 0 — confirming the current quiet throttle picture is not burst-masked. If `burst_max` is later raised to e.g. 50000 on plants, `burst_usec` deltas become non-zero on at least one sample before a `nr_throttled` increment would otherwise have occurred, confirming masking. Revert to unknown-burst if the kernel on this host stops exporting the two fields or redefines them hierarchical.

Falsifier: If kernel source shows `nr_bursts`/`burst_usec` count ancestor burst as well (hierarchical) or counts something other than consumed burst time/events as named, the non-hierarchical/burst-consumed wording is revised and `_burst_verdict` is renamed to "own-burst only if so named". If `cpu.max.burst` on a non-root cgroup is not readable or not settable as the html suggests, the `burst_max unknown` path stays and the docstring is narrowed to counters only.

Limits: Docs are rendered html, not `kernel/sched/fair.c`; per-field burst prose is inferred from field list + sched-bwc narrative, not re-derived. One host, two slices, default-zero burst — unmeasured under non-zero config. PSI per-slice effect is named but not wired here; that wiring belongs to the follow-up reading that pairs `burst_usec` with `cpu.pressure some/total`.
