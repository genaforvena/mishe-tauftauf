---
field: engine-model-routing-and-usage
angle: metric
date: 2026-10-31
status: discarded
owner: genome
---

# A burn-rate metric needs a time series this plant does not keep

## What we read

- Google SRE Workbook, "Alerting on SLOs" (ch. 5), https://sre.google/workbook/alerting-on-slos/. Points used: the definition "Burn rate is how fast, relative to the SLO, the service consumes the error budget"; the multiwindow, multi-burn-rate table (Table 5-8: a 1-hour long window with a 5-minute short window at burn rate 14.4, and a 6-hour window with a 30-minute short window at burn rate 6, both for a 99.9% SLO); and the claim that the short window lets the alert stop firing soon after errors stop ("ceasing to fire five minutes later, rather than one hour later").
- The chapter's own limits: it gives a closed-form detection time only for single-window burn-rate alerts; the multiwindow approach is described by example; its cost is "Lots of parameters to specify".

## What this plant already does

- `src/mishe_tauftauf/engine_usage.py` reads one provider sample per account window: remaining fraction and `reset_ms`. `_window_verdict` (lines ~46-57) grades that sample FRESH, STALE or UNKNOWN by age, and rejects a reset that precedes the observation.
- `engine_usage.py` `_save_last_good` (lines ~151-160) overwrites `engine/last-good.json` with the latest samples on each save. The file is `{'version': 1, 'accounts': samples}`, one row per alias. No earlier sample is kept.
- `engine_policy.py` (`_classify_account`, lines ~119-165) classifies a window by its level only: `0 < eff < reserve_pct` is `reserve`, with `reserve_pct` 0.15 by default. It never reads a rate.

## What is new here

A burn rate is the SRE idea that maps onto this plant's quota windows: consumed fraction divided by elapsed fraction of the window. It would answer a question the level check cannot: whether a window at 40% remaining will last until its reset at the current pace. The mapping is clean, since each provider window is a fixed budget with a known reset.

Computing it, though, needs two or more timestamped fraction samples per window. The plant keeps one per alias, and it overwrites that one. From a single sample the rate is undefined, so the metric cannot be formed from current state without new persistence. That is the substantive finding: the metric is not missing from the policy's arithmetic; the history it needs is never written.

We did not measure the consequence live. Whether sampled windows would show a pace that the level check misses is an open question, and the answer would decide whether the history is worth keeping.

## Discard

Discarded: a burn-rate metric needs a per-window sample history, which `engine_usage.py` overwrites, so it is a new persistence feature, not a one-line application. Observation gap for genome: once history is kept, measure how often a `healthy` window reaches `reset` below the reserve line.

## Limits

- The SRE chapter's multiwindow alert is for error budgets of request traffic. Quota windows are a different kind of budget and may not behave the same.
- This is a discard, not a claim that rate-based routing is wrong. No route decision was replayed.
- Source code was read at the current checkout (HEAD d3c075ef); the running pin 91d5385 may differ.
