---
field: freshness-and-staleness
angle: recent
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/scan_freshness.py
---

## What we read

**From Freshness to Effectiveness: Goal-Oriented Sampling for Remote Decision Making** — Aimin Li, Shaohua Wu, Gary C. F. Lee, Sumei Sun. arXiv:2504.19507v3 (v1 28 Apr 2025, v3 6 Feb 2026, 42 pp). <https://arxiv.org/abs/2504.19507> PDF <https://arxiv.org/pdf/2504.19507.pdf>. Retrieved 2026-11-03 as /tmp/freshness-recent-250419507.pdf 5081551 B (42 pages, arXiv GenPDF). Read: Abstract, §I Introduction (§I-A motivation "fresher holds greater value" heuristic, §I challenge "freshness alone does not determine utility"), §II-B joint sampling/decision formulation Problem 1 (eq. 10a–10b sampling frequency constraint 1/fmax), §VI–VII sensitivity analysis and Theorem 9 threshold fmax^T. Skimmed: §IV-V QUICK BLP two-stage algorithm (Dinkelbach root + LP) and ONE PDSI convergence O(R1^K), §VI-B-C ALP scaling; no claim relied there.

Core claim: information freshness (AoI) and decision effectiveness diverge. The paper co-designs sampling and decision in an Age-aware Remote MDP (AR-MDP) where state evolves under held action a_t = A_i on [D_i, D_{i+1}) and delay Y_i is random. The objective is long-run average cost E[C(X_t,a_t)] subject to average sampling rate ≤ fmax. Theorem 9 gives a threshold fmax^T ≜ 1/(F(ρ*) ) such that (i) if fmax ≥ fmax^T, the optimal cost h* is independent of fmax (extra sampling yields no gain), (ii) if fmax < fmax^T, h* = fmax·Q*(fmax) is monotone non-increasing with derivative dh*/dfmax = U*(λ*). Past AR-MDP literature minimized AoI; this paper shows that is the wrong objective when C(·) depends on semantic content and source dynamics — "some stale data may be more valuable than fresh updates" (Abstract).

## What this plant already does

* **Age is an interval, not a point, for scans.** `src/mishe_tauftauf/scan_freshness.py` `age_bounds()` (lines 107–121) brackets a scan receipt's whole-acquisition age as `[min,max]` ns from `CLOCK_BOOTTIME` endpoints; `classify_age()` (124–148) answers `recent`/`stale`/`unknown` where `unknown` is bounds straddling the threshold. Header: "UTC is diagnostic only and never establishes recency." This is the correct LeaseGuard-style separation referenced in the 2026-10-22 REVISIT.
* **Windowed AoI is measured but display-only.** `aoi_summary()` (150–294) computes Yates §II `Δ(t)=t−u(t)` sawtooth summaries `current`, `mean_age=E[Q]/E[Y]` (Q=D²/2, Eq. 2), `mean_peak`, `p95`, `window_seconds`, and since 2026-11-02 supports unfolded `Q_i=X_i·T_i+X_i²/2` (Kaul-Yates Eq. 4) via `lags`/`t_median`/`mean_transport`. Used by `checks/coverage-window-expiry.py:240` for the `aoi :` line only; no verdict consumes it.
* **Sampling is open-loop and AoI-centric.** Scans are periodic and the standing checks treat freshness as a binary threshold (`_fresh`, `classify_age`, lease `[0,20]s` in `coordination/site_sync.py`). There is no model of downstream decision cost C(X_t,a_t), no sampling-rate constraint fmax, and no statement of where additional sampling stops helping. The 2026-11-01 REVISIT measured initial age at publish as 30–1923 s (`created−acquisition.start` median 58 s), so windowed AoI from `created` already understates age; it did not ask whether that age matters to the decision.
* **The consumer decision exists but is not linked to age.** Each check (coverage-gap, window-expiry, evidence-binding) makes a routing decision based on the published reading; the scan that produced the reading evolves the underlying host state while the held verdict persists — exactly the holding-action paradigm (A1) the paper models (Remark 1: robotic teleoperation, UAV coordination, industrial supervisory control).

## What is new here

1. **Freshness ≠ effectiveness.** The plant optimizes for small AoI (fresher scan wins) as if that were the same as better routing. Li et al. proves it is not: minimizing AoI can waste sampling budget when the source is slow-moving or the stale datum already encodes the decision-relevant mode (e.g., a host that has been in the same `sense.*` state for minutes). The AR-MDP formalizes the joint problem: choose sampling instants S_i (waiting Z_i ∈ Z) and action A_i based on (X_{S_i−Δ}, Δ, u_{−1}, a_{−1}) to minimize C, subject to rate fmax. The plant has the first half (sampling) and the second half (verdict) but never writes them as one problem.

2. **A threshold sampling frequency.** Theorem 9's fmax^T is not a tunable constant; it is derived from the unconstrained optimum ρ* via F(ρ*). Beyond it, sampling faster does not lower decision cost — the curve is flat. This complements the two prior readings: LeaseGuard said the lease limit must cover the holder's worst-case cycle (27 s vs 20 s), Aged-Updates said windowed AoI must include initial age T; this says the *sampling rate itself* has a point beyond which freshness stops buying effectiveness, whatever the AoI number.

