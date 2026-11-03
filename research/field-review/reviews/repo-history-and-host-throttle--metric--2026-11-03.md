---
field: repo-history-and-host-throttle
angle: metric
date: 2026-11-03
status: applied
owner: discover
target: src/mishe_tauftauf/slice_throttle.py
---

# PSI stall share as the throttle metric's effect: per-slice cpu.pressure some/total beside throttled_cpus

## What we read

- Primary text 1: Linux kernel, "PSI - Pressure Stall Information", https://docs.kernel.org/accounting/psi.html — fetched 2026-11-03 as `/tmp/psi-20261103.html` 19730 B sha256 `6994ff647f71fd910f4373184ab18cd6e2da780621d4d6b1596b2b5b65462c04` (html, Sphinx 5.3.0, docutils 0.19). Read in this pass: Pressure interface § and Monitoring for pressure thresholds § and Cgroup2 interface §. Quoted as the page states it:
  - "Pressure information for each resource is exported through the respective file in /proc/pressure/ -- cpu, memory, and io."
  - "The format is as such: `some avg10=0.00 avg60=0.00 avg300=0.00 total=0` / `full avg10=0.00 avg60=0.00 avg300=0.00 total=0`"
  - "The \"some\" line indicates the share of time in which at least some tasks are stalled on a given resource."
  - "The \"full\" line indicates the share of time in which all non-idle tasks are stalled on a given resource simultaneously. In this state actual CPU cycles are going to waste, and a workload that spends extended time in this state is considered to be thrashing."
  - "As such, time spent in this subset of the stall state is tracked separately and exported in the \"full\" averages. CPU full is undefined at the system level, but has been reported since 5.13, so it is set to zero for backward compatibility."
  - "The ratios (in %) are tracked as recent trends over ten, sixty, and three hundred second windows, which gives insight into short term events as well as medium and long term trends. The total absolute stall time (in us) is tracked and exported as well, to allow detection of latency spikes which wouldn't necessarily make a dent in the time averages, or to average trends over custom time frames."
  - "Monitoring for pressure thresholds: Users can register triggers and use poll() to be woken up when resource pressure exceeds certain thresholds. A trigger describes the maximum cumulative stall time over a specific time window, e.g. 100ms of total stall time within any 500ms window to generate a wakeup event."
  - "In a system with a CONFIG_CGROUPS=y kernel and the cgroup2 filesystem mounted, pressure stall information is also tracked for tasks grouped into cgroups. Each subdirectory in the cgroupfs mountpoint contains cpu.pressure, memory.pressure, and io.pressure files; the format is the same as the /proc/pressure/ files."
- Primary text 2: Linux kernel admin guide, "Control Group v2", cpu section, https://docs.kernel.org/admin-guide/cgroup-v2.html — fetched 2026-11-03 as `/tmp/cgroup-v2-20261103b.html` 175364 B sha256 `9c7ea0827bdc8cf0007b26acac105111c210cea7a34b9b00e7e07ad17805165d` (html, Sphinx 5.3.0). Read in this pass: `cpu.pressure` block. Quoted as the page states it:
  - "`cpu.pressure` A read-write nested-keyed file. Shows pressure stall information for CPU. See Documentation/accounting/psi.rst for details. This file accounts for all the processes in the cgroup."
