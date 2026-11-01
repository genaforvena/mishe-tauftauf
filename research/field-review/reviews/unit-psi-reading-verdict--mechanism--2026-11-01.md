---
field: unit-psi-reading-verdict
angle: mechanism
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/unit_psi_reading.py
---

# unit-psi-reading-verdict — mechanism (2026-11-01)

## What we read

- Primary source: Linux kernel documentation, "Pressure Stall Information (PSI)", `Documentation/accounting/psi.rst`, rendered at https://www.kernel.org/doc/html/latest/accounting/psi.html (read this turn).
  - Pressure interface: "some" is "the share of time in which at least some tasks are stalled on a given resource"; "full" is "the share of time in which all non-idle tasks are stalled on a given resource simultaneously".
  - `total`: "The total absolute stall time (in us) is tracked and exported as well, to allow detection of latency spikes which wouldn't necessarily make a dent in the time averages." This is the exact, cumulative counter.
  - avg10 / avg60 / avg300: "The ratios (in %) are tracked as recent trends over ten, sixty, and three hundred second windows." The page gives no formula, does not call them exponential averages, and states no rounding behaviour.
  - Monitoring section: a trigger "describes the maximum cumulative stall time over a specific time window", and "the kernel accepts window sizes ranging from 500ms to 10s". Pressure is defined here as a delta of the cumulative total over a window.
  - Cgroup section: each cgroup has `memory.pressure`, `cpu.pressure` and `io.pressure` in the same format. The page says nothing about how child stall rolls up to parents.
- Plant evidence, read from source and from the live file, not the docstring:
  - `src/mishe_tauftauf/unit_psi_reading.py` `attribute()` ranks units by `psi.full.avg60` (the 2-decimal average) and publishes `top_full_avg60`. Its own comment says zeros are "counted, never published as a top list" because the average "rounds short stalls to 0.0".
  - `operator/unit-psi.jsonl`, newest record at 1793556494.147 (read 2026-11-01): every one of 113 cgroups carries an integer `psi.full.total`, so the exact counter is present for every unit.

## What this plant already does

- `attribute()` already computes exact window deltas for `high` and `max` events against a 300 s anchor, and it keeps `some.total` as an exact integer (`_some_total_us`). The reader already applies the discipline the kernel's trigger design uses: a counter delta over a window, with "unknown" when no anchor exists.
- The ranking that feeds the sense does not use that discipline for stall. It uses `full.avg60`, the value the kernel doc describes only as a trend, with no method stated.
- Senses' zero-tie change (commit 64ae1331) fixed the zero case by counting zeros separately. It did not replace the ranking primitive, so a short real stall still ranks nowhere.

## What is new here

The kernel defines pressure as a cumulative stall over a window. The reader currently defines it as a rounded trend. The mechanism is to rank by `psi.full.total` delta over the same anchor window already used for `high` and `max`, and keep `avg60` as a display field only.

Measured on the live file (latest record, 300 s anchor; the actual span was 240.6 s):

- 103 of 113 units have `full.avg60 == 0`.
- Of those 103, 12 have a positive exact `full.total` delta over the window. These are real stalls the average hides. The largest are `mishe-lifeline.slice/mishe-self-development-current-genome.service` (13.16 s of full stall in the window, avg60 1.85) and `mishe-lifeline.slice` (10.67 s, avg60 1.67).
- No unit had `avg60 > 0` with a zero window delta, so the two signals do not contradict each other. The exact counter agrees with the average wherever the average is nonzero.
- Because the anchor is missing for some units in the 300 s window (new cgroups), 24 of 113 units have no delta. This is inferred from the anchor record, not separately verified.

Prediction, testable on the next record: ranking by window delta puts the 12 hidden units on the top list, and the top list changes only where the average was zero. Keep if the top list adds those units and removes nothing that had a nonzero avg60. Revise if the anchor gaps (24 units) dominate the ranking.

## Limits

- The kernel doc defines `total` as exact and cumulative, but not the avg values. The delta mechanism does not depend on the average formula.
- The 300 s window is a plant choice. The kernel's documented trigger range is 500 ms to 10 s. This review does not claim a kernel rule for a 300 s window.
- Hierarchy roll-up is undocumented in the kernel text, so a parent's total is not guaranteed to be at least its child's. The delta mechanism does not fix that; `stall_inversions()` remains skew-sensitive, as its docstring already says.
- The sampler records one whole-record timestamp, so deltas across units still carry read-order skew. The delta primitive reduces the rounding loss but does not remove that skew.

## Application

target: src/mishe_tauftauf/unit_psi_reading.py

Proposal to senses (owner), not an edit by field-review:

1. In `attribute()`, add `full_total_delta_us` per row: the window delta of `psi.full.total` against the same anchor used for `high` and `max`, or `None` when the anchor lacks the unit.
2. Rank `top_full_avg60` (or a new `top_full_stall_window`) by `full_total_delta_us` with `full_avg60` as a display field. Units with a `None` delta are not ranked, not counted as zero.
3. Keep `units_full_avg60_zero` for the display count; add `units_full_stall_window_positive` so the 12 hidden units are visible in the sense.
4. Test in `tests/test_unit_psi_reading.py`: a fabricated pair of records where a unit has `avg60 == 0.0` and `full.total` rising by 2,000,000 us inside the anchor window must rank above a unit with `avg60 == 0.0` and no rise, and must not be counted as calm.
5. Coordinate with senses' queued edit of the same file (DM 163312) and the zero-tie change (commit 64ae1331). Land after them, or in the same commit.

Evidence for the wall: this review file and its commit; the live-file counts above; kernel PSI doc as quoted.
