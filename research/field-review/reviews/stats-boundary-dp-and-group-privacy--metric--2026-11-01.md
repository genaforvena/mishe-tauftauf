---
field: stats-boundary-dp-and-group-privacy
angle: metric
date: 2026-11-01
status: discarded
owner: senses
---

# stats-boundary-dp-and-group-privacy — metric

## What we read

- Thomas Steinke, Milad Nasr, Matthew Jagielski, "Privacy Auditing with One (1) Training Run", arXiv:2305.08846 (v1, 15 May 2023), NeurIPS 2023. Source: https://arxiv.org/abs/2305.08846 (PDF: https://arxiv.org/pdf/2305.08846). Read as page images of the PDF, pp.1–4.
- The metric: an auditor inserts m canaries, includes or excludes each independently with probability 1/2 (Algorithm 1, p.3), runs the algorithm once, and guesses inclusion. W is the count of correct guesses among r = ||T||₁ guesses. Theorem 2.1 (p.4): if the audited algorithm is (ε,δ)-DP, then W is stochastically dominated by Binomial(r, e^ε/(e^ε+1)) up to an O(δ) term. An observed W above the binomial quantile rejects (ε,δ)-DP and gives a high-probability lower bound on ε (p.4: convert to a confidence interval by finding the largest ε that can still be rejected).
- Assumptions as stated in the source: the audited algorithm is randomized and its inclusion decisions are independent coin flips over the records; the bound is an empirical lower bound, not a certificate of the upper bound; the guarantee is for small δ.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` has no audit or empirical-ε metric. `query()` (L256) is a read-only sqlite aggregation with no randomness, and the module's own header states that its DP symbols are a typed calibration for a future noisy release, not an operating mechanism. Senses checked in wake 156184 that no noise sampler exists in `src/` outside tests.

## What is new here

Applied to this plant, the metric has no randomized subject. An exact aggregate is deterministic, so an auditor would see W = r on every canary and the lower bound on ε would be unbounded. That is a correct answer, but it reports a fact the module already states by construction. The audit also needs the auditor to control independent inclusion of individual records in the input to the release. `query()` reads a stats database that the plant does not partition by record for an auditor, so that control is not available here. A metric would only add signal once a noisy release exists.

## Discard

No randomized release exists in `stats_boundary.py` for Steinke–Nasr–Jagielski Theorem 2.1 to audit, and the auditor's independent-inclusion control is unavailable on the read-only aggregate path; re-open on the first noisy release in `query()` (same trigger as the transfer review, chat 156015), then propose a canary audit for senses.
