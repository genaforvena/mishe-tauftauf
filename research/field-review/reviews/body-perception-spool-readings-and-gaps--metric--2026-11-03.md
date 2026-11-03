---
field: body-perception-spool-readings-and-gaps
angle: metric
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-spool-readings-and-gaps — metric (2026-11-03)

## What we read

- Beyer, Jones, Petoff, Murphy (eds.), *Site Reliability Engineering: How Google Runs Production Systems*, Chapter 4 "Service Level Objectives," https://sre.google/sre-book/service-level-objectives/ — fetched 2026-11-03 as HTML 67,930 bytes (headers `last-modified: Thu, 08 Oct 2026 04:48:00 GMT`, `content-type: text/html`) and retained locally as `/tmp/sre-slo.txt` (12,000 chars extracted from `<div class="content" id="content">`). Read §§ Service Level Terminology, Indicators, Objectives, Agreements, Indicators in Practice (§§ Collecting Indicators, Aggregation) in full. Verbatim passages relied on:
  - SLI: "An SLI is a service level indicator—a carefully defined quantitative measure of some aspect of the level of service that is provided." Common SLIs: "request latency—how long it takes to return a response to a request," "the error rate, often expressed as a fraction of all requests received, and system throughput, typically measured in requests per second. The measurements are often aggregated: i.e., raw data is collected over a measurement window and then turned into a rate, average, or percentile."
  - Availability: "often defined in terms of the fraction of well-formed requests that succeed, sometimes called yield" (§Indicators), with near-100% expressed as nines.
  - SLO: "An SLO is a service level objective: a target value or range of values for a service level that is measured by an SLI. A natural structure for SLOs is thus SLI ≤ target, or lower bound ≤ SLI ≤ upper bound."
  - Error budget: "it is better to allow an error budget—a rate at which the SLOs can be missed—and track that on a daily or weekly basis" and "The SLO violation rate can be compared against the error budget … with the gap used as an input to the process that decides when to roll out new releases."
  - SLI selection: "You shouldn't use every metric you can track in your monitoring system as an SLI; … a handful of representative indicators are enough."
  - Aggregation caution: averaging hides bursts — "Consider a system that serves 200 requests/s in even-numbered seconds, and 0 in the others. It has the same average load as one that serves a constant 100 requests/s, but has an instantaneous load that is twice as large as the average one."

## What this plant already does

- `src/mishe_tauftauf/body_perception.py:111` defines freshness as a point check: `FRESH_SECONDS = 30.0`; `src/mishe_tauftauf/body_perception.py:1229` `DENSE_FRESH_SECONDS = 30.0` (same). `read_redmi10_dense_baseline` (2100–2212) computes `break_seconds = now - last_epoch` and verdicts `verified` if `< DENSE_FRESH_SECONDS` else `stale` — a single-point comparison, not a ratio over a window.
- `GAP_WINDOW_SECONDS = 86400.0` (1038) and `read_redmi10_delivery_gaps` (1151–1207) report `gaps_24h = len(window)` and `max_receipt_lag_s` over the last day — a windowed count and a worst span, not a success fraction or availability ratio; no `good/total` SLI, no SLO target fraction, no error budget typed. `rg -n "error.budget|burn.rate|SLI|SLO|SLO_" src/mishe_tauftauf/body_perception.py` finds only this kind of prose in prior reviews — no computed ratio.
- The dense window itself is `DENSE_TAIL_BYTES = 262144` (~2500 rows, ~3.5 h at 5 s) / `DENSE_WINDOW_ROWS = 2000` (~2.8 h) — a fixed tail, not a measurement window for a rate. Sequence accounting (`_dense_sequence_accounting` 2059–2092, `expected/received/lost/dup/unparsed/steps_back`) counts rows, not time-weighted availability.
- Prior metric for this same cell (2026-11-01, RFC 3550) proposed a per-batch jitter `D=(Rj-Ri)-(Sj-Si)` with smoothed `J += (|D|-J)/16` and `max_abs_d_s` over 244 batches (median |D| 1.1 s, max 1335.4 s, J 83.0 s) — a burstiness metric, orthogonal to a windowed availability ratio.

## What is new here

The SRE chapter's metric move is to recast a point/count measure as an SLI ratio over an explicit window, with an SLO threshold and an error budget, and to treat aggregation carefully.

1. **SLI as ratio, not point or count.** The chapter's canonical form is a fraction over a window — "fraction of well-formed requests that succeed" (yield), or error rate as fraction, with raw data "collected over a measurement window and then turned into a rate, average, or percentile." For this spool that would be windowed freshness availability, e.g. `SLO_window_freshness = good_seconds / window_seconds` where a second counts as good when `now - last_epoch_at_that_second < 30.0`, or equivalently `SLO_window_rows = received / expected` over `GAP_WINDOW_SECONDS` using the sequence accounting already present. The SLO is then `SLI ≥ target` (e.g. 0.99 over 28 days), and the error budget is `1 - target` of the window consumed at the observed violation rate — the vocabulary the chapter uses to gate releases ("gap used as an input to … when to roll out new releases"), not just to describe state.

