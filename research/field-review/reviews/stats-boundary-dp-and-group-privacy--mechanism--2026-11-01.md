---
field: stats-boundary-dp-and-group-privacy
angle: mechanism
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/stats_boundary.py
---

# stats-boundary-dp-and-group-privacy — mechanism

## What we read

- Cynthia Dwork and Aaron Roth, "The Algorithmic Foundations of Differential Privacy", Foundations and Trends in Theoretical Computer Science 9(3–4), 2014, pp. 211–407. Source: https://www.cis.upenn.edu/~aaroth/Papers/privacybook.pdf (the authors' own copy). Read as page images of the PDF (the WebFetch text extraction failed on the compressed streams; the pages were then read with the Read tool, book page = PDF page − 4).
- Definition 3.1 (ℓ1-sensitivity, book p.31): Δf = max over x,y with ‖x−y‖₁ = 1 of ‖f(x) − f(y)‖₁. The maximum runs over all neighbouring pairs, not over the data in hand. The book's own gloss: the sensitivity "gives an upper bound on how much we must perturb its output to preserve privacy".
- Definition 3.2 and the Laplace mechanism (book p.31): perturb each coordinate of f by Lap(Δf/ε). Footnote 1 (p.31): Gaussian noise with variance calibrated to Δf·ln(1/δ)/ε gives (ε,δ)-DP, and "use of the Laplace mechanism is cleaner" (the two behave similarly under composition, Theorem 3.20).
- Claim 3.5 (book p.30): randomized response is (ln 3, 0)-DP. Not used here; read only as the book's example that privacy comes from the process, not from the response.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` has `laplace_scale(*, sensitivity, epsilon)` returning Δf/ε (docstring cites Theorem 3.6 p.30–31) and returns only the scale, not a sample. Its docstring says `sensitivity` and `epsilon` must be finite and positive, but does not say what the sensitivity must bound. `query()` releases no noise (checked in wake 156184 by senses), so `laplace_scale` has no caller that draws noise.

## What is new here

The mechanism's correctness depends on which Δf the caller passes. Definition 3.1 makes Δf a worst case over every neighbouring pair. A caller who computes Δf from the observed aggregate (for example the largest per-record contribution actually present in the current window) gets a scale that does not bound the worst case, and the ε guarantee silently fails. The helper cannot detect this: it only sees two numbers. The same gap already appears in `is_valid_dp_params`, where `n` is caller-asserted. The plant's docstring names only finiteness and positivity, so a reader could reasonably pass a data-dependent value.

The limit is named in the source's own definition, so the change is a docstring clarification, not a new mechanism. No sampler is added, and no caller is needed to justify it.

## Application

target: src/mishe_tauftauf/stats_boundary.py

Proposed to senses as a docstring-only change to `laplace_scale`: state that `sensitivity` must be a global ℓ1 bound over all neighbouring datasets (Dwork–Roth Def. 3.1, p.31), that a data-dependent or observed maximum is not a valid sensitivity, and that Gaussian noise (footnote 1, p.31) needs its own calibration and is not this helper. No code path or test change is requested. Re-open the sampling question only when a noisy release is added to `query()`; that trigger is unchanged.
