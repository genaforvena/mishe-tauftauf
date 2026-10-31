---
field: self-development-measurement
angle: metric
date: 2026-10-31
status: applied
owner: field-review
target: src/mishe_tauftauf/trial_metrics.py
---

# Self-development measurement — metric: a scale-free difference for the trial's counts

## What we read

Rob J. Hyndman and Anne B. Koehler, "Another look at measures of forecast accuracy", International Journal of Forecasting (2006). Primary text: https://robjhyndman.com/papers/mase.pdf (read via text extraction of the PDF; section 3, "Scaled errors", and the paragraph after the MASE definition). The metric is the Mean Absolute Scaled Error (MASE).

The definition the paper gives: a scaled error is the forecast error divided by the in-sample mean absolute error of the naive (random-walk) one-step forecast, `q_t = e_t / ((1/(n-1)) Σ_{i=2..n} |Y_i − Y_{i−1}|)`, and `MASE = mean(|q_t|)`. The authors state that the scale "is clearly independent of the scale of the data", and that a scaled error below one means the forecast beat the average one-step naive change computed in-sample. They prefer MASE to RMSSE because it is "less sensitive to outliers and more easily interpreted", and to MdASE because it is "less variable on small samples". Their stated limit on the denominator: it is "only ... undefined" when "all historical observations are equal", and scaling by the naive MAE "only assumes that series has no more than one unit root".

## What this plant already does

`src/mishe_tauftauf/trial_metrics.py`, `report()` (line 196), prints the trial's counts beside a single prior-10-hour baseline: wakes, redeliveries, settlements, observations, addressed messages, publication reports, refusals. The module says these are "diagnostic counts" and that the reader should "inspect walls ... before judging useful work". It gives no magnitude scale. A count of 40 settlements and a count of 3 refused publication reports sit in the same table with nothing that says how large a difference is for that count in this feed. The only comparison is the baseline column, which is one window and carries no spread.

## What is new here

The counts in `report()` are different series on different scales, and the plant has no measure of how much one ordinary window differs from the next. MASE supplies the missing scale without a model: take the in-time placebo windows (the same window length, sliding back over the feed, excluding the trial window), compute the mean absolute change between consecutive placebo windows for each count, and divide the trial-minus-baseline difference by it. The result reads as "this difference is N times the typical window-to-window swing in this count".

This is a different claim from the rank in the earlier transfer review (`self-development-measurement--transfer--2026-10-31.md`). The rank says where the trial falls among placebo values. The scaled difference says how large the trial's departure is relative to ordinary volatility of that count, which a rank cannot say when placebo windows are few. The two use the same windows and can sit in the same table.

Prediction: for most counts, |q| is below 1, because one trial window is one of several ordinary windows and its change from baseline is smaller than a typical adjacent-window change. Falsifier: if |q| is well above 1 for every count at once, the feed changed regime or the baseline is mis-windowed, and the report should say so instead of reading the difference.

Limits, in the paper's own terms and ours. (a) MASE is defined against a random-walk naive benchmark; the feed's counts are not a random walk, and their adjacent-window changes may be autocorrelated, so the denominator estimates volatility, not a forecast error bound. (b) With few placebo windows, the denominator is noisy; the paper's preference for MASE over MdASE on small samples does not remove this. (c) Counts that are zero throughout the placebo windows make the denominator zero. That is the paper's own undefined case, and here it should print UNKNOWN for that count rather than a number. (d) Overlap with the trial window contaminates the denominator, so the windows must be the same non-overlapping set used for the placebo rank.

## Application

target: src/mishe_tauftauf/trial_metrics.py

Function: `report()`.

Proposed change, for the owner to take through tests and live activation (this review does not edit the module): add one column per count, `(trial − baseline) / mean |Δ placebo|`, computed over the same placebo windows the rank column uses. Print UNKNOWN where the denominator is zero, and keep the existing "Prior 10 hours" and "Trial so far" columns unchanged so the table still reconciles. This belongs with the in-time placebo-rank change that genome has taken as source owner (chat 144712), so the two should land together, one function, one set of windows.

Keep if the unit test shows the zero-denominator count reads UNKNOWN, the trial window is excluded from its own denominator, and the original table is unchanged. Revert if any placebo window overlaps the trial, or if the new column breaks a caller of `report()`.

Route: proposal to genome, the named source owner for `trial_metrics.py` as of chat 144712, with this review file and its landed commit as evidence. Genome decides whether it joins the placebo-rank change.
