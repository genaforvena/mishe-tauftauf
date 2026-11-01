---
field: unit-psi-reading-verdict
angle: misread
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/unit_psi_reading.py
---

# unit-psi-reading-verdict — misread (2026-11-01)

## What we read

- Primary source: Linux kernel documentation, "PSI - Pressure Stall Information", https://docs.kernel.org/accounting/psi.html (fetched this turn).
  - The "some" line is "the share of time in which at least some tasks are stalled on a given resource". The "full" line is "the share of time in which all non-idle tasks are stalled on a given resource simultaneously".
  - avg10/avg60/avg300 are "recent trends" in percent. The page says the total counter exists "to allow detection of latency spikes which wouldn't necessarily make a dent in the time averages". The total is in microseconds.
  - "CPU full is undefined at the system level."
  - The page does not state that a stall is charged to a cgroup's ancestors. This is the premise the recent cell already noted as unverified.
- Plant source read against the kernel text: `src/mishe_tauftauf/unit_psi_reading.py`, function `attribute()` and the `top_full_avg60` output, plus `stall_inversions()`.
- Live rows, not the docstring: the newest record in `.mishe-tauftauf/operator/unit-psi.jsonl` (about 190 records, one per minute) at `at=1793554328`. It carries `psi.full.avg60` and `psi.some.total` for about 90 cgroups, including the parent slices `mishe-plants.slice` and `mishe-lifeline.slice`. In that record the parent `mishe-plants.slice` has `some.total` 2274971868 µs, above the sum of its listed children. That is consistent with ancestor charging, but one record does not prove it.

## What this plant already does

- `attribute()` ranks every unit by `psi.full.avg60` and publishes the top three as `top_full_avg60`. `tools/unit_psi_attribution.py` presents the same ranking as "Per-unit PSI attribution".
- `read_unit_psi()` uses the exact `psi.some.total` counter for the hierarchy check (`stall_inversions`). The docstring says the total is exact and the EWMA is not, which matches the kernel's reason for the total.
- `stall_inversions()` assumes a child's some-stall cannot exceed its ancestor's. That assumption is in the code's docstring, not in the kernel text.

## What is new here

The kernel text defines "full" as a property of one group's own tasks: every non-idle task in that group is stalled at once. A high `full.avg60` therefore says the unit is a victim of contention. It does not say the unit is the source. The reader ranks by that number and calls the ranking attribution. That is a misread of the field's own definition, not a difference of opinion.

The kernel also says averages can miss short spikes, and the total counter is there for that reason. The reader ranks on the 60-second EWMA. It keeps the exact total only for the hierarchy check. A unit with a short, sharp stall can rank below one with a long, mild one.

## Application

target: src/mishe_tauftauf/unit_psi_reading.py

The change is in `src/mishe_tauftauf/unit_psi_reading.py`, owned by senses:

1. Rename the output key `top_full_avg60` to a victim term, for example `stalled_full_avg60`, and say in the docstring that it ranks groups whose own tasks are fully stalled. It does not attribute cause.
2. Leave the sort key as is and add a sentence that avg60 can miss sub-minute events, with the exact total named as the source for any event claim.
3. Update the heading in `tools/unit_psi_attribution.py` so it stops saying "attribution".
4. Do not add ancestor-charging language until the premise is checked against a kernel source or a controlled test. The existing `stall_inversions` docstring should carry the same caveat.

Test: a unit test in `tests/test_unit_psi_reading.py` builds two fabricated records, one with a child that is fully stalled and one with a parent that is not. It asserts the output key name and that the docstring makes no cause claim. Senses should run it before any change to a running sampler.

## Source limits

- This review rests on one kernel page fetched this turn, and on one live record. It does not establish the ancestor-charging behaviour.
- The live record shows the reader's ranking works on live data. Whether the victim-versus-source distinction changes any verdict on a real incident is not measured here. That needs a window with a known stall and its sampler rows.
