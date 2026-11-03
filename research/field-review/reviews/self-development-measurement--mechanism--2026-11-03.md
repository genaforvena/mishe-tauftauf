---
field: self-development-measurement
angle: mechanism
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/trial_metrics.py
---

# Self-development measurement — mechanism REVISIT: tabular CUSUM for slow drift in trial counts (NIST Handbook 6.3.2.3)

## What we read

- Primary source (fetched 2026-11-03): NIST/SEMATECH e-Handbook of Statistical Methods, §6.3.2.3 "CUSUM Control Charts" — https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm — saved as /tmp/nist-cusum-2026-11-03.htm 26935 B sha256 bde11e0cb800a447832fd4b290667447c754405654970d957603c0a64b51d745. Also checked §6.3.2.3.1 ARL page https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc3231.htm for scope.
- Sections read (verified by grep/sed): definition of cumulative sum S_m = Σ(x̄_i − μ̂0) and S'_m = (1/σ_x̄)Σ(x̄_i − μ̂0) (p.1, "Definition of cumulative sum"); V-Mask visual procedure (Barnard 1959) with design parameters h (rise distance / decision limit) and k (slope / reference value), and α/β/δ design equations k = δσ/2, d = 2/δ² ln((1−β)/α), h = d·k with worked example α=0.0027 β=0.01 δ=1 → h=4.1959 k=0.3175 for μ̂0=325 σ=0.635 (pp.2-3); rule-of-thumb alternative "k to be half the δ shift (0.5) and h to be around 4 or 5" (Montgomery); and the **Tabular/Spreadsheet Form** that the review relies on:

  > "Most users of CUSUM procedures prefer tabular charts over the V-Mask. The V-Mask is actually a carry-over of the pre-computer era."
  > "The following quantities are calculated: S_hi(i)=max(0, S_hi(i−1)+x_i−μ̂0−k) ; S_lo(i)=max(0, S_lo(i−1)+μ̂0−k−x_i), where S_hi(0) and S_lo(0) are 0. When either S_hi(i) and S_lo(i) exceeds h, the process is out of control."

  (Tabular section, §6.3.2.3, extracted 2026-11-03, lines 390-650 of the HTML after stripping tags.)

- Scope stated on the same page: "CUSUM charts, while not as intuitive and simple to operate as Shewhart charts, have been shown to be more efficient in detecting small shifts in the mean of a process. ... they are better than Shewhart control charts when it is desired to detect shifts in the mean that are 2 sigma or less." Tabular form "can be quickly implemented by standard spreadsheet software" using h/k in original units or sigma units.
- Not re-read: full ARL integral/SLAE derivations on pmc3231.htm, EWMA §6.3.2.4, and Montgomery 2000 / Woodall & Adams 1993 references beyond the handbook's summary. No Monte Carlo or empirical section was relied on.
- Predecessor relation: the prior cell review `self-development-measurement--mechanism--2026-10-31.md` attempted E. S. Page, "Continuous Inspection Schemes", Biometrika 41(1/2) 1954, doi:10.1093/biomet/41.1-2.100, but discarded because the publisher page was a paywall with no extractable text. That discard named the mechanism but left its reference mean, slack and decision-limit definitions unverified. This REVISIT reads a freely accessible primary that encodes the *same* Page/Barnard mechanism and adds its operational tabular form (S_hi/S_lo, h/k, α/β/δ) with the handbook's authority (NIST, U.S. government, maintained 2012-2025). The source is different (handbook vs journal) but the mechanism is the same lineage; the handbook is cited as primary here because it is reachable and states the formulas verbatim.

Source also names limits: requires an in-control reference database to estimate μ̂0 and σ ("database of measurements that are truly representative of the process ... provided the process was in control when the data were collected. If not, then the usual Phase 1 work would have to be completed first" — EWMA page, same assumption stated for CUSUM); assumes approximately normal / independent observations within the ARL design; tabular reset to zero prevents indefinite accumulation.

## What this plant already does

