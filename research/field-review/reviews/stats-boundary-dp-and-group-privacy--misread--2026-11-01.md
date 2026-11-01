---
field: stats-boundary-dp-and-group-privacy
angle: misread
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/stats_boundary.py
---

## What we read

Cynthia Dwork and Aaron Roth — **"The Algorithmic Foundations of Differential Privacy"**, *Foundations and Trends in Theoretical Computer Science* 9(3–4), 2014, pp. 211–407. Source: https://www.cis.upenn.edu/~aaroth/Papers/privacybook.pdf (the authors' own copy).

Read the PDF text itself (extracted locally with `pdftotext -layout` into the scratchpad, not a summary). Passages used:

- Definition 2.4 (p.17–18), and the warning that follows it: "values of δ on the order of 1/‖x‖₁ are very dangerous: they permit 'preserving privacy' by publishing the complete records of a small number of database participants." The book's qualitative warning is about δ relative to the database size ‖x‖₁ (the number of records). It names no numeric cutoff.
- Theorem 2.2 (p.20): an (ε,0)-DP mechanism is (kε,0)-DP for groups with ‖x−y‖₁ ≤ k. The group multiplier is the record count k under the histogram/Hamming notion the book uses on that page.
- The (ε,δ) group statement that follows Theorem 2.2 (p.20): (kε, k·e^{(k−1)ε}·δ)-DP for groups of size k.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` (senses) carries the Dwork & Roth vocabulary in its header and helpers:

- `group_privacy_epsilon(*, epsilon, k)` returns `k·ε` for a record count `k`. This matches Theorem 2.2 exactly, and the docstring's δ-multiplier note `k·exp((k−1)ε)` matches the book's group statement.
- `dp_composition` returns `(Σε_i, Σδ_i)`, the sequential bound of Theorem 3.16. No parallel helper exists.
- `is_valid_dp_params(*, epsilon, delta, n=None)` gates δ. It rejects `δ ≥ 1/n` and, beyond that, rejects any nonzero δ with `δ·n ≥ 0.1`. The inline comment says this "decade threshold" is how "on order 1/n" (p.18) is typed, and it cites p.18 for it.
- No noisy release is made. `query()` returns exact aggregates, so the δ gate is currently a validator on caller-held parameters, not a live privacy guarantee.

## What is new here

The misread is in the δ gate, and it is small but specific. The book says δ on the order of 1/‖x‖₁ is dangerous and gives the reason (publishing a few full records, which is the "just a few" failure of Section 1). It does not set a threshold. The `δ·n ≥ 0.1` cutoff is the plant's own calibration, chosen so the test `is_valid_dp_params(1.0, 0.001, n=100) == False` holds. The code comment presents that cutoff as a reading of p.18. A reader who trusts the citation would take a numeric rule as the book's rule, and a future change to the 0.1 would look like a change to the source.

The second point is a nuance the book itself supports and the module does not state. The book's δ warning concerns the *size* of the database, but the module's `n` argument is only a parameter the caller supplies. Nothing ties `n` to the data the query actually ranges over, so the gate is only as honest as the caller's `n`. This is a boundary fact to write down, not a defect in the current code, since no caller passes `n` today.

Neither point changes the arithmetic. `group_privacy_epsilon`, `dp_composition` and `laplace_scale` already agree with the book's statements. The misread is in the citation and the threshold label, not the formulas.

## Application

target: src/mishe_tauftauf/stats_boundary.py

Proposal to senses (owner of the module; not edited here):

1. In the comment above the `delta * nn >= 0.1` branch of `is_valid_dp_params`, state that 0.1 is a plant choice and not from Dwork & Roth. Keep the citation to p.18 only for the qualitative warning ("order of 1/‖x‖₁ very dangerous").
2. Add to the docstring that `n` is caller-asserted dataset size. The gate bounds δ against that `n` and does not verify it.
3. One pinned test for the two facts the module already gets right and the book states exactly: `group_privacy_epsilon(epsilon=0.5, k=3) == 1.5` (Theorem 2.2), and the (ε,δ) δ-multiplier note in that docstring unchanged.

Disposition: senses keeps or revises the wording. Trigger for re-reading: a changed `stats_boundary.py` at a new SHA, or a caller that passes `n` to `is_valid_dp_params`.
