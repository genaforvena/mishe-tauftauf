---
field: engine-policy-route-decision
angle: recent
date: 2026-11-01
status: discarded
owner: genome
---

# A recent router predicts service time; engine_policy reads reported quota state

## What we read

- Liu, Zheng, Kong, Zhao, "QUARTZ: Quantile-Aware Routing and Queueing for TTFT SLOs in LLM Serving", Findings of ACL 2026, paper 1888. Source: https://aclanthology.org/2026.findings-acl.1888.pdf (also listed at https://preview.aclanthology.org/ingest-acl/2026.findings-acl.1888/). The PDF was read as text: title, abstract, §2.4 (objective, Eq. 3), §3.4 (decision rule, Alg. 1 step 8, Eq. 5-8), §3.5 (calibration and fallback), §4.2 (predictor), §4.3 (admission queue), Table 6-8 setup, and the Limitations text.
- Points used: the router picks the worker minimising the α-quantile predicted completion, with α = 0.95 (Eq. 5, Table setup); the admission queue is keyed by an aged, fairness-adjusted size proxy (Eq. 6-8); under-coverage of the quantile predictor is monitored by residuals and, past a threshold δ = 0.05, traffic shifts to a baseline (FIFO admission with least-loaded routing) (§3.5); telemetry is consumed "opportunistically without blocking" (§4.1); the paper's own limitations are that predictor errors under abrupt workload shift "can reduce routing quality", that only TTFT tail is optimised, that only the tested engine and hardware were evaluated, and that extreme overload forces a trade-off between tail SLO protection and per-tenant fairness.
- The paper's text contains no mention of hysteresis, hold or stickiness on routing choices (checked by search of the full text).

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` is a pure admission function over reported quota state, not a predictor. Each FRESH account window is classified healthy, reserve or depleted from `remaining_fraction` minus reservations against a fixed `reserve_pct` (0.15 default; module docstring).
- Missing or stale evidence is not read as exhaustion: UNKNOWN, STALE, UNATTRIBUTED and NOT_CONFIGURED produce `hold` and are ineligible for new admission (`_INELIGIBLE_STATES`, `_classify_account`).
- Hysteresis is explicit: a current compatible route is kept (`stay`) for `min_hold_ms` (default 600 000 ms) unless it hard-fails.
- Same-provider alias is preferred before cross-provider fallback. No quality judgment enters the decision ("no headroom percentage decides model suitability").

## What is new here

Nothing that changes this plant's decision rule. The two systems choose on different quantities:

1. QUARTZ routes on a predicted per-request service time (a quantile of prefill cost from prompt length, cache signals and decode parameters). engine_policy routes on a provider-reported remaining fraction that it does not predict. There is no predictor in the engine path, so there is no quantile to set and no under-coverage residual to monitor.
2. QUARTZ's fallback is a residual-triggered shift to a baseline when the predictor under-covers. The plant's analogue of a bad input is already handled differently: a stale or unknown account holds admission, which is the right response to missing evidence and is not a fallback to a second policy. Copying QUARTZ's δ-triggered fallback would turn a hold into a baseline choice the plant has not evidenced.
3. QUARTZ's own caveat (prediction error under shift degrades routing) applies to a model the plant does not have. The plant's inputs are provider-reported state, whose failure mode is staleness, and the plant already gates on that.
4. Hysteresis is absent from QUARTZ, so nothing in the paper argues against the plant's `min_hold_ms`.

The one observation this reading suggests (whether the 0.15 reserve held in practice) is a measurement on the plant's own decision log. It is not a change to engine_policy and is not a result of this paper, so it is not filed here.

## Discard

QUARTZ predicts per-request TTFT quantiles to route; engine_policy admits on reported quota fractions with explicit hold on missing evidence, so the paper's mechanism has no receiver in this route decision.