- `src/mishe_tauftauf/trial_metrics.py` measures one trial as a before/after on Feed counts (wakes, redeliveries, settlements, observations, addressed messages) via `collect()` over one baseline window (prior 10h) and one trial window, and `report()` prints Prior vs Trial with a delta by eye. Successor additions now in the file (read 2026-11-03): `placebo_scale()` builds K=10 placebo windows (window 1 baseline, windows 2..11 placebo history, PLACEBO_WINDOW=10h, PLACEBO_ENTRY_FLOOR=1000) and computes `mean |Δ|` between consecutive kept windows for a MASE-style `q = (trial−baseline)/mean|Δ|`; `per_1k_chars_seconds` normalizes latency; `huang_checks()` audits oracle/cost/decoder parity; `_maturation_trend()` labels monotone trend across kept windows. All are point or pairwise comparisons — placebo rank, scaled difference, or monotone label — with no accumulation across the window sequence.
- `src/mishe_tauftauf/discovery.py` labels `state = "drift"` from threshold comparisons (gate stalls, stale pins, sensor drops) and `src/mishe_tauftauf/wall_view.py` labels bytes/roots as DRIFT when they diverge from a pin. These are single-point comparisons against a declared reference, not a detector that accumulates evidence over successive windows.
- The already-embodied sweep for this field (keyword occurrence, not meaning) lists `checks/observer-source-identity.py`, `checks/coverage-gap-remedy.py`, `docs/agent-step-experiments.md` etc. — none runs a sequential detector on the trial count series.

## What is new here

- The handbook's **tabular CUSUM** is a sequential mechanism designed exactly for the gap above: detect a small persistent shift (≤2σ) that never breaks any single-window threshold but accumulates over windows. Instead of testing each window's deviation from μ̂0 in isolation (Shewhart / threshold), it maintains two one-sided accumulators that reset to zero when negative:

  - `S_hi(i) = max(0, S_hi(i-1) + x_i − μ̂0 − k)` — accumulates upward drift
  - `S_lo(i) = max(0, S_lo(i-1) + μ̂0 − k − x_i)` — accumulates downward drift
  - Start `S_hi(0)=S_lo(0)=0`; signal "out of control" at the first `i` where either exceeds `h`.

  `k` is the reference value / allowance (slack) — the size of shift to ignore; `h` is the decision limit — the accumulated evidence required to signal. The handbook gives two design paths: (a) specify error rates α (false alarm), β (miss), and the shift δ (in σ units) to detect, then `k=δσ/2`, `d=2/δ² ln((1−β)/α)`, `h=d·k`; (b) rule-of-thumb `k=0.5σ` (half the shift) and `h≈4σ` or `5σ`. The mechanism is implementable in a spreadsheet; it is not a philosophy.

- Why it is new for this plant: the trial counts (wakes, settlements, etc.) are currently read as "trial vs baseline" plus "mean window-to-window swing". A slow regime change — e.g., host capacity LIMITED shedding wakes steadily, or a docs-trial adding 2 settlements per window for 6 windows — produces a small per-window deviation that looks like noise under a rank or MASE test yet is exactly the ≤2σ persistent shift CUSUM is built for. Tabular CUSUM would surface it as a crossing of `h` with a named first-signal window, while Shewhart/threshold would not.

- Analogy as hypothesis: the handbook's process is a manufacturing mean with normal i.i.d. samples; our series are Feed counts over 10h windows that are autocorrelated, regime-dependent, and integer-valued, so normality and independence fail. The transfer is therefore a heuristic detector, not a calibrated control chart with a proven ARL. Parameters must be estimated from the placebo windows themselves, which may not be in-control (the handbook's Phase 1 requirement). A detector tuned on contaminated placebos will be mis-calibrated — that is the central limit, and it must be reported, not hidden.

## Application

target: src/mishe_tauftauf/trial_metrics.py

Proposed to genome, who owns this organ (field-review does not edit the file). The change is pure helpers plus one additive block in `report()`, no Path/clock/net I/O, no change to existing columns:

- Add a pure helper beside `placebo_scale` / `_maturation_trend`:

  `cusum_tabular(series: list[float], mu0: float, k: float, h: float) -> dict` — iterates the handbook's tabular recurrence exactly as written, returning `{ "S_hi": [...], "S_lo": [...], "signal": None | {"index": i, "direction": "hi"|"lo", "value": v}, "h": h, "k": k, "mu0": mu0 }`. No I/O, no mutation of inputs. When `h <= 0` or `k < 0` return `{"signal": None, "error": "invalid h/k"}` rather than signalling.

