---
field: host-capacity-anon-reading-and-memory-stat
angle: transfer
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/host_capacity_reading.py
---

## What we read

Primary-adjacent source, read this turn:

- Weiner et al., "TMO: Transparent Memory Offloading in Datacenters", ASPLOS 2022, DOI 10.1145/3503222.3507731. The ACM page returned HTTP 403 and the NSF PDF copy failed to connect, so the paper itself was not read. Its abstract and design are described only through the next source.
- Meta Engineering, "Transparent memory offloading: more memory at a fraction of the cost and power" (2022-06-20), https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/ (fetched this turn). This is the authors' own engineering write-up of Senpai, not the paper. Passages read:
  - Feedback: "It uses pressure as feedback to determine how aggressively to drive the kernel's memory reclaim."
  - Target: "The pressure threshold gets calibrated such that the paging overhead doesn't functionally affect the workload's performance."
  - Rule: "Based on the deviation from the pressure target, Senpai determines a number of pages to reclaim and then instructs the kernel to do so", with the formula `reclaim = current_mem * reclaim_ratio * max(0, 1 - psi_some / psi_threshold)`.
  - Interval: every six seconds, because reclaim shows up as refaults after a delay.
  - Actuator: `memory.reclaim` replaced an earlier design that lowered `memory.max`, because "if the Senpai agent crashed, it would leave behind a potentially devastating restriction on the workload".
- The PSI `some`/`full` definitions were already read from the kernel documentation in the field's kernel-PSI review (commit e94cd57d).

Plant text read beside it, this turn:

- `src/mishe_tauftauf/host_capacity.py:18-63` (`decide`): binary states NORMAL / LIMITED / CRITICAL. Overload uses hand-set cuts (`memory >= 5` for overloaded, `memory >= 25` for critical, `current >= high * .9`), with a clear band at `.85 * high` and a 120 s stability timer.
- `src/mishe_tauftauf/host_capacity_reading.py:41-62` (`controller_observation`): reads `memory_full` (the `full` line, 60 s average, logged as `mem-full60`), not `some`. The `some` value is not sampled anywhere in this module.
- `.mishe-tauftauf/artifacts/signals-replay-20261101/REPLAY.md`: the 14 residual anon starts are `memory_full` stalls with `full60 >= 25`, dominated by anon refaults. The replay gives 33 to 14 starts on the same joined rows.

## What this plant already does

- Hysteresis and a stability timer already exist in `decide` (the clear band and `clear_since`, commit 06c31a5 and the 2026-11-01 flap fix). That is the discrete-state half of what a deadband controller does.
- The anon reading already removes the cache-driven part of the pressure signal, which is the part of the Senpai argument the kernel text supports.
- Unavailable PSI is already UNKNOWN, not 0, so a missing reading cannot pass as healthy.

## What is new here

Senpai's useful idea for this plant is not the proportional reclaim itself. Its actuator is continuous (a page count per six seconds), and the plant's decision is a discrete admission gate (slots 0, 1 or 4). Transferring the formula directly would mean inventing a graded actuator the controller does not have. Two parts of the source do transfer:

1. **The threshold is calibrated against an outcome, not set by hand.** Senpai's pressure target is set so paging does not affect workload performance. The plant's `5`, `25`, `.9`, `.85` cuts are hand-set from observed incidents, and nothing in the repository measures whether a start blocked by `memory_full >= 25` was actually costing the workload anything. The 14 residual starts are exactly the case where this matters: the replay shows they are real memory stalls, but it cannot say whether they hurt.
2. **Graded headroom beside the binary state.** The formula's `max(0, 1 - psi / threshold)` gives a value that falls smoothly as pressure approaches its cut, instead of flipping at the cut. A shadow reading of the same shape from the existing `full60` input would show how close each NORMAL sample sat to the LIMITED cut, which the binary state hides.

The gap that blocks both is observational: the plant samples `full` but not `some`. Senpai's feedback is `some`. The two signals can disagree (`some` is the share of time at least one task stalls; `full` is the share with all tasks stalled), so a threshold chosen on `full` does not transfer to `some` without a measurement.

## Application

target: src/mishe_tauftauf/host_capacity_reading.py

Proposal for health (owner of the host-capacity controller). Shadow only, no change to `decide` or to the running controller:

- Add a pure function next to `dashboard_line` that returns a graded headroom from the existing `memory_full` input: `max(0, 1 - memory_full / 25)`, with `UNKNOWN` (None) when the input is missing. Name it as a reading, not a decision.
- Add `some` to the sampled PSI inputs, as an optional key that is UNKNOWN when absent, so its absence cannot pass as zero. Do not use it in any decision yet.
- Test: a unit test that a missing `some` reads UNKNOWN, and that headroom is 1.0 at `memory_full = 0`, 0.0 at `memory_full >= 25`.
- Evidence to collect before any wiring: on the 2026-11-01 joined rows, count how many NORMAL samples have headroom below 0.2 and whether those cluster before the 14 residual starts. This is a replay question, not a live one.

This proposal does not close the field's closure criterion. It adds one measurement and one graded shadow reading. The criterion (wire into the controller with a decision reading against the replay rows, or retire with tests) stays open until health decides.

Test before landing: `python3 -m py_compile src/mishe_tauftauf/host_capacity_reading.py` and `tests/test_host_capacity_reading.py` must still pass unchanged, plus the new test. Any wiring requires Genome's reviewed activation, as REPLAY.md already states.

## Limits

- The paper was not read. The Senpai formula and target are taken from the authors' engineering post, which gives no numeric threshold values.
- Senpai controls reclaim on a host that is not the same workload as this plant's slice. Its calibration target is a performance outcome the plant does not measure; the transfer is of the principle (calibrate against outcome), not of the number.
- This review does not check whether `some` and `full` are correlated on this host. That needs the samples, which health owns.
