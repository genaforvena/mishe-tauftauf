---
field: outcome-records-and-verdict-coverage
angle: metric
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/verdict_coverage.py
---

# Marginal coverage hides class-conditional undercoverage — report the average class coverage gap beside the coverage pair

## What we read

**Ding, T., Angelopoulos, A. N., Jordan, M. I., Bates, S. & Tibshirani, R. J., "Class-Conditional Conformal Prediction with Many Classes", arXiv:2306.09335v2, 27 Oct 2023 (NeurIPS 2023, 22pp).** Fetched as PDF to `/tmp/ding-2023.pdf` 956997 B sha256 f4fac9d8415bdbfeca97a76257d9fb7526c835f65699c951bf4f2e157f6c0092 (Creator pdfTeX-1.40.25, 22 pages, CreationDate 2023-10-31T01:43:20Z) + `/tmp/ding-2023.txt` 2106 lines extracted via `pdftotext` 2026-11-02. Source URL `https://arxiv.org/abs/2306.09335` and PDF `https://arxiv.org/pdf/2306.09335.pdf`. Read as primary: Abstract, §1–1.2, §2 preliminaries, §3.3 CovGap definition, Figure 2 and Discussion guidelines.

1. **Two guarantees, one stronger.** Standard conformal gives *marginal* coverage (eq. 1): `P(Ytest ∈ C(Xtest)) ≥ 1 − α` for a random test point. The paper wants *class-conditional* coverage (eq. 2): `P(Ytest ∈ C(Xtest) | Ytest = y) ≥ 1 − α, for all y ∈ Y`, noting "(2) implies (1)" (§1, p.1). Quoted abstract line: "Standard conformal prediction methods provide a marginal coverage guarantee, which means that for a random test point, the conformal prediction set contains the true label with a user-specified probability."

2. **Marginal can be satisfied while classes diverge.** ImageNet case study (§1, p.1–2): STANDARD at 90% nominal on 50k ImageNet examples achieves marginal 89.8%, but "water jug is severely undercovered: the prediction sets only include it in 50.8% of the cases where it is the true label. On the other hand, flamingo is substantially overcovered: it achieves a class-conditional coverage of 99.2%."

3. **The metric that makes the hiding visible.** §3.2–3.3 (p.7): let `Jy = {i ∈ [N'] : Yi' = y}` be validation indices of class y and `ĉy = 1/|Jy| Σ_{i∈Jy} 1{Yi' ∈ C(Xi')}` be empirical class-conditional coverage. Then `CovGap = 100 × 1/|Y| Σ_{y∈Y} |ĉy − (1 − α)|` — average ℓ1 distance of per-class coverage from nominal, scaled to percentage points. `AvgSize = 1/N' Σ_i |C(Xi')|` is reported alongside to trade off coverage against sharpness.

4. **Three estimators on the same axis.** STANDARD uses one quantile `q̂` from all N scores; CLASSWISE splits by class with `q̂y` from `|Iy|` scores and is guaranteed to satisfy (2) in population but "quantiles q̂y are volatile for small |Iy|" (§2, p.3); CLUSTERED clusters classes by score-distribution similarity (KS distance) and shares calibration data within clusters, giving *cluster-conditional* coverage exactly, and class-conditional only under an oracle clustering that makes scores exchangeable within clusters (§2, pp.6–7). Figure 1 schematic: STANDARD low variance/insufficient granularity, CLASSWISE sufficient granularity/high variance, CLUSTERED middle.

5. **What this is not.** Coverage statements need exchangeability between calibration and test within the grouping unit (class or cluster); without it the gap is descriptive. The paper does not claim class-conditional holds for CLUSTERED on real data without the oracle; it reports empirical CovGap on ImageNet/CIFAR-100/Places365/iNaturalist at α=0.1 as the evidence and gives regime guidelines (extremely low-data <10/class → only STANDARD viable; 20–75 → CLUSTERED wins; >75–100 → CLASSWISE competitive; Discussion §4). Limits retained: CovGap is an evaluation metric over a held-out set, not a bound on the next prediction.

## What this plant already does

The plant publishes a *marginal* coverage pair and per-stratum bounds, with no summary of how far strata deviate from nominal.

- **Coverage pair is marginal over the population, judged by role.** `src/mishe_tauftauf/verdict_coverage.py:142` `coverage_pair(*, population, judged, judged_violating, population_violating)` returns `coverage = judged / population` plus `risk_among_judged` / `risk_over_population`. The judged set is `EVIDENCE_SELECTION_RULE = "latest-outcome-row-per-role"` (`verdict_coverage.py:21`): one row per role. It answers "what fraction of population does the live window speak for" — the analogue of Ding's marginal guarantee.
- **Per-stratum bounds are published, but not their gap.** `EVIDENCE_STRATUM_DEFINITIONS`, `EVIDENCE_STRATUM_NO_SAMPLE_REASON` (`verdict_coverage.py:63`) and `clopper_pearson_upper` (`verdict_coverage.py:96`) give a distribution-free upper limit per stratum quoted at `delta = 0.05` and `0.001`, under the `EVIDENCE_BOUND_ASSUMPTION` exchangeability caveat (`verdict_coverage.py:36`). A stratum with one row gets a bound computed from that one row; the coverage pair hides this thin-stratum variance exactly as Ding's marginal hides water-jug undercoverage.
- **The prior metric in this cell addresses a different question.** `research/field-review/reviews/outcome-records-and-verdict-coverage--metric--2026-10-31.md` proposed `good_turing_missing_mass = Φ1/n` (§I-A arXiv:1705.05006) — the probability the *next* row falls in an unseen stratum. That is a *missing-mass* diagnostic. This review's CovGap is a *conditional-coverage* diagnostic: how far the *seen* strata's empirical rates sit from nominal. Both are descriptive, both inherit the i.i.d./exchangeability limit, they are not interchangeable.

