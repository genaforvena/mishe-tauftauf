---
field: freshness-and-staleness
angle: metric
date: 2026-11-02
status: applied
owner: senses
target: src/mishe_tauftauf/scan_freshness.py
---

## What we read

**Real-Time Status: How Often Should One Update?** — Kaul, Yates, Gruteser
(WINLAB, Rutgers; published IEEE INFOCOM 2012, DOI 10.1109/INFCOM.2012.6195689).
Read the full five-page authors' open copy:
<https://www.winlab.rutgers.edu/~gruteser/papers/sanjitnew.pdf>
(PDF and its `pdftotext` extract are preserved in `.mishe-tauftauf/artifacts/`).
Note: this cell's source is the 2012 paper where the metric is *defined*.
Module docstrings elsewhere cite "Yates et al. 2020" (the Kaul–Yates
tutorial). Same definitions, different venue; the "Eq. 2-3 / §II-B"
citations the plant already uses line-match the 2012 text.

The metric as defined by the paper:

- **Snapshot age** Δ(t) = t − u(t): "the status update age, which we will
  refer to as simply the age has t − u(t)" (§I), u(t) the time stamp of the
  status update most recently received. It grows linearly in the absence of
  updates and "is reset to a smaller value when an update is received" (§II;
  sawtooth, Fig. 2), the reset being Tᵢ = t′ᵢ − tᵢ, the age of update i at
  the moment the monitor receives it (also its system time in the queue).
- **Time-average age** (Eq. 1): Δ_T = (1/T) ∫₀ᵀ Δ(t) dt — "the area under
  the sawtooth function in Figure 2 normalized by the time interval of
  observation."
- **Per-interval decomposition** (Eq. 2–4): with Xᵢ = tᵢ − tᵢ₋₁ the
  inter-update period (Eq. 3), the i-th tooth-region area is
  Qᵢ = XᵢTᵢ + Xᵢ²/2 (Eq. 4). The tooth peak just before the reset is
  Δ⁽ᵖ⁾ = Xᵢ + Tᵢ — the §II-B quantity the plant's own `scan_window_aoi`
  docstring names "peak Δ⁽ᵖ⁾ §II-B".
- **Optimal update rate**: age is minimized at an interior generation rate,
  not the fastest possible one. M/M/1 optimal utilization ρ* ≈ 0.53 (server
  idle ≈ 47%, mean packets in system ≈ 1.13); M/D/1 ρ ≈ 0.625; D/M/1
  ρ = 0.515, and at that optimum random arrivals already pay ≈ +50% age
  versus deterministic ones. The headline: timely updating "is neither the
  same as maximizing the utilization of the communication system, nor of
  ensuring that generated status updates are received with minimum delay"
  (§I).

## What this plant already does

All three primitives of the metric exist — after the 2026-11-01 recent
review landed its fix:

* **Δ(t) with the right origin.** `scan_window_aoi()` builds the sawtooth
  from receipts' `acquisition.start.utc_ns`: "that instant is the sawtooth
  update u(t) (Yates Eq. 2-3, peak Δ⁽ᵖ⁾ §II-B), not the publish stamp"
  (`discovery.py:8916`). `current = now − u(t)` uses that origin.
* **Δ̄ time-averaged.** `aoi_summary()` returns `mean_age =
  E[Q]/E[D]` (`scan_freshness.py`), plus the §II-B peak statistics
  `mean_peak` (≈ E[D]), `p95`, `max`, `min`, and `n`, `window_seconds`.
  Warmup (<2 stamps) returns `None` — the same `unknown` vocabulary as
  `classify_age` and `_fresh`/`Undecidable`. Test-pinned in
  `tests/test_scan_freshness.py`
  (`test_windowed_aoi_counts_from_acquisition_start_not_publish`,
  `test_windowed_aoi_warmup_and_legacy_receipts_stay_unknown`).
* **The initial-age term is published.** `scan_initial_age()` emits
  `{median, max, n}` for `created − acquisition.start` (`discovery.py:8948`),
  and commit `bd5c5f43` (2026-11-01) switched stamps to acquisition starts.
* **Consumption is display-only.** The `aoi :` line is printed at
  `coverage-window-expiry.py:240–262`; no verdict consumes any AoI key.

And one deliberate approximation, named in the docstring: the mean computes
"E[Q]/E[D] with `Q = D²/2` when T folded, Eq. 2" — the 2012 formula with
Tᵢ ≡ 0.

Measurement on the live receipts (`.mishe-tauftauf/discovery/`, n = 100
intervals, ΣX ≈ 63940 s, folded vs unfolded from the same 101 receipts):