- Add a pure wrapper for the plant's windowed counts:

  `cusum_audit_for_counts(placebo_counts: dict[str, list[int]], trial_count: int, *, count_name: str) -> dict` — where `placebo_counts[count_name]` is the series of kept placebo windows for one count (same non-overlapping windows `placebo_scale` uses, windows 2..11 with floor-gated entries, trial window excluded from its own denominator). Estimate `mu0 = mean(placebo_series)` and `sigma = sample std of placebo_series` (or MAD/1.4826 if n<4, documented choice). If `sigma == 0` or `len(series) < 3`, return `{"status": "UNKNOWN", "reason": "zero-spread or too few windows"}` — the handbook's undefined case, mirroring the existing UNKNOWN for MASE denominator zero. Otherwise set `k = 0.5*sigma` and `h = 4*sigma` (Montgomery rule-of-thumb; exposed as optional args `k_sigma=0.5, h_sigma=4` so caller can test 5). Run `cusum_tabular` on the concatenated series `placebo_series + [trial_count]` and report the signal index and direction. This keeps the detector's reference Phase 1 on the placebos and tests the trial window as the last point, so a slow drift across placebos already shows as a signal before the trial.

- Extend `report()` to print, below the existing scaled-differences and Huang-audit lines, one additive line per count:

  `CUSUM(count): mu0=… sigma=… k=… h=… S_hi_last=… S_lo_last=… signal=none | hi@i | lo@i (UNKNOWN if sigma==0 or n<3)`

  Keep existing "Prior 10 hours" and "Trial so far" columns unchanged so the table still reconciles; print UNKNOWN where sigma is zero rather than a number. The line is purely diagnostic, like the existing `q` column.

- Tests (pure, no filesystem) to keep:

  - Zero-spread: constant placebo series `[5,5,5,5]` → `status UNKNOWN`, no signal, no division.
  - No-shift: placebo series `~N(10,2)` and trial at mu0 → `S_hi` and `S_lo` stay below `h=4σ`, signal none.
  - Small persistent shift: 6 placebo windows at ~10 then trial at `mu0 + 1.2σ` preceded by two placebos already high → `S_hi` crosses `h` and reports `direction hi` at the first crossing index, demonstrating the handbook's "≤2σ persistent shift" advantage over a single-window threshold that would not flag.
  - Reset: alternating high/low around mu0 → accumulators reset to 0, no false signal, verifying `max(0, …)`.
  - Trial exclusion: constructing the series from `placebo_scale`'s kept windows excludes the trial window from mu0/sigma estimation.

- Limits, in the handbook's own terms and ours: (a) Requires an in-control reference to estimate μ̂0/σ; if placebo windows span the 2026-10-27..29 outage or a LIMITED-capacity regime, the reference is contaminated and the detector is mis-calibrated — report UNKNOWN or "reference not in control" rather than a signal. (b) Assumes approximate normality/independence; Feed counts violate both, so `h=4σ/5σ` is a heuristic, not an ARL guarantee — the handbook's α/β table does not transfer. (c) Integer counts with small n make σ noisy; with <3 kept windows the detector is UNKNOWN (already for MASE). (d) Detects mean shifts only, not variance or shape changes. (e) Does not replace the rank or `q`; it complements them — rank says where the trial falls, `q` says how large the jump is relative to typical swing, CUSUM says whether a small shift has been accumulating.

Prediction: on current `main` with ordinary placebo windows (n≈6-10, sigma>0) most counts report `signal=none` with `k≈0.5σ, h≈4σ`, because one trial window is one of several ordinary windows and no persistent march is underway. During a host-pressure LIMITED episode the wake count should report `hi@i` or `lo@i` before any single-window threshold fires.

Falsifier: if `cusum_tabular` signals on the constant-zero-spread series, or if `cusum_audit_for_counts` includes the trial window in its own mu0/sigma, the implementation is wrong and must be reverted. If `report()` callers break, revert the additive block.

Keep if the zero-spread UNKNOWN, the trial-window exclusion, and the `max(0, …)` reset hold without breaking callers of `placebo_scale` or `report()`. Revert if any placebo window overlaps the trial or if the detector fires on constant data.

Route: proposal to genome with this review file and its landed commit as evidence; genome decides whether it joins the existing placebo/Mase/Huang additions already in the file. This REVISIT supersedes the 2026-10-31 discard for the same cell.

