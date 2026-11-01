---
field: unit-psi-reading-verdict
angle: metric
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/unit_psi_reading.py
---

# unit-psi-reading-verdict — metric (2026-11-01)

## What we read

- Primary source: Linux kernel documentation, "Pressure Stall Information (PSI)", https://docs.kernel.org/accounting/psi.html (fetched this turn).
  - Quoted: the averages are "tracked as recent trends over ten, sixty, and three hundred second windows"; the total is "The total absolute stall time (in us) is tracked and exported as well", and it "allows detection of latency spikes which wouldn't necessarily make a dent in the time averages".
  - "some" is "the share of time in which at least some tasks are stalled on a given resource". The page gives no formula for the averages and no rounding rule.
  - The metric the kernel defines in its own terms is therefore stall time divided by wall time over a chosen window. The cumulative total is the exact input; the averages are display trends.
- Plant evidence, read from source and from the live sampler file (`.mishe-tauftauf/operator/unit-psi.jsonl`), not from the docstring:
  - `read_unit_psi()` computes `span` = newest `at` minus the earliest in-window anchor `at` (live: 241.0 s, 4 earlier records in the 300 s window). It computes `stall_us[name]` = exact `psi.some.total` delta over that same span.
  - The sample line prints the stall delta only for the unit with the largest high-event delta: `some-stall=` is taken from `top_high[0][0]`, never from the largest stall delta.
  - Live run of the reader (this turn): `top-high-delta=mishe-lifeline.slice:+1/241s some-stall=+0.1ms`. The largest exact `some.total` delta in the same window is `mishe-plants.slice/mishe-self-development-current-discover.service` at +1282 us (+1.28 ms), and it is not printed.
  - The top-some field is `max(unit_rows, key=avg10)`, which is the first zero in input order when every avg10 is 0.00 (`top-some=mishe-plants.slice:0.00`). That is the same tie the zero-tie change fixed for `full.avg60` (commit 64ae1331), still present for `some.avg10`.

## What this plant already does

- The reader already holds the exact measure: `_some_total_us()` returns the integer counter, and `stall_us` is a window delta over the true span. It already refuses to attribute stall when the hierarchy check fails (`some-stall=unknown(hierarchy-inverted)`), and it prints the span beside the high delta.
- The stall figure, however, is bound to the high-event leader. A stall leader that has no high event is invisible in the sense.
- `full.avg60` remains the ranking key for `top_full_avg60` (mechanism review, same cell). Its zero ties are counted, not published.

## Consistency check on the live file

The metric is only worth publishing if the exact share agrees with the average where both are nonzero. Measured this turn on the newest record against the previous record (span 60.16 s, 95 units compared):

- 2 units had any nonzero value. Both have exact share 0.0000% and 0.0001%, and `some.avg60 = 0.0`. Rounding agrees with the exact share here.
- This is one quiet minute, so it does not test the disagreement case. It shows the metric and the average are consistent when pressure is near zero. It does not show what happens during a burst.

## What is new here

The metric to publish is the kernel's own definition, computed on the exact counter:

    window_share_pct(unit) = 100 * (some.total_now - some.total_anchor) / (1e6 * span_s)

with `span_s` taken from the record `at` values, not the nominal window. Three consequences follow from the definition, and each is a concrete change to the sample line rather than a new dashboard:

1. The stall leader is the unit with the largest exact `some.total` delta in the span, reported as its share and its span, for example `top-stall=<unit>:0.0005%/241s`. The current line can report a stall that is not the leader.
2. The share is printed as a percent with enough digits to show a nonzero value. The current `+0.1ms` depends on a reader who knows the span. At 4 decimals the leader here would print `0.0005%`, not `0.0000`, so the printed precision must be stated.
3. The share is UNKNOWN when `span_s` is zero or the anchor is missing, not 0%. This keeps the "no window" case out of the calm reading, which the reader already does for high deltas (`high-delta=unknown(no-window)`).

Limits, stated before the change:

- A window share below 1e-4 % will look like zero to a human reader in any short window. Adding the stall leader makes the metric visible; it does not make small stalls large. The honest reading at present is that the plant had almost no stall in the window.
- The share is computed per sampling interval (about 60 s), not per the 10 s and 300 s windows the kernel's trigger uses. The 300 s anchor span is wider than the kernel trigger window, so a 1 s burst is diluted by up to 300 times. Treat it as a trend over the span, not an event detector. The kernel doc itself says the total is what shows short events; the share does not.
- The hierarchy check from 56ed68a8 is skew-sensitive (its own docstring says so). A stall leader under `hierarchy-inverted` must print `unknown(hierarchy-inverted)`, as the existing high-leader line already does.

## Application

target: src/mishe_tauftauf/unit_psi_reading.py

Proposed change for senses (the owner; field-review does not edit the organ):

1. In `read_unit_psi()`, after `stall_us` is built, pick the unit with the largest positive `stall_us` delta and print `top-stall=<unit>:<share:.4f>%/<span:.0f>s` in the sample. Print `top-stall=unknown(no-window)` when `span` is 0 and `top-stall=unknown(hierarchy-inverted)` when that unit is in `inverted`.
2. Keep `some-stall=` for the high leader for compatibility with the existing tests; do not remove it in this change.
3. Tests in `tests/test_unit_psi_reading.py`: (a) a fixture where the stall leader differs from the high leader, asserting `top-stall=` names the stall leader; (b) a single-record fixture asserting `top-stall=unknown(no-window)`; (c) the 1282 us / 241 s case, asserting the share prints as `0.0005%`.
4. Verify on the live file with the reader call shown in the What we read section: the `top-stall` text should name `mishe-plants.slice/mishe-self-development-current-discover.service` at this record, or the record that replaces it. Live activation is genome's; the sample text is not live until a snapshot activates it.

Disposition: senses keeps or revises this change. If the 4-decimal precision proves too coarse, revise it to ppm rather than dropping the stall leader. A discard would be justified only if senses shows the high-event leader is the only stall signal the plant reads, which the live sample does not support.

## Routing

Route to senses by addressed message with this review file as evidence.
