---
field: evidence-and-verdicts
angle: metric
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/verdict_coverage.py
---

# Evidence verdicts need Guo's calibration metric: ECE/MCE and reliability, not just coverage

## What we read

Chuan Guo, Geoff Pleiss, Yu Sun, Kilian Q. Weinberger, *On Calibration of Modern Neural Networks*, ICML 2017 (PMLR 70, 14 pages) — read as the canonical text at:

- https://arxiv.org/pdf/1706.04599.pdf (arXiv:1706.04599v2, 3 Aug 2017)

Fetched 2026-11-03 as `/tmp/guo2017.pdf` 1349691 B sha256 `cb654a65acb785ed4387d6c2db1301d065075dddc2d363676836a2f2a14bc50d` (pdfTeX 1.40.17, 14 pages, Creator LaTeX hyperref). Transferable object is the **metric by which a confidence claim is judged calibrated**, not the ResNet/LeNet results.

Five passages carry the transfer (page/equation from the fetched text):

1. **Perfect calibration definition, eq. (1), p.2.** `P(Ŷ=Y | P̂=p) = p, ∀p∈[0,1]` where the probability is over the joint distribution. "the probability associated with the predicted class label should reflect its ground truth correctness likelihood" (p.1). Perfect calibration is impossible with finite samples since `P̂` is continuous — motivates empirical approximations.

2. **Reliability diagrams, p.2–3 (DeGroot & Fienberg 1983; Niculescu-Mizil & Caruana 2005).** Partition predictions into `M` bins `B_m` with `I_m = ((m-1)/M, m/M]`. `acc(B_m) = (1/|B_m|) Σ 1(ŷ_i=y_i)` "is an unbiased and consistent estimator of `P(Ŷ=Y | P̂∈I_m)`" and `conf(B_m) = (1/|B_m|) Σ p̂_i` approximates `p`. Perfectly calibrated → `acc(B_m)=conf(B_m) ∀m`, i.e. diagram plots identity. "Any deviation from a perfect diagonal represents miscalibration."

3. **Expected Calibration Error, eq. (2)–(3), p.3 (Naeini et al. 2015).** `E_{P̂}[ P(Ŷ=Y|P̂=p) − p ]` approximated by `ECE = Σ_m (|B_m|/n) |acc(B_m) − conf(B_m)|`. "the difference in expectation between confidence and accuracy" weighted by bin mass; gap per bin is red bar in Fig.1. Scalar summary of diagram, used as primary metric in Fig.2–3 and Table 1.

4. **Maximum Calibration Error, eq. (4)–(5), p.3.** `max_{p∈[0,1]} |P(Ŷ=Y|P̂=p)−p|` approximated by `MCE = max_m |acc(B_m)−conf(B_m)|`. "In high-risk applications where reliable confidence measures are absolutely necessary, we may wish to minimize the worst-case deviation." ECE is weighted average; MCE is largest gap.

5. **Limits and separation from accuracy, §3 p.3–4 + Fig.3.** Model capacity (depth/width), Batch Normalization, and low weight decay increase ECE while reducing 0/1 error: "increased model capacity will lower training NLL, and thus the model will be more (over)confident on average." Fig.3: test error drops 29%→27% while NLL overfits after LR drop at epoch 250 — "overfitting manifests in probabilistic error rather than classification error." NLL is minimized iff `π̂(Y|X)` recovers `π(Y|X)` (Friedman et al. 2001). §4 calibration methods (histogram binning, isotonic regression, BBQ, Platt/temperature scaling) are post-hoc transforms requiring hold-out validation; temperature scaling is "single-parameter variant of Platt Scaling" most effective on most datasets (Table 1, 15/18 datasets best or tied). No claim relied beyond eq.(1)–(6) and Fig.2–3.

Not re-read line-by-line: full Table 1 per-dataset numbers, Bayesian Binning derivation, vector/matrix scaling variants. No claim relied there.

## What this plant already does

