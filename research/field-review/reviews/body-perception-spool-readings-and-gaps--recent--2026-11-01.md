---
field: body-perception-spool-readings-and-gaps
angle: recent
date: 2026-11-01
status: discarded
owner: senses
---

# body-perception-spool-readings-and-gaps — recent (2026-11-01)

## What we read

- Jungo, Xiang, Gashi, Holz, "Representation Learning for Wearable-Based Applications in the Case of Missing Data", arXiv:2401.05437 (submitted 8 Jan 2024, revised 12 Jan 2024; accepted at the AAAI 2024 Human-Centric Representation Learning workshop). Source: https://arxiv.org/abs/2401.05437
- Read: the abstract and submission metadata, via the abstract page. The full text was not read in this turn; this review does not rely on any result beyond what the abstract states.
- Reported there: transformer-based imputation beats statistical baselines on signals that change frequently, not on monotonic signals; the study varies masking ratios, not gap lengths; the authors recommend hybrid imputation strategies.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py` never imputes a missing reading. `_gap_records` (around line 701) counts a malformed class-C halt record as bad and returns `None` for the whole file, so the caller reports UNKNOWN rather than a lower bound a reader could take for the whole history.
- `_dense_unknown` (around line 1033) is the single UNKNOWN shape for the dense baseline. The dense window is fixed at `DENSE_WINDOW_ROWS = 2000` and `DENSE_FRESH_SECONDS = 30.0` (around lines 838–845), and staleness is shown as freshness, not filled.
- `read_node_consumer_state` (around line 1392) says in its docstring that it "never repairs a malformed state and never infers human movement". The plant's stated stance is that a gap stays a gap.
- `_dense_light_class` (around line 1038) bins lux into four classes. It is a categorical summary of delivered rows, not an estimate of values that were not delivered.

## What is new here

The source's finding is that the value of filling a gap depends on how fast the signal changes. This plant has no fill step, so that dependence has no place to act. A dynamics-dependent policy would only matter if the plant chose to estimate missing body readings, and its docstrings say it does not. Adopting the finding would therefore mean adding imputation, which contradicts the plant's no-inference boundary.

## Discard

Imputation of missing wearable samples contradicts this plant's deliberate no-inference boundary in `body_perception.py`. The source's only transferable point, that gap effects depend on signal dynamics, needs an imputation step to act on, and the abstract reports masking ratios rather than gap lengths, so it cannot inform the UNKNOWN thresholds that do exist.

Re-open trigger: a senses decision to publish a reconstructed series from dense rows. Until then, the honest-UNKNOWN reading of gaps is the design.
