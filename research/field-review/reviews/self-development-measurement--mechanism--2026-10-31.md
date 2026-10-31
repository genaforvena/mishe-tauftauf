---
field: self-development-measurement
angle: mechanism
date: 2026-10-31
status: discarded
owner: field-review
---

# Self-development measurement — mechanism: sequential change detection (discarded, source unreachable)

## What we read

E. S. Page, "Continuous Inspection Schemes", Biometrika 41(1/2), June 1954, pp. 100–115, doi:10.1093/biomet/41.1-2.100 (cumulative-sum chart, the operational mechanism this cell asks about). Source: https://doi.org/10.1093/biomet/41.1-2.100, resolving to https://academic.oup.com/biomet/article-lookup/doi/10.1093/biomet/41.1-2.100.

The primary text could not be read. The publisher page returned a paywall ("You do not currently have access to this article", "only available as a PDF"), and the first-page preview is an image with no extractable text. No other copy of the paper was reached in this turn. Secondary summaries (Wikipedia's CUSUM entry, a citation index entry) were seen in a search, but a summary of a summary is not a reading, so nothing here is taken from them.

## What this plant already does

`src/mishe_tauftauf/` names drift as states and does not run a sequential detector on any measured series. `discovery.py` sets `state = "drift"` from threshold-style comparisons (gate stalls, runtime pins that go stale across sites, sensor sets the checkout drops), and `wall_view.py` labels bytes or import roots as `DRIFT` when they diverge from a pin. These are point-in-time comparisons against a declared reference, not an accumulation over a window. `trial_metrics.py` prints counts for a baseline and a trial window with a delta and no spread.

## What is new here

A cumulative-sum detector would accumulate small persistent shifts in the trial counts (wakes, settlements, refusals) and report when the accumulated deviation crosses a decision limit, so a slow drift that never breaks any single-window threshold would still surface. That is a candidate for `trial_metrics.report()`. Its parameters (reference mean, allowed slack, decision limit) are the part the discard depends on, and those are stated in the paper we could not read, so no parameter choice here would be grounded in the source.

## Discard

Primary text behind a paywall in this turn (publisher page, no readable first page); the mechanism's reference mean, slack and decision-limit definitions are unverified, so no application is proposed. Route the discard to genome with this file as evidence. Reopen this cell if an accessible copy of the paper is obtained, or if a freely accessible primary text of the same CUSUM definition is read instead.
