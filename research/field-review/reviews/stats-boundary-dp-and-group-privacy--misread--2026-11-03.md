---
field: stats-boundary-dp-and-group-privacy
angle: misread
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/stats_boundary.py
---

# REVISIT misread: (ε,0) bounds every output, (ε,δ) bounds loss with probability 1−δ — the quantification switch we still elide

## What we read

Cynthia Dwork and Aaron Roth — **"The Algorithmic Foundations of Differential Privacy"**, *Foundations and Trends in Theoretical Computer Science* 9(3–4), 2014, pp. 211–407. Source: https://www.cis.upenn.edu/~aaroth/Papers/privacybook.pdf (the authors' verified copy).

Fetched and read the PDF itself (not a summary). Retained `/tmp/privacybook.pdf` (2,130,262 bytes) and `/tmp/privacybook.txt` (11,211 lines via `pdftotext -layout`). Passages used, page-anchored to the book's pagination (PDF page = book page + 4):

- Definition 2.4 (p.17–18): `M` is `(ε,δ)`-DP if for all `S` and `x,y` with `‖x−y‖₁ ≤ 1`: `Pr[M(x) ∈ S] ≤ exp(ε) Pr[M(y) ∈ S] + δ`, with `‖x‖₁` the database size and `‖x−y‖₁` the `L₁`/Hamming distance over `N^{|X|}` (Def. 2.3 p.17).
- The two paragraphs immediately after Def. 2.4 (p.18–19) — the qualitative core of this review. First paragraph: "values of δ on the order of `1/‖x‖₁` are very dangerous: they permit 'preserving privacy' by publishing the complete records of a small number of database participants" — the "just a few" failure of Sec. 1.1 p.10. Second paragraph (pp.18–19): even when `δ` is negligible, there is "what amounts to a switch of quantification order. `(ε,0)`-DP ensures that, for every run of `M(x)`, the output observed is (almost) equally likely to be observed on every neighboring database, simultaneously. In contrast `(ε,δ)`-DP says that for every pair `x,y`, it is extremely unlikely that, ex post facto the observed value `M(x)` will be much more or much less likely to be generated when the database is `x` than when it is `y`. However, given an output `ξ ∼ M(x)` it may be possible to find a database `y` such that `ξ` is much more likely to be produced on `y` than it is when the database is `x`." Defined privacy loss `L^{(ξ)}_{M(x)‖M(y)} = ln Pr[M(x)=ξ]/Pr[M(y)=ξ]` (p.18) and forward reference "As we will see in Lemma 3.17, `(ε,δ)`-DP ensures that for all adjacent `x,y`, the absolute value of the privacy loss will be bounded by `ε` with probability at least `1−δ`."
- Proposition 2.1 (Post-Processing, p.19): "Let `M` be `(ε,δ)`-DP. Let `f : R → R'` be an arbitrary randomized mapping. Then `f ∘ M` is `(ε,δ)`-DP." Proof fixes `T = {r : f(r) ∈ S}`. The `f` is a data-independent mapping on the mechanism's output — a function of `r` alone, not of `(x,r)`.
- Theorem 2.2 (p.19–20): "`Any (ε,0)`-DP mechanism is `(kε,0)`-DP for groups of size `k`: for all `‖x−y‖₁ ≤ k`, `Pr[M(x)∈S] ≤ exp(kε) Pr[M(y)∈S]`." Followed immediately (p.20): "More generally, composition and group privacy are not the same thing and the improved composition bounds in Section 3.5.2 (Theorem 3.20), which substantially improve upon the factor of `k`, do not — and cannot — yield the same gains for group privacy, even when `δ = 0`." And the `(ε,δ)` group note (p.20, line after): `(kε, k·e^{(k−1)ε}·δ)`-DP, not `(kε,kδ)`.
- Lemma 3.17 (p.44–45) and Remark 3.2 (p.44): `M` is `(ε,δ)`-DP iff `D^{δ}_{∞}(M(x)‖M(y)) ≤ ε` and `D^{δ}_{∞}(M(y)‖M(x)) ≤ ε` for all neighbors — i.e., up to `δ` mass the max-divergence is `ε`, formalizing the "with probability at least `1−δ`" reading. The two-part reformulation with `δ`-close `Y'`/`Z'` makes the quantification explicit.
- Theorem 3.16 (Sequential composition, p.43/B.1 p.267): `M_{[k]}(x) = (M₁(x),…,M_k(x))` with each `M_i` `(ε_i,δ_i)`-DP is `(Σ ε_i, Σ δ_i)`-DP (non-adaptive statement; Appendix B extends to adaptive composition with halting).

