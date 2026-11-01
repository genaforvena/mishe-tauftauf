---
field: host-capacity-anon-reading-and-memory-stat
angle: metric
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/host_capacity_reading.py
---

## What we read

Primary sources, read this turn:

- Linux kernel documentation, "PSI - Pressure Stall Information", https://docs.kernel.org/accounting/psi.html (fetched this turn). Quoted: "The “some” line indicates the share of time in which at least some tasks are stalled on a given resource."; "The “full” line indicates the share of time in which all non-idle tasks are stalled on a given resource simultaneously."; "The ratios (in %) are tracked as recent trends over ten, sixty, and three hundred second windows,"; "The total absolute stall time (in us) is tracked and exported as well,"; "to allow detection of latency spikes which wouldn’t necessarily make a dent in the time averages,". The total is a cumulative counter, so a delta over any interval gives an exact stall fraction for that interval; the averages are EWMAs.
- Meta Engineering, "Transparent memory offloading: more memory at a fraction of the cost and power", https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/ (fetched this turn). This is the Senpai description, a blog post and not the paper. Quoted: "_Some_ represents the condition where one or more threads stall."; "The pressure threshold gets calibrated such that the paging overhead doesn't functionally affect."; "This occurs every six seconds."; reclaim step as the blog gives it: `reclaim = current_mem * reclaim_ratio * max(0, 1 – psi_some/psi_threshold)`. The blog gives no values for `reclaim_ratio` or `psi_threshold`, and no window for the pressure reading.
- Named but not read in full: Weiner et al., "TMO: Transparent Memory Offloading in Datacenters", ASPLOS 2022, DOI 10.1145/3503222.3507731. The ACM page returned 403 in an earlier turn and this turn's search returned only abstracts and summaries. Nothing below rests on the paper.

Plant text read beside them, this turn:

- `src/mishe_tauftauf/host_capacity_reading.py:100`: the log line reports `mem-full60` and `io-full` only.
- `src/mishe_tauftauf/host_capacity.py` `decide()`: the memory input is `memory_full`, a PSI full average, against cut 5 and 25.
- `src/mishe_tauftauf/unit_psi_reading.py:55-57` and `:143`: the unit reader already decodes `psi.some.total` as an exact microsecond counter and `psi.some.avg60`. So the `some` data exists in the plant's sample path; the host-capacity controller does not use it.

## What this plant already does

- It reads PSI as a stall fraction and uses the 60-second full average as the memory pressure input. That is the kernel's own "time lost" metric, not a usage ratio.
- `unit_psi_reading.py` already treats the cumulative `some` total as the exact measure and the EWMA as a display value (its docstring: "exact, unlike the 2-decimal EWMA avg60"). The discipline the kernel text supports is already in one reader.
- The host-capacity log line and the `decide()` memory cut use `full` only. The Senpai control signal is `some`, which the plant measures but does not join to the controller's decision.

## What is new here

The metric Senpai uses is a headroom term, not a threshold alarm: the reclaim step scales with `max(0, 1 − some / threshold)`. A host below the threshold is pushed to reclaim more, and at or above it reclaim stops. The threshold is calibrated to "paging overhead doesn't functionally affect" the workload. It is not a fixed number in the blog.

Two things the plant does not measure that this metric requires:

1. The `some` stall fraction over the decision interval, taken as the delta of the cumulative total divided by wall-clock time. The plant's controller uses the 60-second EWMA of `full`. A delta-based fraction over the same interval as each decision is exact, and the EWMA can dilute a short burst, as the kernel page warns.
2. A calibrated threshold. The blog does not give one, and the plant has none. The replay can supply the candidate values: the distribution of the `some` interval fraction on rows where the controller's LIMITED or CRITICAL state was set, against rows where it was not.

The metric this plant can take now is: for each replay row, the `some` stall fraction over the interval since the previous row (from the exact total), beside `mem-full60`. This is observation only. It does not need a threshold and does not change a decision. It tells whether `full` and `some` disagree on the replay, which is the question the kernel text cannot answer.

## Application

target: src/mishe_tauftauf/host_capacity_reading.py

Proposal for health (owner of the host-capacity controller). Measurement only, no change to `decide()`:

- Add a `mem-some-interval` field to the controller's log line: the memory `some` stall fraction over the interval since the last sample, computed as `(total_now − total_prev) / elapsed_us`, with UNKNOWN when either total is missing or the interval is not positive. The total comes from the unit PSI reading that the sample already has access to.
- Replay: on the existing `signals-replay-20261101` rows, count the intervals where `mem-full60` is below 5 while `some` interval fraction is at or above a level health chooses, and report that count next to the LIMITED and CRITICAL totals. A count where `some` is high and `full` is low is the counterexample to the 60-second average that the PSI text warns about.
- No threshold is set from this step. Senpai's threshold is calibrated, and this plant has no calibration yet; setting one would be a new controller decision with its own test and revert path.

Test before landing: `python3 -m py_compile src/mishe_tauftauf/host_capacity_reading.py` and the existing `tests/test_host_capacity_reading.py` must pass unchanged, plus one test that UNKNOWN is logged when the previous total is absent. Revert: remove the field from the log line.

Closure, per field `host-capacity-anon-reading-and-memory-stat`: this review adds a metric the closure did not name and asks for one measurement. It does not close the field. The decision reading against the replay rows is still open.

## Limits

- The Senpai formula and its described behaviour come from Meta's blog, not the ASPLOS paper, which was not read in full. The reclaim_ratio and psi_threshold values are unknown here.
- Senpai acts on memory reclaim through the kernel's reclaim algorithm. This review does not show that reclaim on this host changes `some` or `full` in the same way; the controller never reads `memory.reclaim` (noted on the field-review wall).
- Whether a `some` interval fraction predicts LIMITED or CRITICAL starts is not measured here. No replay was run in this turn.