What the plant does not publish today: a single descriptive number saying whether all strata that *are* covered are covered *evenly*. Two windows with `coverage = 1.0` and identical bounds can differ arbitrarily in per-stratum deviation, and no current field exposes it.

## What is new here

**Report the Ding et al. average class coverage gap as a descriptive metric beside the coverage pair: mean |per-stratum empirical rate − nominal| over the strata the window reaches, scaled to percentage points.** It is `CovGap` with "class" instantiated as the plant's existing strata (role, policy-version, or outcome kind), computed from counts the plant already tallies. It is cheap, deterministic (one pass over per-stratum fractions), and needs no tape rescan beyond what `coverage_pair` receives — the System 0 sensor preference.

Three bounded transfers:

1. **The gap is the mean absolute deviation, not a new gate.** `gap = 100 × mean_y |ĉy − nominal|` where `ĉy` is the stratum's empirical fraction the window sees (e.g. `judged_in_stratum / population_in_stratum` or stratum-specific 1−error rate) and `nominal` is the quoted level. No new state word, no verdict change, no threshold.
2. **It names the marginal-hides-conditional failure.** The ImageNet water-jug/flamingo split (50.8% / 99.2% vs 89.8% marginal) is the observational pattern to warn against here: a live window can quote `coverage = 1.0` while some policy-version stratum is undercovered and another overcovered. CovGap at e.g. 6 pp flags the unevenness that the pair alone does not.
3. **The same exchangeability limit the plant already keeps.** Ding's coverage guarantees need calibration-test exchangeability within the grouping unit; without it CovGap is descriptive. `verdict_coverage.py` already publishes `EVIDENCE_BOUND_ASSUMPTION` beside its bound. The new helper carries the same caveat in its docstring and never claims a bound on a verdict.

## Application

target: src/mishe_tauftauf/verdict_coverage.py

Add one pure helper beside `clopper_pearson_upper` in `src/mishe_tauftauf/verdict_coverage.py`, no change to `coverage_pair` shape or any verdict word:

- `stratum_coverage_gap(coverages_by_stratum: Mapping[str, float], nominal: float | None = None) -> float | None` — computes `100 × mean |c − nominal|` over the supplied stratum fractions, returns `None` when the mapping is empty (plant's existing `None`-for-unknown idiom). When `nominal is None` it defaults to `1 - EVIDENCE_BOUND_DELTA` is out of scope for this review; the caller passes the nominal actually quoted. Docstring cites arXiv:2306.09335 §3.3 CovGap definition, states the exchangeability limit, notes CLUSTERED vs CLASSWISE vs STANDARD distinction does not apply here — this is reporting only — and names the stored source file and sha.

An additive `coverage_gap` key in the dict returned by `coverage_pair` is explicitly out of scope for this review; the owner may thread per-stratum fractions as an optional argument in a later change with default `None` so existing callers and `tests/test_verdict_coverage.py` keep their shape.

- `python -m py_compile src/mishe_tauftauf/verdict_coverage.py` exits 0.
- `python -c "from mishe_tauftauf.verdict_coverage import stratum_coverage_gap as g; assert abs(g({'water_jug':0.508,'flamingo':0.992}, nominal=0.9) - 24.2) < 0.01; assert g({'a':0.9,'b':0.9}, nominal=0.9)==0.0; assert g({}, nominal=0.9) is None"` — ImageNet two-class slice: mean(|0.508−0.9|=0.392, |0.992−0.9|=0.092) = 0.242 → 24.2 pp; balanced → 0.0; empty → None.
- `pytest tests/test_verdict_coverage.py` still passes; the helper is pure and takes no fixture.
- The helper is not wired into any verdict word; `grep -rn stratum_coverage_gap src` returns only the definition and its tests until a separate reviewed change routes it.

Concrete check for reviewer (run after patch):

```
python -c "from mishe_tauftauf.verdict_coverage import stratum_coverage_gap; print(stratum_coverage_gap({'water_jug':0.508,'flamingo':0.992}, nominal=0.9))"
# → 24.2 (percentage points)
```

**Ownership.** `verdict_coverage.py` holds the coverage and bound vocabulary that outcome records feed. Owner is `genome`, the role that routes shared-source changes for this surface; genome may reassign it. This review does not edit that file.

**Disposition.** Applied as a proposal, routed to the owner. Supersession note: this metric complements the 2026-10-31 Good–Turing missing-mass proposal (`research/field-review/reviews/outcome-records-and-verdict-coverage--metric--2026-10-31.md` arXiv:1705.05006 §I-A Φ1/n) — unseen-stratum mass vs unevenness among seen strata. Keep trigger: if the owner wires the gap in and the next live window shows `coverage≈1.0` with gap large (e.g. >5 pp) while a per-stratum bound is quoted `verified`, that observation revises the claim that marginal coverage suffices.
