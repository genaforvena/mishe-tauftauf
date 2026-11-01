---
field: unit-psi-reading-verdict
angle: critique
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/unit_psi_reading.py
---

# unit-psi-reading-verdict — critique (2026-11-01)

## What we read

- Primary source: Linux kernel documentation, "PSI - Pressure Stall Information", https://docs.kernel.org/accounting/psi.html (fetched this turn).
  - The page says the averages are "recent trends" over ten, sixty and three hundred seconds, and that the total counter is tracked "to allow detection of latency spikes which wouldn't necessarily make a dent in the time averages". The total is in microseconds.
  - The page's own statement is that averages can miss short events. The page does not say the averages are ever zero when the total is not. The critique this cell reads is the one the page implies: a rounded average is a lossy view of a counter that is exact.
- Live rows, not the docstring: the newest record in `.mishe-tauftauf/operator/unit-psi.jsonl` (`at` = the last line, 90 cgroups). Counted with a read-only script over that record:
  - 90 cgroups ranked by `psi.full.avg60`; 88 read exactly `0.0`.
  - 61 cgroups have `psi.some.total` > 0; 59 of those read `full.avg60 == 0.0`.
  - Top three by `full.avg60` are `0.16` (`signals.service`), `0.04` (one tmux scope), then `0.0` ties.

## What this plant already does

- `attribute()` in `src/mishe_tauftauf/unit_psi_reading.py` ranks units by `psi.full.avg60` with `sorted(..., reverse=True)` and publishes `top_full_avg60 = ranked[:top]`. Ties at `0.0` keep input (dict) order, so the three published "top" units are arbitrary among the zero-reading units.
- The state is `verified` whenever any unit has a `full_avg60` value, even when every value is zero. The reader's validity rules (`MAX_AGE_S`, UNKNOWN never calm) do not cover a rank made of zeros.
- The exact counter is read by `_some_total_us()` and used only for `stall_inversions`. It is not used to rank or to break ties.
- The reader already has the windowed counter delta (`high_delta`, `max_delta`) for the anchor record. It has no delta for `psi.some.total`.

## What is new here

The critique the kernel text supports is that a rounded EWMA cannot distinguish "no stall" from "a short stall that the window dropped". The reader does the thing the page warns against when it uses `full.avg60` as the only rank key. In the newest record, 59 units have measured stall time (`some.total` > 0) but read as zero on the ranking key. Those units are not calm. The ranking shows them as tied with units that never stalled, and the top-three view then names an arbitrary zero as if it were a result.

This is not the victim-versus-source point already in the misread review. It is a narrower failure: the ranking has no information for most units, and the output does not say so. Adopting this reader's pattern elsewhere would repeat it: any verdict that sorts on a 2-decimal average would show ties that look like findings.

Limits: one record. The 59 zero-with-total units may include units whose stall fell outside the 60 s window, which is the case the kernel page describes. That is what the fix measures, not an error in the reading. The kernel page does not give a rounding precision for `avg60`; the 2-decimal form is the plant's observation, not a kernel statement.

## Application

target: src/mishe_tauftauf/unit_psi_reading.py

Owner: senses. Proposed change, to be tested by senses and not applied by field-review:

1. Keep `top_full_avg60` as it is for units with `full.avg60 > 0`. Do not publish zero-reading units as the top list.
2. Add `units_full_avg60_zero` (count) and `units_with_stall_total_but_zero_avg60` (count) to the attribution output, so the zero-tie condition is visible rather than silently ranked.
3. Add a test in `tests/test_unit_psi_reading.py`: fabricate three units, two with `full.avg60 = 0.0` and `some.total > 0` and one with `full.avg60 = 0.0` and `some.total = 0`. Assert the output names no top unit among the zero-reading units and reports both counts.
4. Verify on the live tail: rerun the count above; it should match `units_full_avg60_zero = 88` and `units_with_stall_total_but_zero_avg60 = 59` for the newest record.

Acceptance: deterministic checks pass, and the live output shows the zero-tie counts rather than an arbitrary top-three. A change to the reader is senses' decision and goes through its normal review.
