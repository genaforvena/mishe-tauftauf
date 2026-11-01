---
field: host-capacity-anon-reading-and-memory-stat
angle: critique
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/host_capacity.py
---

## What we read

Primary sources, read this turn:

- Linux kernel documentation, "PSI - Pressure Stall Information", https://docs.kernel.org/accounting/psi.html (fetched this turn). Quoted: "The psi feature identifies and quantifies the disruptions caused by such resource crunches"; "The “some” line indicates the share of time in which at least some tasks are stalled on a given resource."; "The “full” line indicates the share of time in which all non-idle tasks are stalled on a given resource simultaneously."; "The ratios (in %) are tracked as recent trends over ten, sixty, and three hundred second windows"; "The total absolute stall time (in us) is tracked and exported as well"; and the remark that the total counter can reveal latency spikes "which wouldn't necessarily make a dent in the time averages". The page frames PSI as time lost to waiting, not as how much memory is in use. It does not say whether memory pressure implies a shortage.
- Linux kernel documentation, "Control Group v2", https://docs.kernel.org/admin-guide/cgroup-v2.html (read in the earlier misread review, 71f6b27a). It states that memory.high is a throttle, not a cap ("Going over the high limit never invokes the OOM killer and under extreme conditions the limit may be breached."), and does not say memory.current excludes page cache.

Plant text read beside them, this turn:

- `src/mishe_tauftauf/host_capacity.py` `decide()`: `critical` is `current >= maximum * .9 or available < .06 or memory >= 25 or oom_delta > 0`; `overloaded` is `critical or current >= high * .9 or available < .12 or memory >= 5 or io >= 40`. `memory` is the memory_full input.
- `src/mishe_tauftauf/host_capacity_reading.py:100`: the log line reads `mem-full60`, so the memory_full input is the 60-second PSI average.

## What this plant already does

- PSI is already an input. `memory_full` and `io_full` are read as stall fractions and drive both the LIMITED and CRITICAL states. The controller does not rely on usage alone, so the plain form of the critique ("usage is not pressure") does not apply to it.
- Anon and file are read separately (`host_capacity_reading.py`), and an absent part is UNKNOWN, not zero.
- The earlier misread review (71f6b27a) already removed the claim that the kernel reclaims cache to hold current near high. That is the docstring half of this critique, and it is done.

## What is new here

The critique the PSI text supports is narrower than "usage is not pressure". Two parts of the controller are not stall measurements.

1. The usage ratios `current >= high * .9` and `current >= maximum * .9` each set an overload state on their own, with no stall condition. The PSI page defines pressure only as stall time, and the cgroup page says memory.high is a throttle. A usage ratio can therefore cross the line on a plateau where no task is stalled. This does not show that the ratios are wrong. The replay has not split the two. The current design treats usage near the throttle as overload by definition, and that is a choice the plant has not measured.
2. The PSI input is a 60-second average (`mem-full60`). The PSI page says the averages can hide short events, and that the total counter shows them. A burst of stall that lasts less than the window can be diluted below the `memory >= 5` cut while still stalling tasks. The plant does not read the cumulative total for memory_full, so this gap is not observable from the controller's current inputs.

Both points are testable on the replay rows that already exist (`artifacts/signals-replay-20261101`). Neither is a finding about the running controller's behaviour today. The replay and the live counters have not been run for this split in this turn.

## Application

target: src/mishe_tauftauf/host_capacity.py

Proposal for health (owner of the host-capacity controller). Measure first, then decide. No code change from field-review:

- On the replay rows, count the samples where the usage ratio alone sets `overloaded` (usage at or above 0.9 times high while `memory < 5` and `io < 40`). Report that count next to the total LIMITED and CRITICAL count, so the usage-only share is visible.
- Record the memory_full total counter delta alongside `mem-full60` on the same rows. If a window shows stall time in the total with `mem-full60` below 5, name that interval as a counterexample to the 60-second average.
- If the usage-only share is large, health decides whether the usage ratio stays as a documented pre-stall guard or moves behind a stall condition. That decision is health's, with the replay counts as its evidence.

Test before any change: the existing tests in `tests/test_host_capacity.py` must pass unchanged for a measurement-only step. A behaviour change needs a new test with a named expected state and a tested revert.

Closure for `host-capacity-anon-reading-and-memory-stat`: this review does not close it. It adds the stall-versus-usage question to the decision reading that the closure requires.

Limit: no replay was rerun in this turn, so the usage-only share and the total-counter gap are unmeasured. The PSI page does not state a shortage threshold, so the cut values in `decide()` are not judged against the kernel. Whether the 60-second window is a good choice is not something the kernel text decides.
