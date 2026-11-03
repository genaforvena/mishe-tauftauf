---
field: stats-boundary-dp-and-group-privacy
angle: recent
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/stats_boundary.py
---

# REVISIT recent: organisation-scale DP is a set of scoped rules, not one epsilon — what DPolicy (2025) adds after Guerra-Balboa

## What we read

Nicolas Küchler, Alexander Viand, Hidde Lycklama, Anwar Hithnawi — **"DPolicy: Managing Privacy Risks Across Multiple Releases with Differential Privacy"**, arXiv:2505.06747, v1 10 May 2025. Source: https://arxiv.org/abs/2505.06747 (PDF: https://arxiv.org/pdf/2505.06747).

Fetched and read the PDF itself (not a summary). Retained `/tmp/dpolicy-2505.06747.pdf` (652,679 bytes) and `/tmp/dpolicy-2505.06747.txt` (1,278 lines via `pdftotext -layout`). Passages used, section-anchored:

- Sec. 2 Background, DP definition `Pr[M(D) in S] <= exp(epsilon) Pr[M(D') in S] + delta`, neighbouring definitions bounded (substitute-one, fixed size) vs unbounded (add/remove), and composition/group-privacy paragraph: sequential `sum epsilon_i`, parallel `max epsilon_i` over disjoint parts, RDP/zCDP/f-DP variants, group privacy `k*epsilon` for pure DP vs `k^2 rho` for zCDP (p.3).
- Sec. 2 Privacy units and converting `user-day -> user-month` requires group privacy with `k=31` (p.8–9), with Wikimedia example `rho_d=0.015 (epsilon_d=0.8, delta=1e-6)` implying `rho_m = 31^2 * rho_d = 14.415` under group privacy (p.9). Also attribute-DP and label-DP relaxations, time-based units and their independence assumption (pp.3–4).
- Sec. 2 Motivation (pp.1–2): why a single global epsilon does not compose across contexts — counting vs ML budgets, and why per-release reporting "only justified if the releases are independent … may not hold" with LiRA amplification 75% -> 90% and Annamalai attribute-concentration attack as concrete multi-release failures (pp.4–5).
- Sec. 4.1 Core concepts (p.5): rule `r_k = (phi_k, U_k, B_k)` — predicate `phi_k : M -> {0,1}` over labelled mechanisms, privacy unit `U_k`, budget `B_k`. Base policies generate `R̂ = {(phi_1,U_1,B_1),...}` (Eq.1), extension policies map `extend: R -> R^ell`. Per-release vs across-release rules, with policy sets as ACL analogy.
- Sec. 4.2 Fig.1 (p.6): example with custom global `User epsilon 10`, per-attribute `HR epsilon 3 / LR epsilon 7`, per-category, and extension `MLExtension` splitting standard vs black-box (`epsilon 10 * budget`), showing two simultaneously-held scopes on the same attribute set.
- Sec. 5.1 Rule pruning (pp.9–11): poset `phi1 ⊑ phi2` iff `{m | phi1(m)} ⊆ {m | phi2(m)}`, unit order `U1 <= U2` if `U2` covers `U1`, rule order `r1 ⪯ r2` iff `phi1 ⊑ phi2` and `U1 <= U2` (Eq.5). **Theorem 5.1** (p.10): `ri` non-constraining if `exists rj != ri` s.t. `ri ⪯ rj` and `Bi >= Bj`. Algorithm 2 propagates implied budget downward; Figure 2 illustrates.
- Sec. 5.2–5.3 (pp.11–13): enforcement as privacy filter checking each rule's predicate per mechanism, block composition via partitioning attributes (PAs) partitioning users into blocks, and incremental rollouts — "accounting for prior DP releases … re-analyze prior data releases … previously accepted [releases] have already consumed budget" (p.12 footnote 15 notes composition holds under less-ideal models).

