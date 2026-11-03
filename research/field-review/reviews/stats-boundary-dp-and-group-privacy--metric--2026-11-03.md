---
field: stats-boundary-dp-and-group-privacy
angle: metric
date: 2026-11-03
status: discarded
owner: senses
---

# stats-boundary-dp-and-group-privacy — metric

## What we read

- Saeed Mahloujifar, Luca Melis, Kamalika Chaudhuri, “Auditing *f*-Differential Privacy in One Run,” arXiv:2410.22235v1 (29 October 2024), Sections 2–3, especially Definition 7. Source: https://arxiv.org/html/2410.22235v1.
- The metric is empirical privacy: after a randomized guessing game yields observation `o`, retain the maximal trade-off curves whose audit accepts, then derive an observation-dependent `ε(δ)` curve (Definition 7). The paper requires a provably small false-negative probability for mechanisms satisfying the hypothesis; empirical rejection on non-private mechanisms is useful but is not a proved false-positive bound. Auditing multiple `(ε, δ)` points independently can inflate rejection probability; the paper instead analyzes the whole `f`-DP curve. Its measurement requires a randomized mechanism and a game that controls or knows the input.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` defines typed DP calibration helpers, but says they are not an operating mechanism. `query()` performs read-only exact SQLite aggregates; it does not randomize output, accept canary inputs, or provide a mechanism for an auditor to run. The previous metric review tested one-run canary auditing and reached the same applicability boundary; this reading adds a richer `f`-DP curve metric, not a new subject to measure.

## What is new here

The paper's concrete measure is an observation-conditioned *curve* of empirical `ε(δ)`, rather than a single scalar `ε` estimate. Its acceptance guarantee is one-sided: audit power for a truly compliant mechanism is bounded, while an observed rejection is evidence from the experiment, not a general certificate that every noncompliant mechanism will be caught. Neither distinction makes a metric applicable to an exact, non-randomized aggregation path.

## Discard

There is no randomized stats release or controllable input for this audit to measure; revisit when `query()` or a successor exposes a noisy mechanism.