- **Has the coverage half of Geifman's metric, no calibration half.** `evidence-and-verdicts--metric--2026-10-22` (Geifman & El-Yaniv NeurIPS 2017) landed `src/mishe_tauftauf/verdict_coverage.py` with `EVIDENCE_SELECTION_RULE="latest-outcome-row-per-role"`, `EVIDENCE_BOUND_METHOD="clopper-pearson-upper-tail"`, `EVIDENCE_BOUND_DELTA=0.05/0.001`, `clopper_pearson_upper(errors, rows, delta)` and `coverage_pair`/`stratum_coverage_gap` (Ding 2023 CovGap §3.3). That review measured on the live site at 2026-10-22: `bound=3270`, `latest_roles=12`, `coverage φ=0.00367`, empirical risk 0, but `b*(0,0.05,12)=0.2209` — and separated population coverage from routed visibility (`EVIDENCE_COVERAGE_ROUTED_NOTE`). The module publishes *how many* rows a verdict speaks for and the bound for the remainder, per stratum.

- **Publishes no confidence or calibration number beside a verdict.** `src/mishe_tauftauf/discovery.py:_ledger_evidence_binding` and `verdict_coverage.coverage_pair` handle 0/1 violation per row, not a `p̂∈[0,1]` confidence. A zero `latest_bad=0` among 12 judged rows is presented as count 0, not as confidence ≈1 or ECE gap. The 2026-10-22 measurement shows the gap is real: at `conf≈1.0` (implied by reporting 0 violations as "no risk") the bin's `acc=0.94` on the standing audit (195/3270 = 0.0596 violation rate → 0.9404 non-violation), i.e. overconfident by `|1.0−0.94|=0.06` on full population, and `|1.0−0.78|=0.22` on the abstained remainder at 95% bound — exactly the diagonal deviation Guo measures.

- **Sweep confirms absence.** `rg -n "ECE|MCE|calibration|reliability|conf\(B|acc\(B" src/ checks/ --ignore-case` hits no `ECE`/`MCE`/`reliability diagram`/`conf(Bm)`/`acc(Bm)` — only unrelated `receipt`/`receive`. `rg -n "ECE|calibration"` over `research/field-review/reviews/evidence-and-verdicts--metric*` hits only the predecessor's `latest_bad=0 is not zero risk` prose, not a scalar metric. The two `coverage` checks (`checks/coverage-gap-remedy.py`, `checks/coverage-window-expiry.py`) stay on routed visibility, not on calibration.

- **Keeps verdict words separate from truth, same limit Guo needs.** `evidence-and-verdicts--recent--2026-11-01` (SCITT §9.2 → `verdict_coverage.py:84-87`) added "verified here means only that a bound was computed" vs ledger's clean-audit `verified` (VC §1.1 literal already in `outcome_publication.py:138` `VC_TRUTH_IMPLIED_BY_VERIFIABLE=False`). That is the receipt-vs-truth separation that lets a calibration metric be stated without claiming a verdict is true. `evidence-and-verdicts--metric--2026-11-01` was discarded (Brown et al. 2001 Wald-vs-Wilson behind bot gate) and recorded the candidate upper-bound route — reachable-copy trigger only — so its gap (published zero without denominator) remains the Geifman predecessor's fix, not ECE.

## What is new here

Guo contributes one idea the plant does not embody: **a verdict that reports a confidence without reporting `|acc−conf|` per bin is uncalibrated, and two scalar summaries decide whether to trust it — `ECE` (weighted average over bins) and `MCE` (worst bin) — read off a reliability diagram whose diagonal is the falsifier.**

Concretely:

- `coverage φ = |judged|/|population|` answers "how many rows did you judge?" (Geifman). `ECE = Σ |B_m|/n · |acc(B_m)−conf(B_m)|` answers "when you said `p̂=0.95`, were you right 95% of the time?" — orthogonal axes. A system can have high coverage and high ECE (confident everywhere, wrong in one bin) or low coverage and low ECE (abstains correctly, calibrated where it speaks). The plant measures the first axis; it has no number for the second.

- `MCE = max_m |acc−conf|` is the high-risk companion to ECE: "what is the largest bin deviation?" Table 1 reports both, and §3 frames MCE as the high-risk choice. For this plant, a `drift` state that rests on one stratum with `MCE≈0.22` is a different signal than a `drift` with `MCE≈0.02`, and the published surface does not distinguish them.

- The diagram is the falsifier: `acc(B_m)` vs `conf(B_m)` should be diagonal; bin construction (`M` equally-spaced intervals of width `1/M`, or equal-mass quantiles) and sample count per bin determine variance (Naeini §S1). ECE via `|B_m|/n` weighting makes low-mass bins matter less; MCE makes them matter most — the paper keeps both for that reason. The plant's rows already carry a natural binning: by `conf` if it ever quotes per-row `p̂`, by `coverage` band (`fresh`/`versioned`/`policy_version_rows=77` vs pre-amendment 2163 rows), or by `genTime` stratum — none currently reported as a calibration table.