3. **Holding-action structure is already the plant's.** The paper's (A1) — decision held constant on [D_i,D_{i+1}) while state evolves under P(·|X,A) — is what `discovery.sample()` plus the check's held verdict does between deliveries. The sufficient statistic is (state, AoI, last action, delay) (Lemma 1), not AoI alone. That is the transfer hypothesis.

4. **Predecessor relation (explicit).** 2026-10-22 (LeaseGuard, Davis et al. 2025) answered *lease validity as interval comparison* (target `site_sync.py`). 2026-11-01 (Miguelez et al. 2025 aged AoI) answered *age origin as acquisition start, not publish* (target `discovery.py`). This paper answers a third orthogonal axis: *whether fresher is more useful for the decision and how much sampling is enough* (Theorem 9, fmax^T). It does not replace either.

## Application

target: src/mishe_tauftauf/scan_freshness.py

The change is additive, pure documentation plus one pure function beside the existing `aoi_summary`/`classify_age` interval discipline; no emitted scan or check path changes.

1. **Name the divergence in the module header.** Extend the docstring that already says "UTC is diagnostic only" with: "Freshness (AoI) does not imply decision effectiveness (Li et al. arXiv:2504.19507v3 §I, Thm. 9). The module measures age and windowed AoI; the consumer's cost C(X_t,a_t) is not modeled here. There exists a threshold rate fmax^T beyond which additional sampling yields no reduction in optimal decision cost; the AoI line is descriptive, not the objective."

2. **Publish the threshold vocabulary as constants and a stub, beside the Yates symbols.** Add:
   ```
   # Li et al. 2026 — goal-oriented sampling; AoI vs effectiveness
   SAMPLING_EFFECTIVENESS_DEFINITION = "h* independent of fmax when fmax >= fmax^T = 1/F(rho*) (Thm.9)"
   # No behavior change: documents where extra sampling stops helping.
   ```
   and a pure helper `sampling_effectiveness_threshold` or `decision_cost_gap` stub:
   ```python
   def sampling_frequency_threshold_hint(current_interval_s: float, fmax_T_hint: float | None = None) -> dict:
       """Return whether current sampling rate is beyond the effectiveness threshold.

       Li et al. Thm.9: if fmax >= fmax^T, extra sampling does not reduce h*.
       This stub publishes the comparison without fitting rho*/F(); caller supplies
       fmax_T_hint when measured. Returns {fmax, fmax_T_hint, beyond_threshold: bool|None}.
       Pure arithmetic, no I/O.
       """
   ```
   The stub reuses the paper's symbols (fmax, fmax^T, rho*, h*) verbatim so a second reader can re-derive the check from the module alone (ledger-band rule precedent from the 2026-11-01 review).

3. **Keep `aoi_summary` contract unchanged.** Existing stamp-only and `lags` paths stay identical; warmup (<2 stamps) stays `None` (`unknown` vocabulary). The new helper is independent.

**Acceptance.** `python -m pytest tests/test_scan_freshness.py` passes unchanged (warmup/unknown, interval straddling, aoi_summary window tests). A new unit test shows: with a synthetic `fmax_T_hint`, `sampling_frequency_threshold_hint` returns `beyond_threshold=True` iff `1/current_interval_s >= fmax_T_hint`, and `None` when no hint is supplied. The `aoi :` line in `checks/coverage-window-expiry.py` still prints. No change to `discovery.py` sampling loop in this step.

**Prediction.** If the plant's median inter-scan interval (≈58 s initial age + scan period) already lies beyond the decision-relevant fmax^T for the checks that consume scans (coverage-gap expiry, evidence-binding), then halving the interval will not move `outcome_coverage` or routed `unknown:outcomes:<role>` — AoI will shrink while effectiveness stays flat, confirming the paper's divergence.

**Falsifier.** A held verification where faster sampling does not improve decision cost would be invalidated if repeated halving of the scan period does reduce the routed condition rate (e.g., fewer `unknown:outcomes` per 24 h window). That would imply fmax < fmax^T and the threshold is higher than hinted, or C(·) is more age-sensitive than assumed. The measurement decides; the module text stays.

**Limits of this reading.** 42-page paper not re-read line-by-line; QUICK BLP/ONE PDSI and ALP scaling not relied on. AR-MDP assumes a controlled Markov kernel P(·|X,A) and bounded costs; the plant's state is not a known MDP and delays are not characterized as Y_i distribution here — transfer is a hypothesis, not a verified capability. The threshold is published as a hint, not fitted from plant data; fitting rho*/F() from tape is the follow-up measurement the review enables. Scan sampling also has host-capacity costs (CPU/anon memory) not in the paper's single constraint. This role is read-only; the change belongs to the owner of `src/mishe_tauftauf/scan_freshness.py` (senses).