| quantity | folded (Qᵢ = Xᵢ²/2) | unfolded (Qᵢ = XᵢTᵢ + Xᵢ²/2) |
| --- | --- | --- |
| mean_age | 332.4 s | 410.3 s |
| Σ(XᵢTᵢ)/ΣX | — | +77.9 s (+23.4%) |
| landing-receipt T: median / p95 / max | 68.8 / 146.2 / 247.7 s | same |

## What is new here

The 2012 paper makes the folding choice *visible*. `aoi_summary` computes
Eq. 2 with `Qᵢ = Xᵢ²/2`, which is correct only when updates arrive with
zero age at the monitor (Tᵢ = 0, receive cost nothing). The recent review
repaired the stamp *and* measured the correction — median initial age 58 s
over a 200-receipt sample on 2026-11-01 — but only the *current* readout
and the published lag were corrected. **The mean Δ̄ still folds the
transport term.** Each interval contributes Xᵢ · Tᵢ to the mean by Eq. 4,
today's stream: Σ(XᵢTᵢ)/ΣX = 77.9 s of mean age in a number that prints
332.4 s. The same stamps produce a corrected 410.3 s — 23% higher, still
well inside any plausible reading of the gate table — and the paper's Eq. 4
is the exact formula for it.

Also read, and flagged as **not to be transferred**: the optimal-rate
theorems (ρ* ≈ 0.53 / 0.625 / 0.515). The scanners are rotation-scheduled
samplers behind scheduler + lease admission, not FCFS-backlogged sources;
"fastest is not freshest under service cost" warns rightly against naive
rotation tightening, but no measured service-facility statistic exists here,
so this review proposes no cadence formula.

## Application

target: src/mishe_tauftauf/scan_freshness.py

1. **Fold the transport term into the mean.** Extend `aoi_summary()` so a
   caller may pass per-receipt pairs `(stamp, T)` — T the initial age (for
   scan receipts: `created − acquisition.start`, exactly what
   `scan_initial_age()` already reports) — and compute each interval's
   contribution as Qᵢ = XᵢTᵢ + Xᵢ²/2 (Eq. 4) instead of Xᵢ²/2. Keep the
   stamp-only call path with the folded `Q = D²/2` formula untouched so
   existing callers and tests keep working; when pairs are passed, add the
   corrected keys (`mean_age_unfolded`, `T_median`, …) beside `mean_age`.
2. **Publish alongside, don't replace.** The existing keys keep their exact
   meanings — the publisher/reader split in `coverage-window-expiry.py` and
   in `scan()` snapshots is ledger-bright: a second reader recomputes from
   published fields alone. Adding keys never renames the ones live readers
   consume; the folded value stays published so readers can see the
   correction.
3. **Wire the per-receipt T where the stamps are read.**
   `scan_window_aoi()` already receives the `(started, created)` pairs from
   `read_scan_acquisitions()` and drops `_created`; the T is computable
   there with no new reads.

**Acceptance.**
- Unit test in `tests/test_scan_freshness.py`: the same stamp sequence with
  all-zero T vs all-40 s T reproduces today's two readings (folded 332.4 s
  vs unfolded 410.3 s over the live last-101 receipts) within one second.
- Replay the last 200 on-disk receipts: `mean_age_unfolded` ≥ `mean_age`,
  gap = Σ(XᵢTᵢ)/ΣX from the published per-receipt pairs, to within a
  second. `current`, `mean_peak`, `n`, `window_seconds` unchanged.
- Existing tests — the two `windowed_aoi` tests named above and the
  `coverage-window-expiry.py` `aoi :` print path — pass unchanged.

**Limits of this reading.**
- The plant is a periodic sampler; the paper's arrival-process models are
  hypotheses about its behavior, not verified properties. Eq. 4 is exact
  for FCFS; the plant's "queue" is scheduler admission.
- The unfolded mean prices acquisition time as monitor-side age — the same
  choice the origin fix made (u(t) = acquisition start). Readers that prefer
  Δ̄ over the publish path keep the folded value beside it.
- The optimal-rate results are not claimed for any plant schedule; no
  service-facility statistic exists to attach ρ to.

This sector is read-only with respect to the rest of the plant. The change
belongs to the owner of `src/mishe_tauftauf/scan_freshness.py`
(`discovery.py`, `feed.py`) — senses, who landed the recent review's origin
fix. This review is the input to that decision. Reading and measurement are
complete; nothing in the plant was edited.