The paper's abstract frames the contribution as the gap between per-release templates and a global guarantee, and its policy language as making "traditionally implicit assumptions on scopes and contexts explicit."

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` (senses, 470 lines at `22a077dd`) is exact-only with DP as typed vocabulary (Dwork & Roth 2014, Dwork et al. TCC 2006). Its helpers are:

- `dp_composition(*, epsilons, deltas)` — sequential composition only `(sum epsilon_i, sum delta_i)` (Thm 3.16), with a docstring note (added post-`0e5e2c38`/`9408be77`) that parallel over disjoint parts under bounded DP is `max_{i!=j}(epsilon_i+epsilon_j)` not `max epsilon` (Guerra-Balboa et al. 2024 Cor.IV.13 / Ex.II.9), and that no parallel helper exists and one must take granularity.
- `group_privacy_epsilon(*, epsilon, k)` — `k*epsilon` with docstring (post-`0e5e2c38`) that `k` is a record count only under unbounded DP; otherwise `k` must be `d_G(D,D')` (Guerra-Balboa Prop.II.2/A.1). Pure-`epsilon` only; `(epsilon,delta)` delta multiplier `k*exp((k-1)epsilon)` noted not returned. Posterior-odds warning from Pufferfish TODS Thm 6.2 added in `d96960ad`.
- `laplace_scale` — `Delta f / epsilon` (Thm 3.6) with global-L1 warning (added `a7c70b5e`: observed maximum not a sensitivity) and Remark 2.1 discretization note.
- `is_valid_dp_params` — `epsilon>0, delta in [0,1)` with dangerous-delta gate `delta < 1/n` and calibrated `delta*n >= 0.1 => invalid` (plant choice on p.18 qualitative warning, attributed in `de4a498e`), and `n` caller-asserted.
- Module header (post-`2b403026`/`d96960ad`) states exact aggregates before any noisy release are outside the epsilon budget and composition does not cover them (Pufferfish §6.3.2), and that a future noisy release needs a ledger including prior exact releases.

Six cells for this field have landed (`8d68b6f9` recent Guerra-Balboa plus misread/mechanism/critique/metric/transfer). None names DPolicy or organisation-scale policy composition, per-attribute budgets, rule pruning, or block composition. The module has no ledger, no per-attribute accounting, no PA/partitioning vocabulary, and no `UserDay/UserMonth` unit handling — exactly the "one epsilon" view DPolicy shows fails across releases.

Senses has applied the Guerra-Balboa docstring proposals (`9408be77`, `a7c70b5e`, `d96960ad`, `de4a498e`) on `main`, verified by `py_compile` and `check_parsing` in those commits.

## What is new here

Guerra-Balboa (our prior recent, `0e5e2c38`) fixes the meaning of `k` under different granularities and the parallel bound for bounded DP. DPolicy (2025) is the complementary recent on what happens when the plant actually ships more than one release. Its transfer is threefold and not in the tree:

1. **A privacy guarantee is a set of rules, not one `(epsilon,delta)`.** A rule is `(phi, U, B)` — scope predicate, unit, budget — and a policy set expands via base and extension policies into a large correlated rule set (Sec.4.1, Eq.1–2, Fig.1). The tight example is Fig.1a–c: a global `User epsilon 10` plus per-attribute `HR epsilon 3 / LR epsilon 7` and a context extension (standard `epsilon *1` vs black-box `epsilon *10`) co-exist; no single global budget captures this. The plant's `DP_EPSILON_DEFAULT=1.0` today reads as a single registry figure; DPolicy's language makes scopes/contexts explicit so that narrow units that give attractive `epsilon` do not create a false sense of privacy when the independence assumption fails (pp.2–4, with Wikimedia, census, and attribute-inference attacks).

2. **Rule pruning is a correctness property, not a tidy-up.** With `|R| = |Unit| * (1+|Attr|+|Cat|*|Lvl|) * |Ext|` rules (p.9), pruning is needed and Theorem 5.1 gives the safe condition: `ri` with `ri ⪯ rj` and `Bi >= Bj` cannot be violated while `rj` is satisfied, so it is non-constraining and can be deactivated (Algorithm 2, Figure 2). The poset construction decomposes `phi1 ⊑ phi2` into base/extension components to avoid `O(|R|^2)` predicate SAT checks. No equivalent pruning criterion exists in `stats_boundary.py`.

3. **Cumulative checking is stateful and must include prior exact releases, with block composition over partitioning attributes.** Enforcement checks every rule against the composition of mechanisms whose predicates match (Sec.5.2), which is a privacy filter over the sequence of releases (citations [34][48][64]). For disjoint data, block composition with PAs (country, time-step intervals `[t1,tk-1], tk, ...`) is tighter than global sequential composition and is the generalisation of parallel composition that DPolicy implements via its segmentation algorithm (pp.11–12). Sec.5.3 explicitly requires incremental rollouts to consume budget for prior exact releases — the same debt the module header already names via Pufferfish, now with a system that budgets it.

Limits the source states and this review keeps: group-privacy conversion for `(epsilon,delta)` or RDP/zCDP is *not* `k*epsilon` / `k^2 rho` in general and needs its own calibration (p.3, [18][27][51]); the `rho_d -> rho_m` example uses a non-tight `zCDP -> (epsilon,delta)` conversion for the fixed `delta` (footnote 10); labelling of mechanisms with correct `(phi,U,B)` is assumed and "hardening the approval process is future work" (p.12). DPolicy is implemented atop Cohere/Tumult-style budget allocators, not as a standalone noisy mechanism — adopting its language does not add noise.

## Application

target: src/mishe_tauftauf/stats_boundary.py

Proposal to senses (owner decides; this review does not edit the module):

Add a boundary paragraph in the module docstring beside the existing Dwork & Roth / Pufferfish / Guerra-Balboa paragraphs, citing **Küchler et al., "DPolicy: Managing Privacy Risks Across Multiple Releases with Differential Privacy", arXiv:2505.06747 (10 May 2025), https://arxiv.org/abs/2505.06747, retained /tmp/dpolicy-2505.06747.pdf 652,679 bytes / .txt 1,278 lines**, stating:

- That a guarantee for multiple releases is not a single `(epsilon,delta)` but a policy set generating rules `r=(phi,U,B)` over labelled mechanisms (Sec.4.1), with per-attribute / per-category scopes and context extensions (Fig.1 example: global `User epsilon 10` co-exists with `HR epsilon 3`). A future noisy release in this module must be checked against such a rule set and via a stateful ledger / privacy filter (Sec.5.2), not a scalar budget comparison; prior exact aggregates are already-consumed budget under incremental rollout (Sec.5.3) consistent with `Pufferfish §6.3.2`.

- That with `|Unit| * (1+|Attr|+...) * |Ext|` rules, pruning uses the posé ordering `r1 ⪯ r2 <=> phi1 ⊑ phi2 && U1 <= U2` (Eq.5) and Theorem 5.1 `ri <= rj && Bi >= Bj => ri non-constraining` (Sec.5.1 / Algorithm 2 / Fig.2). Any pruning helper must attribute this theorem, not a budget-comparison heuristic.

- That tightening across disjoint data should use block composition with explicit partitioning attributes (PAs) and time-step interval segmentation (pp.11–12), not the scalar parallel `max epsilon`. The existing sequential `dp_composition` and `k*epsilon` (pure DP) notes remain, with the clarification that for `(epsilon,delta)`/zCDP the group-privacy calibration is not `k*epsilon` (Sec.2, p.3) and needs its own reviewed parameter — no helper for it is added here.

Docstring-only, no behaviour, no new helper, no noise, no ledger. Revertible by `git revert`. Verification: `python3 -m py_compile src/mishe_tauftauf/stats_boundary.py` and `python3 research/field-review/field_review.py check` must stay PASS; `coverage` count unchanged except recency date moves for this cell.

Distinction from prior reviews on this field: the 2026-11-01 recent (`0e5e2c38`, Guerra-Balboa 2024) supplied the bounded-vs-unbounded granularity fix for `k` and parallel composition; this REVISIT keeps that and adds the orthogonal organisation-scale result — a single release's granularity is necessary but not sufficient once the plant holds state across releases and attributes. The discarded metric (`de20c8a9`, Steinke et al. 2023) and transfer (`2b403026`, RDP) both presupposed a noisy release the module lacks; DPolicy's policy layer precedes any noisy release and budgets the exact-release debt they named.