Quoted directly from the PDF text to avoid paraphrase drift of the switch.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` (470 lines at `4a9f26a8`) is exact-only; DP symbols are typed calibration with no noisy release. Its current posture after six landed cells and four applied docstring patches (`9408be77`, `a7c70b5e`, `d96960ad`, `de4a498e`, `4a9f26a8` DPolicy header) is:

- `is_valid_dp_params(*, ε, δ, n)` validates `ε>0`, `δ∈[0,1)`, and with `n` gates `δ < 1/n` plus the plant's calibrated `δ·n ≥ 0.1 → invalid` (comment since `de4a498e` explicitly marks `0.1` as plant choice, not from p.18, and that `n` is caller-asserted).
- `group_privacy_epsilon(*, ε, k)` returns `k·ε` for pure `(ε,0)`-DP with docstring noting `k` is record count only under unbounded DP (otherwise `d_G`, Guerra-Balboa) and that posterior-odds leakage under correlation needs an independence assumption (Pufferfish TODS Thm 6.2, `d96960ad`).
- `dp_composition` is sequential-only `(Σ ε, Σ δ)` with note that bounded parallel is `max_{i≠j}(ε_i+ε_j)` (Guerra-Balboa Cor. IV.13), no parallel helper.
- `laplace_scale` returns `Δf/ε` with global-`L₁` warning (Def. 3.1) and Remark 2.1 discretization note.
- Module header (lines 9–41): cites Dwork & Roth Def. 2.4 / Remark 2.1 / Prop. 2.1 / Thm 2.2 / Thm 3.6 / Thm 3.16, says "summary statistics are not safe" (Sec.1.1 pp.8–9) and "randomization is essential" (Sec.2.3 p.16), that "post-processing `f∘M` stays `(ε,δ)`-DP" and that exact aggregates before a noisy release are outside the budget (Pufferfish §6.3.2 + DPolicy Sec.5.3), plus the `4a9f26a8` DPolicy multi-release lens (`r=(φ,U,B)`, pruning Thm 5.1, block composition).

Prior misread `2026-11-01` correctly fixed the delta-threshold attribution and the caller-asserted `n` gap. None of the six landed cells names the quantification switch, the data-independence qualifier on post-processing, or the group-vs-composition non-equivalence — the three points that change how the same `(ε,δ)` numbers read.

## What is new here

The book treats `δ` not as a second epsilon to sum, but as a change in what the guarantee quantifies. `(ε,0)` bounds `exp(ε)` on every output simultaneously; `(ε,δ)` bounds `|L|≤ε` only with probability at least `1−δ` over the mechanism's coins (Lemma 3.17), and `δ` mass may have `|L|≫ε` (p.18–19 paragraph above). A reader who treats `(1.0, 1e-6)` as `(1.0,0)` plus negligible slack will misread both what a single `(ε,δ)` release promises and what `Σ δ` composes: the composed `Σ δ` is the `δ`-mass where the composed max-divergence exceeds `Σ ε`, not a per-run failure rate to average away.

Two companion points the same pages make, and the module currently elides:

1. **Post-processing is data-independent.** Proposition 2.1's `f` is a mapping `R→R'` on outputs only. The header's current "post-processing `f∘M` stays `(ε,δ)`-DP" omits the qualifier. A future change that post-processes with a function of `(x, M(x))` (e.g., re-querying the DB to format the output) is not covered and would silently need its own analysis.

2. **Advanced composition ≠ group privacy.** Theorem 3.20's `O(√k)`-style improvement over `kε` for composition "do not — and cannot — yield the same gains for group privacy, even when `δ=0`" (p.20). A caller who sees sequential `(Σ ε, Σ δ)` and assumes the same tighter bound transfers to `k` correlated records will under-state the group factor by the book's own prohibition.

Neither point changes arithmetic today — no noisy release exists — but both change how the existing docstrings will be used to justify one.

## Application

target: src/mishe_tauftauf/stats_boundary.py

Proposed to senses (owner) as a docstring/comment-only change — no behaviour, no new helper, no test change; revertible `git revert` of the `senses` follow-up commit:

1. **Header: qualify post-processing.** Change "post-processing `f∘M` stays `(ε,δ)`-DP" to "post-processing `f∘M` stays `(ε,δ)`-DP for data-independent `f: R→R'` (Prop. 2.1 p.19)"; a data-dependent `f(x,·)` needs new analysis.

2. **Near `is_valid_dp_params` / header ε-δ paragraph: state the quantification switch.** Add after the dangerous-delta line: "`(ε,0)` bounds the output ratio for every output simultaneously; `(ε,δ)` only bounds `|L^{(ξ)}|≤ε` with probability at least `1−δ` over `M(x)` (p.18–19; Lemma 3.17, Remark 3.2 `D^{δ}_{∞}≤ε`); `δ` mass may have much larger loss. `Σ δ` is `δ`-mass for the composed mechanism, not a per-output failure rate." Keep the existing `0.1` plant-choice note intact.

3. **In `group_privacy_epsilon` docstring: note non-transfer of advanced composition.** After the `k·e^{(k−1)ε}·δ` note, add: "Improved composition bounds (Theorem 3.20, Sec.3.5.2) do not — and cannot — improve group privacy even when `δ=0` (p.20)."

All three cite the page-anchored paragraphs above so a later reader can distinguish book from plant choice.
