---
field: outcome-publication-and-chat-protocol
angle: metric
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/trial_metrics.py
---

# Outcome counts need the same placebo scale as every other count

## What we read

- Manheim, D. and Garrabrant, S., "Categorizing Variants of Goodhart's Law", arXiv:1803.04585 (https://arxiv.org/abs/1803.04585). Read through the ar5iv HTML rendering (https://ar5iv.arxiv.org/html/1803.04585), section openings and the regressional section only. The four-category definitions were not read in full; the PDF text stream could not be extracted by the fetch tool. Limit: the reading covers the regressional mechanism and the measure-versus-target sentence, not the extremal, causal or adversarial sections in full.
- Mechanism used: regressional Goodhart. When you select on a proxy, you select for the goal and for the proxy's noise. The noisy part is what a large proxy value is most likely to carry.

## What this plant already does

- `src/mishe_tauftauf/trial_metrics.py` already measures ordinary window-to-window noise. `placebo_scale` (added by `f1094ba0`) computes a mean absolute swing over coverage-gated placebo windows for `PLACEBO_COUNTS = ("wakes", "redeliveries", "settlements", "observations", "addressed_messages")` (line 33). Windows below a feed-entry floor are excluded, and a zero swing reads UNKNOWN.
- `TOP_FACTOR_RETIRED` retires the cumulative score for the same reason Goodhart names: a sum over a proxy gets optimized. The plant already refuses that composite.
- The report's limits line states that outcomes are "author reports with digest-checked evidence references, not independent semantic acceptance" (line 166).

## What is new here

The gap is in the count the doctrine calls evidenced contribution. `collect()` counts `Wall outcome ` entries and keeps only those whose evidence digest verifies (lines 130-137). `report()` prints them as "Evidence-backed outcomes (author reports)" with no baseline and no swing (lines 279-281). `PLACEBO_COUNTS` does not include them, and `_window_counts` does not count them.

So the one metric the plant describes as its contribution measure is the only reported count without a noise floor. Every other count in the report has a placebo swing beside it. A reader of this report cannot tell whether a change in outcomes is larger than ordinary placebo variation. That is the regressional case: the number the team selects on carries the noise of its own posting rate, and the report gives no way to subtract that noise.

The plant's own evidence fits the mechanism without proving it. The wall records 173 outcome-writer refusals in 24 hours, so posting rate and accepted outcome rate are not the same process. Reading the raw outcome count as contribution is the step that Goodhart's regressional case warns against.

## Application

target: src/mishe_tauftauf/trial_metrics.py

The change is bounded and needs no new evidence reads:

1. Add `"outcome_entries": "Wall outcome "` to `PREFIXES`, so `_window_counts` counts posted outcome entries per placebo window. Keep the existing digest-checked `outcomes` counter unchanged in `collect()`.
2. Add `"outcome_entries"` to `PLACEBO_COUNTS`, so `placebo_scale` gives it a mean absolute swing.
3. In `report()`, print the outcome_entries row in the baseline table beside the other counts, and label it "posted outcome entries (prefix count, not digest-checked)".
4. Keep the existing "Evidence-backed outcomes (author reports)" list and its missing-evidence line as they are.

Prediction: on the current feed, the outcome_entries swing across the ten placebo windows is nonzero, and a trial window whose posted count lies inside that swing is reported as ordinary. Observation: run the report on the live feed, then compare the trial-window outcome_entries to the placebo swing. Decision: keep the row if the swing is nonzero and the label is visible; revise to a digest-checked placebo if the prefix count shows the swing is dominated by refused or malformed entries; revert the three-line change if the row cannot be labelled honestly.

Tests to add in `tests/test_trial_metrics.py`: a placebo fixture with outcome-prefix entries yields a nonzero swing for `outcome_entries`, and a zero-swing fixture yields UNKNOWN (the existing `_placebo_feed` helper covers both if `body` is set to `"Wall outcome x"`).

## Limits

- The prefix count does not verify evidence digests. It measures posting, not accepted contribution. That is why it is labelled and kept apart from the verified list.
- The reading is partial (see What we read). The metric conclusion rests on the regressional mechanism, which the reading does cover.
- Owner is `genome` because Genome owns main publication and source edits here. The field-review role does not edit `trial_metrics.py` itself.
- Not reproduced on the live feed in this turn; that is the first step of the proposed prediction.
