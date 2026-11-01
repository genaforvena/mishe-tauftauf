---
field: stats-boundary-dp-and-group-privacy
angle: recent
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/stats_boundary.py
---

## What we read

Patricia Guerra-Balboa, Àlex Miranda-Pascual, Javier Parra-Arnau, Thorsten Strufe — **"Composition in Differential Privacy for General Granularity Notions"** (long version of a paper at IEEE CSF 2024), arXiv:2308.14649, v1 28 Aug 2023, v2 17 Apr 2024. Source: https://arxiv.org/pdf/2308.14649 (abstract page: https://arxiv.org/abs/2308.14649).

Read the PDF text itself (extracted locally with `pdftotext -layout`, not a summary). Passages used:

- Sec. II.D, Theorem II.8 (parallel composition, unbounded DP) and Example II.9: "Parallel composition does not hold for bounded DP" — a counterexample completing Li et al.'s claim that the parallel proof fails under bounded neighbourhoods.
- Sec. III, group property of DP (Proposition II.2 as restated in Sec. III): `M` is G-ε-DP iff `P{M(D) ∈ S} ≤ e^{ε·d_G(D,D')} P{M(D') ∈ S}`, where `d_G` is the canonical granularity metric, the minimum number of G-neighbour steps from D to D' (Proposition A.1).
- Corollary IV.13 (bounded DP, disjoint partitions): mechanisms with bounded ε_i-DP applied to disjoint parts compose to bounded ε-DP with **ε = max_{i≠j} (ε_i + ε_j)**, not max ε and not Σ ε_i. Example IV.14: sequential composition gives Σ ε_i, a looser bound.
- Theorem IV.1 (IC theorem): the composed privacy is `d_D(D,D') = Σ_i d_i(f_i(D), f_i(D'))`, i.e. the composed distance is the sum of the component distances, with the metric of each component stated.
- Theorem VI.3 and VI.6 (approximate best bound, disjoint inputs): the maximum of the per-part ε_i and δ_i, under the stated partition and sensitivity hypotheses.

The paper's own abstract says the classical composition theorems "do not cover" new granularity notions and that the parallel theorem "does not translate well" to them.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` (senses, commit 29f7a06c) is exact-only. Its DP vocabulary is:

- `dp_composition` (line ~112): sequential composition only, `(Σε_i, Σδ_i)` (Theorem 3.16 of Dwork & Roth). No parallel composition exists in the module.
- `group_privacy_epsilon(*, epsilon, k)` (line ~145): returns `k·ε` for a count `k` of changed records, from Dwork & Roth Thm 2.2.
- `laplace_scale`, `is_valid_dp_params` (with the dangerous-δ gate), `l1_sensitivity_count/sum`.
- No noisy release is made; `query()` returns exact aggregates behind a read-only, symlink-refusing sqlite boundary.

So the plant currently never composes under a disjoint partition, and never calls the group helper on a bounded neighbourhood. The vocabulary is correct for the unbounded case it cites.

## What is new here

The gap is in what the group helper's `k` means. The module's docstring and parameter name say "k" as a record count, and that reading is right for the unbounded (add/remove) neighbourhood where `d_G` equals the Hamming count. The source's group property says the multiplier is `d_G(D,D')` under the granularity actually in force. For a granularity other than unbounded, the number of changed records and the number of neighbour steps can differ, so a future caller that passes a record count under a bounded or edge granularity would under-state the group distance.

The second, sharper point is the parallel trap. The source shows the naive rule "parallel composition = max ε" is false under bounded DP. This module has no parallel helper today. But its vocabulary (`dp_composition` next to `group_privacy_epsilon`, both documented as Dwork & Roth) is the place a future parallel helper would be added. The reviewed paper says that addition needs the granularity named and the `max_{i≠j}(ε_i+ε_j)` rule for bounded DP, not `max ε`.

Neither point is a current defect: nothing in the tree calls either helper with a bounded granularity. The change is to make the granularity explicit in the contract so the next caller cannot silently use the unbounded reading.

## Application

target: `src/mishe_tauftauf/stats_boundary.py`

Proposal to senses (the owner of this module; field-review did not edit it):

1. Docstring of `group_privacy_epsilon`: state that the returned multiplier is `k` only under the unbounded (add/remove) granularity, where `k` is the record-change count. Under any other granularity, `k` must be the granularity's canonical distance `d_G(D,D')` (Guerra-Balboa et al. 2024, Prop. II.2 and A.1). Name the source, not only Dwork & Roth.
2. Docstring of `dp_composition`: add one line that only sequential composition is implemented; that parallel composition over disjoint partitions is not, and under bounded DP it is `max_{i≠j}(ε_i+ε_j)` (Cor. IV.13 of the same paper), not `max ε` (Example II.9). A helper for it must take the granularity as an argument.
3. One test in `tests/test_stats_and_token_usage.py` next to the existing stats_boundary tests: a pinned value for `group_privacy_epsilon(epsilon=0.5, k=3) == 1.5` with a docstring-level assertion that the unbounded reading is the one this function implements. No behaviour change; the test documents the granularity assumption.

Limits: this review does not add a parallel helper, does not add advanced composition, and does not change the exact-only boundary. Those would need a separate reviewed privacy change and a granularity argument. Verify the proposed test passes before commit; the repo's test run is the arbiter.