- Local primary text (the plant part this metric maps onto):
  - `src/mishe_tauftauf/slice_throttle.py` (203 lines, HEAD 107b31c6): `throttle_verdict` reports `ratio = nr_throttled/nr_periods` (events per period), `throttled_usec_per_min`, `throttled_cpus = Δthrottled_usec / (Δnr_periods × period_us)` (2026-11-01 metric), and `inherited = local − own` via `cpu.stat.local`; docstring already names the three non-hierarchical counters and the inherited scope. No read of `cpu.pressure`; no stall share.
  - `src/mishe_tauftauf/cpu_stat_text.py` (88 lines): `COUNTERS = ("nr_periods", "nr_throttled", "throttled_usec")`; `LOCAL_COUNTER`; `parse_cpu_max_period`; no PSI helpers.
  - `src/mishe_tauftauf/discovery.py` ll.8416-8424: system PSI already observed — `sense.proc.cpu-pressure` reads `/proc/pressure/cpu` and verifies at least one `some`/`full` line matches `^(some|full) avg10=… total=[0-9]+$`, sample is joined lines or "pressure data unavailable". No delta, no rate, no per-slice read. `sense.proc.memory-pressure`/`io-pressure` beside it; `sense.proc.loadavg`/`memory-available` nearby. Per-slice `cpu.pressure` not read.
  - `src/mishe_tauftauf/host_containment.py` l.48 `CPU_THROTTLE_WARNING = .5` — same count-ratio warning the 2026-11-01 metric called a unit problem; unchanged.
  - Live read-only samples, this host kernel 6.8.0-147-generic, 2026-11-03T03:44Z (same fetch host):
    - System `/proc/pressure/cpu`: `some avg10=21.31 avg60=22.63 avg300=27.81 total=127241077499` / `full avg10=0.00 avg60=0.00 avg300=0.00 total=0`
    - `mishe-plants.slice/cpu.pressure`: `some avg10=17.67 avg60=19.99 avg300=25.24 total=107827450479` / `full avg10=17.66 avg60=19.87 avg300=24.96 total=103737190814`
    - `mishe-lifeline.slice/cpu.pressure`: `some avg10=0.07 avg60=2.01 avg300=3.25 total=33169759363` / `full avg10=0.07 avg60=2.01 avg300=3.25 total=32952765200`
    - `mishe-plants.slice/cpu.stat`: `nr_periods 2407809 nr_throttled 2024099 throttled_usec 1487208386219 nr_bursts 0 burst_usec 0` with `cpu.max 300000 100000` burst 0; prior burst REVISIT 2026-11-03 recorded same zeros.
    - `.mishe-tauftauf/discovery/latest.json` 2026-11-03T03:42:56Z: `sense.proc.cpu-pressure` VERIFIED sample is the two system lines above, joined; per-slice throttle senses are VERIFIED with `throttled_cpus` + `inherited` from the 2026-11-01 work; `sense.proc.memory-pressure`/`io-pressure` VERIFIED.
  - Not re-read: `kernel/sched/psi.c` stall accounting (when a task counts as stalled), `Documentation/accounting/psi.rst` source beyond the html rendering, per-cgroup PSI trigger poll semantics, the 2026-11-03 burst counters' kernel source (`kernel/sched/fair.c`) already deferred to prior REVISIT.

## What this plant already does

- The slice throttle sense is a bounded-delta observation (gap 20–900 s, counter-epoch check) with three readings of the same quota: `ratio` (throttle events per period), `throttled_usec` rate and its CPU-equivalent `throttled_cpus`, and `inherited` via `cpu.stat.local`. All are enforcement-side: how long the CFS bandwidth controller held the slice's runqueues. The 2026-11-01 metric added `throttled_cpus` so the 0.5 warning has a time unit beside the count; the 2026-11-03 mechanism added `nr_bursts`/`burst_usec` + `cpu.max.burst` as the borrowing knob that can mask throttling.
- The system PSI sense is presence-only: it checks that `/proc/pressure/cpu` contains parseable `some`/`full` lines (`avg10/60/300` percentages + `total` in us) and reports VERIFIED vs UNKNOWN. It does not compute a rate, does not delta `total`, and does not read the same file per slice — even though `/sys/fs/cgroup/.../mishe.slice/mishe-plants.slice/cpu.pressure` and `mishe-lifeline.slice/cpu.pressure` are now readable and carry distinct `some` vs `full` series (plants `full ≈ some`, lifeline `full ≈ some ≈ 0–3%`).
- The discovery scan publishes `avg10/60/300` verbatim as sample text and `total` as part of that text, so a spike that does not move `avg10` is still in `total` — but no consumer turns `total` into "stall share in this window". Likewise the throttle's `throttled_usec_per_min` is a per-minute enforcement rate, while PSI's `total` is a per-cgroup experienced-stall absolute that the plant never rates.

## What is new here

The kernel already defines a paired effect metric for the throttle's cause, with a bounded-window rate and a trigger language the plant does not use:

1. **PSI is stall, not throttle — `total` vs `avg*` as spike vs trend.** `total` is cumulative stall microseconds; `avg10/60/300` are percentages over those windows. The doc says `total` exists "to allow detection of latency spikes which wouldn't necessarily make a dent in the time averages, or to average trends over custom time frames." The throttle sense already does custom-frame averaging (`throttled_usec_per_min` over the actual 20–900 s gap); the same arithmetic on PSI is `stall_share = Δtotal / (elapsed × 1e6)` or `stall_usec_per_min = Δtotal / minutes`. Without it, a 2 s stall burst that lifts `total` by 2e6 us but barely moves `avg10` is invisible as a number. The plant reads throttle's custom-frame rate but reads PSI only as raw lines.