2. **Why a point gate alone is insufficient — the aggregation warning.** The chapter's burst example (200/s even, 0 odd → same average 100/s as steady 100/s, but 2× instantaneous load) maps directly onto the frozen tail this plant already measured: the RFC 3550 frozen window showed one 1335 s deviation dominating smoothed J while median |D| was 1.1 s. A single `break_seconds` at the tail hides that shape exactly as an averaged request rate hides bursts. A windowed ratio (`fraction of window fresh`) alongside a percentile (e.g. p99 break, or p95) would preserve it; a single point does not.

3. **Handful of SLIs, not every trackable number.** The chapter warns against promoting every metric to an SLI. This plant already tracks ~10 numbers beside the dense window (break_seconds, run spans, expected/received/lost/dup/steps_back, gaps_24h, max_receipt_lag). The SRE discipline would pick one or two windowed ratios as the SLI (e.g. freshness availability over GAP_WINDOW_SECONDS and optionally row delivery ratio over DENSE_WINDOW_ROWS) and keep the rest as diagnostics, rather than adding a new SLI per diagnostic.

Distinct from prior metrics for this field: RFC 3550 jitter measures per-batch spacing unevenness (D/J/max|D|) at batch granularity — this measures fraction-of-window success as availability; from recent/misread REVISITs: Li et al. AoI Δ(t)/threshold (sampling-dependent effectiveness), Lamport partial-vs-total ordering / common-clock, Tail at Scale p99 amplification, and Schafer MAR complete-case — none names an SLI ratio / SLO target / error budget.

## Application

Proposal to `senses`, who own `body_perception.py`. Read-only from this role; no edit made here.

target: src/mishe_tauftauf/body_perception.py

Bounded change — docstring-only boundary note beside `DENSE_TAIL_BYTES` / `DENSE_WINDOW_ROWS` / `DENSE_FRESH_SECONDS` / `FRESH_SECONDS` / `GAP_WINDOW_SECONDS` and beside `read_redmi10_dense_baseline` / `read_redmi10_delivery_gaps` (around lines 1038, 1112, 1225–1300, 1950–2212), complementary to the existing Narayanan & Shmatikov / k-anonymity / Pufferfish / Wasserstein/MQM / El Emam k-map / RAPPOR / RFC 3550 jitter / RFC 9002 / Tail at Scale / Lamport / Li AoI / Spanner TrueTime notes:

- Cite Beyer et al., *SRE* Ch.4 "Service Level Objectives" (https://sre.google/sre-book/service-level-objectives/) §§ Indicators/Objectives/Aggregation, with the verbatim SLI/SLO/error-budget definitions above and the burst-average example.
- State that the current measures beside this window — `break_seconds < 30.0` point gate, `gaps_24h` count, `max_receipt_lag_s` worst span, sequence `received/expected` — are not SLI ratios and carry no SLO target fraction or error budget; no `good/total` over an explicit measurement window is published.
- Name what would be needed if a windowed SLI is ever wired: numerator/denominator definitions, measurement window (e.g. GAP_WINDOW_SECONDS vs a 28-day SLO window), target threshold (e.g. `SLI ≥ 0.99`), error-budget accounting and caller that gates any release or polling change on remaining budget, and aggregation choice (mean vs p99 vs time-weighted) per the chapter's aggregation caution. Until then, the 30.0 s gate remains a point target, not an SLO in this chapter's sense.

Complements (not edits) the 2026-11-01 RFC 3550 jitter proposal — jitter diagnoses burstiness per batch pair, this SLO ratio diagnoses availability over a window — and all prior notes beside this window. Revertible by `git revert`; no verdict/behaviour change.

## Limits

- The SRE chapter describes SLO practice for request-serving and storage systems; retargeting "fraction of well-formed requests that succeed" onto phone spool freshness is an analogy, not a claim that a carried device is a serving system. Clock-domain divergence (Lamport 1978 note: phone epoch vs host `time.time()` without Tm/C1-C2, 100 s max lag observed) still applies — any time-weighted availability ratio inherits that divergence.
- The burst example is illustrative; the plant has not computed `good_seconds/window_seconds` on a live window, only the frozen RFC 3550 tail (244 batches, 243 pairs). Any future SLI must state its denominator window explicitly — GAP_WINDOW_SECONDS 86400.0 is a count window, not necessarily the SLO window.
- Error-budget gating of releases is quoted verbatim; this plant has no release gate wired to body-perception state, and adding one would need the threshold, window, and caller named explicitly.
