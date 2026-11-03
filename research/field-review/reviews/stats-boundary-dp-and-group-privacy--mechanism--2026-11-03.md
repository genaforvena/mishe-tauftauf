---
field: stats-boundary-dp-and-group-privacy
angle: mechanism
date: 2026-11-03
status: discarded
owner: senses
---

## What we read

Cynthia Dwork and Aaron Roth, *The Algorithmic Foundations of Differential Privacy*, Foundations and Trends in Theoretical Computer Science 9(3–4), 2014. Authors' copy: https://www.cis.upenn.edu/~aaroth/Papers/privacybook.pdf. Read Definitions 2.4 and Theorem 2.2, book pp. 17–20 (PDF pp. 21–24). Theorem 2.2 gives pure group privacy `(kε, 0)` for datasets at distance at most `k`; the following paragraph says approximate `(ε, δ)` group privacy instead gives `(kε, k e^((k−1)ε) δ)`, with δ growing exponentially in kε.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py:185-200` implements the pure-DP epsilon multiplier and explicitly documents the approximate-DP delta multiplier as `k*exp((k-1)epsilon)` (Dwork–Roth footnote 1, p.20). The helper does not return δ. The module's `query()` still returns exact aggregates; there is no noisy approximate-DP release here.

## What is new here

The approximate group-privacy bound is operational only when a caller has an `(ε, δ)` mechanism and needs the guarantee for groups of size `k`. Its δ cost grows as `k e^((k−1)ε) δ`; it is not the linear `kδ` bound used for sequential composition. The existing helper already names this distinction and has no approximate mechanism to serve.

## Discard

The source's relevant approximate-DP group bound and its limitation are already documented beside the pure-DP helper; with no approximate-DP release, another helper or wording change would duplicate unused machinery.