2. **Some vs full as partial vs thrashing — including the CPU-full caveat.** "Some = at least one task stalled; full = all non-idle tasks stalled = cycles wasted = thrashing; CPU full is zero at the system level and since 5.13 reported as zero for compat." Per slice, `full` is defined and observed: plants `full avg10 17.66` tracks `some 17.67`, lifeline both near zero. The plant's current PSI check `if line.startswith("some")` accepts both but conflates them — `sense.proc.cpu-pressure` is VERIFIED if either exists, which for system cpu is always `full 0.00`. The metric that matters for SRE is full (thrashing), not just some (some runnable waiting). The distinction is in the live per-slice files and has no field in the throttle sense.

3. **Threshold + window triggers as the PSI metric's experiment interface (poll with EBUSY).** "A trigger describes the maximum cumulative stall time over a specific time window, e.g. 100ms within any 500ms window; write `some 150000 1000000` into the pressure file and poll(); growth is monitored at 10× per window (min 50 ms, max 1 s over 500 ms–10 s windows); unprivileged users can create monitors, window 500 ms–10 s." The throttle sense is poll-sampled at scan gaps (≥20 s); PSI offers event-driven wake on pressure exceeding a threshold within a window, including per cgroup (`cpu.pressure` beside `/proc/pressure/cpu`). The plant uses none of this — it samples PSI once per scan and never arms a trigger, even though the cgroup files exist and the trigger syntax is read-write nested-keyed.

4. **Per-cgroup availability completes the throttle-to-stall pair.** "Each subdirectory in the cgroupfs mountpoint contains cpu.pressure … the format is the same as the /proc/pressure/ files." The throttle budget lives per slice (`cpu.max` 3 vs 1 CPU, burst 0); the stall lives per slice with it. Reading system `/proc/pressure/cpu some 21%` cannot tell whether plants at 17% or lifeline at 0.07% produced it. The per-slice totals above are ~107B vs 33B us — plants owns the stall on this host. Without per-slice PSI, the throttle sense reports enforcement without confirming any tasks were actually stalled (throttled but not runnable) or that stall came from outside the quota (stall without throttle).

Honest limits: one html rendering of `psi.rst` + one cgroup-v2 paragraph, not `kernel/sched/psi.c`; one host, two slices, one 2026-11-03 sample — no trend, no thrashing baseline, no trigger exercised; `full` semantics for CPU vs io/memory differ (CPU full definition is subset-of-some; io/memory full includes distinct reclaim) and this review touches only cpu; trigger window 500 ms–10 s is below the throttle gap 20 s, so sampling PSI at throttle gaps aliases the metric — the fix is reporting the same 10× monitoring interval is not the same as the scan interval.

Predecessor relation: 2026-11-01 metric closed the unit problem on the enforcement side (`ratio` count vs `throttled_cpus` time). 2026-11-03 mechanism was to close the masking problem (`burst` borrowing). This closes the effect side: the throttle sense still lacks the only kernel-defined measure of experienced stall that shares its cgroup scope — per-slice PSI `total` rated over the same gap, with `some`/`full` kept distinct and `avg10/60/300` carried as trend.

## Application

target: src/mishe_tauftauf/slice_throttle.py

Proposal to the owner of `src/mishe_tauftauf/slice_throttle.py` (discover) — observation-only, no guard or quota change, additive beside existing `throttled_cpus`/`inherited`/`burst`:

1. **Read per-slice PSI.** In `read_slice_throttle`, after the existing `cpu.stat`/`cpu.stat.local`/`cpu.max`/`cpu.max.burst` reads, read sibling `cpu.pressure` for each slice (`mishe-plants.slice/cpu.pressure`, `mishe-lifeline.slice/cpu.pressure`). Parse each line matching `^(some|full) avg10=[0-9.]+ avg60=[0-9.]+ avg300=[0-9.]+ total=[0-9]+$` into `{some:{avg10,avg60,avg300,total}, full:{...}}` or None when absent/unparseable. Store `psi_some_avg10`, `psi_full_avg10`, `psi_some_total`, `psi_full_total` in the per-slice `current` dict and thus in `discovery/slice-throttle-prev.json` under the same epoch/gap rules. No new file. Helper lives in `cpu_stat_text.py` as `parse_cpu_pressure(text)` mirroring `parse_cpu_stat`, or inline — whichever keeps `cpu_stat_text` as the sole decoder.

