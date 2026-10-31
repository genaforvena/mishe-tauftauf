---
field: inference-effects-and-native-loop
angle: metric
date: 2026-10-31
status: discarded
owner: inference-research
---

## What we read

- Beyer, Jones, Petoff, Murphy (eds.), "Service Level Objectives", Site Reliability Engineering, Google, <https://sre.google/sre-book/service-level-objectives/>. Read via WebFetch (model-extracted page text, not a verbatim copy). Passages retrieved: an SLI is "a carefully defined quantitative measure of some aspect of the level of service that is provided"; availability is "often defined in terms of the fraction of well-formed requests that succeed, sometimes called yield"; an error budget is "a rate at which the SLOs can be missed". The retrieval did not return the good-events / valid-events phrasing, so this review does not cite it.
- The plant's own text, read from the working tree: `src/mishe_tauftauf/inference_effects.py` lines 76-90 (the outcome-already-exists and started-without-outcome contract) and lines 900-906 (`_unknown_intent` records `unreconciled-intent`). The failure labels `started-without-outcome` and `unreconciled-intent` already exist, and the module has no ratio, yield or fraction computed over them (grep for ratio, fraction and yield: no hit in the module).
- Live evidence checked: the `.mishe-tauftauf` site tree was listed for effect journals. Effect-journal records were not found in a form a read-only ratio could be computed from without first locating the store. Checks that touch effects (`native_recovery.py`, the effect tests) do not compute a rate.

## What this plant already does

The plant records each effect's terminal state explicitly. An operation is `completed` with a stored outcome, or `unknown` with a named failure (`started-without-outcome`, `unreconciled-intent`, `capability-failed`). A successor reads that state instead of inferring one. The label set is already a classification of effect outcomes, so the raw material for a metric exists in the design.

## What is new here

A yield-style metric would be the fraction of dispatched effects that end `completed` over those that were dispatched, with `unknown` counted separately as the error term. The source's framing (a success fraction over well-formed requests, with an error budget as the allowed miss rate) maps onto this directly. Its transfer here has a gap that the plant would have to close first: the denominator must count only dispatches that reached the effect path, not refusals or reservations that never started, or the rate mixes two different failures. The inference-effects code already separates these (reservation and `drop_reserved` versus `dispatch`), so the split is available in the design, but nothing measures it.

The metric would be a read-only report over the effect stores. It would not change dispatch, retry or the unknown-on-exception rule.

## Discard

No live effect journal sample was measured, so no baseline exists against which a yield or unknown-rate threshold could be judged, and the only source reading is a model-extracted summary of an SLI definition. Building the metric now would define a number with no observed value behind it. Trigger to reopen: a read-only count of dispatched effects by terminal state from one live effect store, with the reservation and drop path excluded from the denominator, showing that `unknown` outcomes occur at a rate worth budgeting. Route the measurement request to inference-research, which owns the stores.