- Limits kept from source (so transfer stays bounded): (i) perfect calibration unattainable with finite samples (continuous `P̂`, p.2); empirical ECE/MCE are estimates with binning variance, not population truth. (ii) eqn(2)→(3) approximation needs i.i.d. validation set drawn from same `π(X,Y)` (assumed §4: "training, validation, test drawn from same distribution"). The plant violates this across the 2026-10-16 allowlist amendment (risk 0.071 before vs 0.037 after, 1.9× — measured 2026-10-22), so ECE like `b*` must be stratified (`versioned` vs pre-amendment) or reported UNKNOWN across the boundary. (iii) Calibration is about `P̂` reporting correctness likelihood, not about ledger truth; reducing ECE does not prove any outcome's evidence is true, and temperature scaling (§4) recalibrates `p̂` post hoc without changing accuracy — it is explicitly not a fix for the ledger's `0 vs 195` divergence (the Geifman predecessor's pin-advance trigger).

## Application

target: src/mishe_tauftauf/verdict_coverage.py

**Change — additive, pure, constants + helpers; no decoder/writer behavior change here:**

1. Beside the existing coverage/bound blocks (currently `EVIDENCE_SELECTION_RULE` at :22, `EVIDENCE_BOUND_METHOD` at :28, `EVIDENCE_BOUND_DELTA` at :45, `EVIDENCE_VERDICT_COVERAGE_DEFINITION` at :51, `EVIDENCE_BOUND_STATE_*` at :84), add a short `--- Calibration (Guo et al. ICML 2017 §2–3, ECE/MCE) ---` block citing `https://arxiv.org/pdf/1706.04599.pdf` (eq.1 perfect calibration, eq.3 ECE, eq.5 MCE, reliability diagrams) and stating the verdict-coverage mapping:
   - `coverage φ` (Geifman) is judged/population mass; `ECE/MCE` (Guo) is confidence-vs-accuracy deviation per bin — orthogonal, both publishable.
   - `acc(B_m)` is empirical violation complement (or non-violation rate) in bin `m`; `conf(B_m)` is mean stated confidence in that bin. At `p̂≈1.0` reported by `latest_bad=0`, the standing audit's `acc≈0.94` (or abstained `acc≈0.78` at bound) is the measured gap.
   - Binning is explicit: `M` equally-spaced intervals `((m-1)/M, m/M]` (paper's default) or named quantile; `M` published beside ECE/MCE so variance is auditable.
   - Limits: ECE/MCE estimates require i.i.d. within stratum; across `EVIDENCE_POLICY_AMENDMENT_TIME` (2026-10-16) report `unknown` per stratum rule already in module; calibration ≠ truth (SCITT/VC wording at :84 stays); finite-sample estimate, not population guarantee.

2. Add pure vocabulary constants (mirroring how `ci_watch.py` exposes SLSA vocab but scoped to verdict coverage, so the sweep for this field finds it beside the coverage helpers):
   ```python
   EVIDENCE_CALIBRATION_METHOD: Final[str] = "ece-mce-reliability"
   EVIDENCE_CALIBRATION_BINS_DEFAULT: Final[int] = 10
   EVIDENCE_CALIBRATION_DEFINITION: Final[str] = "ECE = Σ |B_m|/n |acc(B_m)-conf(B_m)| (eq.3), MCE = max |acc-conf| (eq.5); reliability diagram acc vs conf should be diagonal"
   ```

3. Add three pure helpers (no I/O, no model, same shape as `clopper_pearson_upper`/`coverage_pair`):
   - `expected_calibration_error(*, confidences: Sequence[float], correct: Sequence[int], bins: int = 10) -> float` — Guo eq.(3): partition `p̂` into `M=bins` equally-spaced intervals, per-bin `acc = mean(correct)`, `conf = mean(confidences)`, return `Σ |B_m|/n · |acc−conf|`. Validate `0<=p̂<=1`, `correct∈{0,1}`, `len` match, `bins>=1`; return `0.0` on empty input.
   - `maximum_calibration_error(*, confidences: Sequence[float], correct: Sequence[int], bins: int = 10) -> float` — Guo eq.(5): `max_m |acc−conf|`, same validation, `0.0` on empty.
   - `reliability_diagram(*, confidences: Sequence[float], correct: Sequence[int], bins: int = 10) -> list[dict]` — per-bin `{"bin": m, "interval": (low, high), "count": |B_m|, "acc": acc, "conf": conf, "gap": |acc−conf|}` ordered by bin, skips empty bins (count 0 not emitted). Pure table so a second reader can reproduce Fig.1 bottom without an image.

   All three are one-pass arithmetic over already-published counts; they do not re-derive bands, do not fit temperature, and do not change the tape format. When per-row `p̂` is not yet quoted (today), `confidences` can be derived from quoted strata: e.g. two bins `{P̂=1.0→conf=1.0, correct=1−violating/judged}` vs `{P̂=0→conf=0}` per stratum — helper still computes honest `|acc−conf|`.

**Acceptance, checkable on the committed bytes (no behavior change):**

- `grep -n "EVIDENCE_CALIBRATION_METHOD\|EVIDENCE_CALIBRATION_BINS_DEFAULT\|expected_calibration_error\|maximum_calibration_error\|reliability_diagram" src/mishe_tauftauf/verdict_coverage.py` finds the constant/helpers with docstrings citing `Guo et al. 2017 eq.(1)/eq.(3)/eq.(5)` and URL `https://arxiv.org/pdf/1706.04599.pdf` on a nearby line.
- `python3 -c "from mishe_tauftauf.verdict_coverage import expected_calibration_error, maximum_calibration_error, reliability_diagram; print(expected_calibration_error(confidences=[1.0]*10, correct=[1]*8+[0]*2, bins=1))"` returns `0.2` (one bin `acc=0.8 conf=1.0 gap 0.2, MCE same`) and `reliability_diagram` returns one dict with `gap 0.2`; empty input returns `0.0`; mismatched lengths raise `ValueError`.
- `grep -n "ECE\|MCE\|Guo\|1706.04599\|reliability diagram" src/mishe_tauftauf/verdict_coverage.py` shows only the new block until a publishing review lands — same invariant the coverage and SLSA blocks preserved.
- Compatibility: `OutcomePublication / decode_publication_body / clopper_pearson_upper / coverage_pair / stratum_coverage_gap` contracts unchanged, `pytest tests/test_verdict_coverage.py -q` and existing `tests/test_discovery*.py` still pass; `python3 research/field-review/field_review.py check` stays PASS.

**What this proposal does not do.** No per-row `p̂` publication, no temperature scaling fit, no change to `discovery.py` sense or `verdict_coverage.py` bound wording (`verified` at :84 stays receipt-only per SCITT/VC), no SLSA/C2PA vocabulary change, no new storage field, no NLL/accuracy tradeoff claim. When the plant later quotes per-row confidence (e.g. from stratum bound complement), the helpers already consume it; until then they compute the overconfidence already measurable from `latest_bad=0` vs `all_bad=195`.

**Owner:** senses (ledger evidence-binding reading and `verdict_coverage` live beside `discovery.py:_ledger_evidence_binding`; ECE/MCE quantify that reading's calibration, same owner as the Geifman coverage predecessor).

**Prediction:** Naming `ECE/MCE` beside `φ` makes a `verified` that says `0/12` auditable as `gap 0.22 at φ=0.0037` on the same page, without changing any published bytes until per-row `p̂` is quoted.

**Falsifier:** If a second reader can correctly state `|acc−conf|` per bin and how it weights by `|B_m|/n` from the current `verdict_coverage.py` without this comment/helpers — i.e., the code already separates coverage mass from calibration gap by definition or docstring — then the gap is already closed and this proposal is a duplicate.

**Predecessor:** `evidence-and-verdicts--metric--2026-10-22` (Geifman & El-Yaniv NeurIPS 2017 → `verdict_coverage.py` coverage φ, selective risk, `b*` bound, CovGap). This REVISIT keeps that word-limit intact and adds Guo's calibration metric beside it: coverage says how many rows were judged, ECE/MCE says whether the confidence quoted for them matched reality.

## Limits

The source is a neural-network calibration paper (vision/NLP, ImageNet/CIFAR), not a ledger audit; adopted constants do not claim ECE is minimized, that temperature scaling was applied, or that NLL/accuracy tradeoff holds for deterministic hash checks. The distinction between a row count (`coverage`) and a confidence-accuracy gap (`ECE = Σ weight·|acc−conf|`) is the only metric transferred — Guo §2–3's diagonal falsifier, not the capacity/BatchNorm causality of §3 or the post-hoc recalibration recipe of §4.
