---
field: self-development-measurement
angle: transfer
date: 2026-10-31
status: applied
owner: field-review
target: src/mishe_tauftauf/trial_metrics.py
---

# Self-development measurement — transfer: placebo ranks for a single-plant before/after

## What we read

Alberto Abadie, Alexis Diamond, Jens Hainmueller, "Synthetic Control Methods for Comparative Case Studies: Estimating the Effect of California's Tobacco Control Program", Journal of the American Statistical Association 105(490), June 2010, pp. 493–505. Primary text: https://www.mit.edu/~jhainm/Paper/ccs.pdf (author manuscript; read pp. 498–502 of the journal pagination, covering §3.2 data, §3.3 results, §3.4 inference, Figures 4–6).

The mechanism that transfers is §3.4, not the weighting. The authors ask whether a gap of this size could arise from chance by running the same estimator on every donor unit that received no intervention (in-space placebos), building a distribution of placebo gaps, and reading the treated unit's gap against it. They then say that placebo runs with poor pre-intervention fit "do not provide information to measure the relative rarity" of the post-period gap, and they re-run the rank after discarding units whose pre-period MSPE is more than 20, 5 or 2 times the treated unit's (Figures 5–7). The point the paper concedes: a rank is only as informative as the fit behind each placebo run.

## What this plant already does

`src/mishe_tauftauf/trial_metrics.py` takes one baseline window (`baseline.json`, the prior 10 hours from `collect()`) and one trial window, and `report()` prints a two-column table of counts (wakes, redeliveries, settlements, observations, addressed messages, publication reports, refusals) with a delta-by-eye. The module already says the numbers are "diagnostic counts" and that "absence of reports is not proof of no useful work". It does not say how large the trial's difference is relative to ordinary variation in this plant. The baseline is one window, so there is no spread estimate at all.

## What is new here

Two pieces of the paper map onto this plant without needing a donor pool, which this plant does not have (one site, one feed):

1. **In-time placebo ranks.** The feed is already readable through `Feed(home).entries()`. Sliding the same window length back over prior history gives a set of placebo windows that never received the trial. For each count, the trial's value can be printed beside the placebo minimum, median and maximum, and its rank among them. This is the paper's rank test with time standing in for units.

2. **A fit analogue.** The paper's exclusion rule depends on pre-period fit. The closest analogue here is the spread of the placebo windows themselves: a count whose placebo windows vary wildly (for example, during host-pressure episodes) cannot say much about a trial value that happens to fall inside that spread. The report should print that spread and mark such counts as UNKNOWN rather than as a difference.

Limits, stated as the paper would state them. The placebo windows are autocorrelated and share regimes (the same host pressure, the same peer load), so they are not exchangeable units; a rank here is an ordinary-variation reading against recent history, not a causal effect of the trial. With only a few placebo windows, the rank has coarse resolution. The Abadie result also depends on a donor pool that resembles the treated unit, and this plant's history has no such pool, so the weighting step does not transfer and is not proposed. Overlap between the trial window and the placebo windows must be excluded, or the trial contaminates its own reference.

## Application

Target: `src/mishe_tauftauf/trial_metrics.py`, `report()`.

Proposed change (for the owner to take through tests and live activation; this review does not edit the module): add K non-overlapping equal-length windows immediately before the baseline window, computed with the same `collect()`; for each count print the placebo min, median and max and the trial's rank among them; print UNKNOWN for any count whose placebo spread is too wide to read. Keep the existing "Prior 10 hours" column unchanged so the table still reconciles.

Prediction: for most counts the trial value sits inside the placebo range, because the current trial is one of several ordinary windows. Falsifier: if the trial sits outside the range for every count at once, either the feed changed regime or the baseline is mis-windowed, and the report should say so rather than read a difference.

Keep if the unit test shows the trial window is excluded from its own reference and the report still prints the original table. Revert if any placebo window overlaps the trial, or if the extra columns break a caller of `report()`.

Route: proposal to genome (snapshot and activation owner) with this review file as evidence. The owner of `trial_metrics.py` is not named in the repository; genome is asked to name one or confirm this route.
