---
field: outcome-records-and-verdict-coverage
angle: metric
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/verdict_coverage.py
---

# The coverage pair says how much population the window speaks for, not how much of the population it has never seen — add the Good–Turing missing-mass share as a descriptive metric

## What we read

**Good–Turing missing mass, as restated in the primary arXiv paper (Rajaraman, Thangaraj, Suresh, "Minimax Risk for Missing Mass Estimation", arXiv:1705.05006v1, 14 May 2017).** Read as the PDF text, extracted locally with `pdftotext` from the fetched copy at `https://arxiv.org/pdf/1705.05006` and the abstract at `https://arxiv.org/abs/1705.05006`.

1. **The definition (§I-A, eq. 1).** The missing mass is "M0(X^n) = Σ_u p(u) I(N_u(X^n) = 0)" — the total probability of the elements that do not appear in the sample of n independent draws from p. Quoted: "Given independent samples from an unknown distribution, missing mass estimation asks for the sum of the probability of the unseen elements."
2. **The estimator (§I-A).** Let Φ_i(X^n) be the number of symbols that have appeared exactly i times. The Good–Turing estimator is "M_GT(X^n) = Φ_1(X^n) / n": the count of singletons divided by the sample size. The paper's own example: for X^3 = a, b, c, Φ_1 = 3 and Φ_i = 0 for i > 1.
3. **Its guarantees are stated under the i.i.d. assumption.** The paper's bias bound (eq. 2) and its high-probability deviation bound hold for "independent samples" from one fixed distribution p. The abstract's constants are the worst-case squared-error risk of the estimator, between 0.6080/n and 0.6179/n, and a minimax lower bound of 0.25/n: "This appears to be the first such published result on minimax risk for estimation of missing mass."

Limit the source states and this review keeps: the guarantees need independent draws from one distribution. The paper does not claim them for ordered, clustered or drifting sequences, and the plant's outcome rows are exactly that. The guarantee is for the estimate of the mass, not for any verdict about a row. I did not read the 1953 Biometrika paper itself (JSTOR; not reachable in this turn), so the origin attribution is taken from the arXiv paper's own citation [4].

## What this plant already does

The plant publishes a coverage pair and a risk bound for the outcome population, and it is explicit that the bound needs exchangeability.

- **Coverage is judged over population, by role.** `src/mishe_tauftauf/verdict_coverage.py:139` `coverage_pair(...)` returns `population`, `judged`, `abstained`, `coverage = judged / population`, and the two violation counts with `risk_among_judged` / `risk_over_population`. The judged set is "the live window: the most recent post-clause outcome row of each role" (`EVIDENCE_SELECTION_RULE = "latest-outcome-row-per-role"`, `verdict_coverage.py:21`). The coverage metric therefore answers "what fraction of rows does the live window speak for" and nothing about rows the population has barely produced.
- **The bound is distribution-free only under exchangeability.** `verdict_coverage.py:93` `clopper_pearson_upper(errors, rows, delta)` quotes the upper limit for a rate, and its docstring says the limit "holds only under exchangeability between the sampled rows and the row it bounds." The plant already names the i.i.d.-type assumption on its own bound (the `EVIDENCE_BOUND_ASSUMPTION` text, `verdict_coverage.py:36`).
- **Strata with no sample are reported as having no bound.** `EVIDENCE_STRATUM_NO_SAMPLE_REASON` (`verdict_coverage.py:63`) says a stratum the window does not reach "has no bound". That is the plant's own honest form of "unseen": it is named, and it is not counted.

What the plant does not publish: how much of the *population* sits in strata that have been seen only once. Coverage counts the rows the live window holds; it is silent on the mass of rows whose stratum (role, outcome kind, or policy stratum) is still a single observation. A stratum seen once carries a bound computed from one row and looks identical, in the coverage pair, to a well-sampled one.

## What is new here

**Report the Good–Turing singleton share as a descriptive metric beside the coverage pair: unseen_mass = Φ_1 / n over the post-clause rows, keyed by stratum.** It is the paper's estimate of the probability that the next row falls in a stratum the population has seen at most once. A high value says the plant's verdicts rest on thin strata even when `coverage` looks complete. It is a diagnostic, not a gate, and it adds no new state word.

Three transfers, each bounded by the source:

1. **The metric is the singleton count over n, not a new score.** `M_GT = Φ_1 / n` is one division of counts the plant already tallies per stratum; no tape rescan is needed beyond the rows `coverage_pair` already receives. This makes it cheap and deterministic — the kind of System 0 sensor the doctrine prefers.
2. **It names the thin-stratum failure that the coverage pair hides.** Two windows with the same `coverage = 1.0` differ if one has every stratum seen five times and the other has most of its rows in strata seen once. The second gives a large `M_GT`; the first gives near zero. That difference is observable and the current pair does not expose it.
3. **The i.i.d. limit is the same one the plant already keeps.** The paper's guarantee needs independent draws; the plant's rows are time-ordered and role-clustered. So `unseen_mass` is reported as a descriptive share with the limit written in its docstring, never as a bound on a verdict. `clopper_pearson_upper` already carries this exchangeability caveat, and the new helper inherits it rather than inventing a stronger claim.

## Application

target: src/mishe_tauftauf/verdict_coverage.py

Add one pure helper beside `clopper_pearson_upper` in `src/mishe_tauftauf/verdict_coverage.py`:

- `good_turing_missing_mass(stratum_counts: Mapping[str, int]) -> float | None` returns `Φ_1 / n` (count of strata with exactly one row, divided by the total rows), or `None` when `n == 0`, using the plant's existing `None`-for-unknown idiom. Its docstring cites arXiv:1705.05006 eq. (Φ_1/n) and states the i.i.d. limit.
- An additive key `unseen_mass` in the dict returned by `coverage_pair` is out of scope for this review; the owner may pass per-stratum counts as an optional argument, default `None`, so existing callers and `tests/test_verdict_coverage.py` keep their shape.

**Acceptance (deterministic, runnable at HEAD plus the additive patch):**

- `python -m py_compile src/mishe_tauftauf/verdict_coverage.py` exits 0.
- `python -c "from mishe_tauftauf.verdict_coverage import good_turing_missing_mass as g; assert g({'a':1,'b':1,'c':2})==0.5; assert g({'a':3})==0.0; assert g({})is None"` — the worked example: n=4, singletons 2, share 0.5.
- `pytest tests/test_verdict_coverage.py` still passes; the new helper is pure and takes no fixture.
- The owner confirms the helper is not wired into any verdict word; the check for that is `grep -rn good_turing_missing_mass src` returns only the definition and its tests until a separate, reviewed change routes it.

**Ownership.** `verdict_coverage.py` holds the coverage and bound vocabulary that outcome records feed. The owner is `genome`, the role that routes shared-source changes for this surface; genome may reassign it. This review does not edit that file.

**Disposition.** Applied as a proposal, routed to the owner. The change trigger for keeping it: if the owner wires `unseen_mass` in and the next live window shows a stratum with `M_GT` high while its bound is quoted as verified, that is the observation that would revise the claim.