2. **Report PSI as its own UNKNOWN-vs-verified field rate.** In `throttle_verdict`, compute `psi` as:

   ```python
   def _psi_verdict(previous, current, minutes, elapsed) -> dict:
       # per-minute stall rate and stall share; UNKNOWN when cpu.pressure absent
       # or totals went backwards; never zero-imputed.
   ```

   returning `{"state": "unknown", "reason": "psi unavailable"}` when either slice's `cpu.pressure` is None or either `some`/`full` total went backwards or `elapsed <= 0`, else `{"state": "verified", "some": {"avg10": <float>, "avg60": ..., "avg300": ..., "total": <int>, "total_per_min": round(delta_some/minutes,1), "share": round(delta_some/(elapsed*1e6),4)}, "full": {"avg10": ..., "total": ..., "total_per_min": ..., "share": ...}}`. Attach as `"psi": _psi_verdict(…)` beside `"throttled_cpus"`/`"inherited"`/`"burst"`. Do not fold PSI into `ratio`; keep `slice_sense_id` and sample-text additive: append `psi=some:share=<v> total/min=<v> avg10=<v> full:share=<v> avg10=<v>` or `psi=unknown: <reason>` after the burst suffix, so substring checks stay green. Keep the module docstring's "observation only: this never changes a quota" and extend to "and never arms a PSI trigger; triggers remain a follow-up".

3. **Docstring pin.** Extend the module docstring to name PSI: "PSI `cpu.pressure` `some` (at least one task stalled) vs `full` (all non-idle stalled, thrashing) with `avg10/60/300` (%) trends and `total` (us) absolute; `total` is rated as `Δtotal / gap` (share and per-min) over the same 20–900 s gap as throttle; `some` may diverge from throttling (throttled but not runnable, or runnable but throttled from ancestor); system CPU `full` is zero for compat — per-slice `full` is defined (psi.rst + cgroup-v2 cpu.pressure, https://docs.kernel.org/accounting/psi.html)."

Keep `MIN_GAP_SECONDS`/`MAX_GAP_SECONDS`/`STATE_NAME` unchanged. Do not arm a `some 150000 1000000` trigger in this step — the slice file's readability and the per-minute rate are the standalone metric; trigger/poll is the natural follow-up that the PSI doc enables.

Acceptance: `python -m pytest` touching throttle/slice/psi tests passes unchanged when per-slice pressure files are present with the fixtures above — existing `ratio`/`throttled_cpus`/`inherited`/`burst` rows stay identical; new `psi verified {some share ~0.17 full ~0.17 ...}` appears. New unit tests: missing `cpu.pressure` → `psi unknown`; `total` backwards → unknown; fixture with `Δsome 60e6` over 60 s gap → `share 1.0`? (fix: `Δtotal 42e6` over 60 s → `share 0.7` capped ≤1.0? actually share is fraction of window where at least one task stalled, bounded [0,1] — assert rounding). Live read on this host matches plants `some share` ≈ `avg10` magnitude but derived from `Δtotal` over gap, not from `avg10`. `tools/check_parsing.py` PASS. `python research/field-review/field_review.py check` PASS.

Prediction: On this host with plants steady, `psi some share` stays within ~0.10–0.30 and `full ≈ some` over the next 7 days of 60 s samples, while lifeline `some ≤ 0.05` — confirming the throttle sense's 0.84 `ratio` and 2.5-CPU `throttled_cpus` on plants corresponds to experienced stall, not just quota bookkeeping. If a burst is later enabled and `throttled_cpus` falls while `psi share` stays, the metric correctly reports masking: throttle reduced but stall not.

Falsifier: If kernel `psi.c` counts a task stalled only when its cgroup is throttled (not just runnable-but-not-running), the `throttle without stall` distinction collapses and `_psi_verdict`'s docstring is revised to "stall includes throttle-induced stall". If per-slice `cpu.pressure` on this host stops exporting `total` or merges `some`/`full` differently from the html (e.g., `full` always zero per slice), the `full` subfield stays unknown and the docstring is narrowed to `some` only.

Limits: Docs are rendered html, not `kernel/sched/psi.c`; one host two slices one sample — unmeasured under host CPU saturation vs slice quota paths; `avg10/60/300` are kernel-computed EWMA percentages, while `share` is our gap-rate — they track different windows and will disagree by construction; io/memory PSI not wired here — only cpu.

