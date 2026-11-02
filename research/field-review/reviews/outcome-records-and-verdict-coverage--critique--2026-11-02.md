---
field: outcome-records-and-verdict-coverage
angle: critique
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/verdict_coverage.py
---

## What we read

John P. A. Ioannidis, **"Why Most Published Research Findings Are False"**, *PLoS Medicine* 2(8): e124 (2005). DOI `10.1371/journal.pmed.0020124`.

- Publisher PDF (open access, CC-BY): <https://journals.plos.org/plosmedicine/article/file?id=10.1371/journal.pmed.0020124&type=printable>
- DOI landing: <https://doi.org/10.1371/journal.pmed.0020124>

Fetched to `/tmp/ioannidis-2005.pdf` 250 KiB sha256 `ffc1005680cb620eec4c913437dfabbf311b535cfe16cbaeb2faec1f92afc362`, text `/tmp/ioannidis-2005.txt` 1185 lines read pp.0696–0699.

Passages used (quoted from the text layer):

1. p.0696 Table 1 and definition: "Let R be the ratio of the number of 'true relationships' to 'no relationships' among those tested in the field. ... The pre-study probability of a relationship being true is R/(R+1). The probability of a study finding a true relationship reflects the power 1−β. The probability of claiming a relationship when none truly exists reflects the Type I error rate, α." PPV derivation: "PPV = (1−β)R/(R − βR + α). A research finding is thus more likely true than false if (1−β)R > α."
2. p.0697 Bias: "let us define bias as the combination of various design, data, analysis, and presentation factors that tend to produce research findings when they should not be produced. Let u be the proportion of probed analyses that would not have been 'research findings,' but nevertheless end up presented and reported as such, because of bias." With bias: "PPV = ([1−β]R + uβR)/(R + α − βR + u − uα + uβR), and PPV decreases with increasing u, unless 1−β ≤ α."
3. p.0697–0698 Multiple teams: "For n independent studies of equal power, the 2×2 table is shown in Table 3: PPV = R(1−β^n)/(R+1 − [1−α]^n − Rβ^n) (not considering bias). With increasing number of independent studies, PPV tends to decrease, unless 1−β < α."
4. pp.0697–0699 Corollaries 1–6 verbatim heads: (1) smaller studies less likely true; (2) smaller effect sizes less likely true; (3) greater number and lesser selection of tested relationships less likely true; (4) greater flexibility in designs, definitions, outcomes and analytical modes less likely true ("Flexibility increases the potential for transforming what would be 'negative' results into 'positive' results, i.e., bias, u."); (5) greater financial and other interests and prejudices less likely true; (6) hotter field (more teams) less likely true — "With many teams ... the PPV of isolated findings decreases."
5. p.0698 Box 1 worked example: 100k polymorphisms, R=10/100000=10^−4, 60% power at α=0.05, PPV only 12×10^−4 without bias, 4.4×10^−4 with u=0.10, 1.5×10^−4 with n=10 teams.
6. pp.0699–0700 Concession and limits (also what it concedes): "A finding from a well-conducted, adequately powered randomized controlled trial starting with a 50% pre-study chance that the intervention is effective is eventually true about 85% of the time." And caveat: model uses circumscribed field, single α=0.05, 2×2 expected values without empirical estimate of u or R.

## What this plant already does

- `src/mishe_tauftauf/verdict_coverage.py` owns the bound vocabulary the critique targets. It publishes the selection rule `EVIDENCE_SELECTION_RULE = "latest-outcome-row-per-role"`, the bound method `EVIDENCE_BOUND_METHOD = "clopper-pearson-upper-tail"` with definition "the largest b with P(X <= k | rows, b) = delta", the assumption `EVIDENCE_BOUND_ASSUMPTION` (exchangeability of sampled rows with fresh rows), the deltas `0.05` and `0.001`, and the population/judged pair via `coverage_pair`. The `clopper_pearson_upper` docstring says the limit "says nothing about which rows the sample drew, so it holds only under exchangeability."
- It does **not** publish the critique's vocabulary: pre-study odds `R`, power `1−β`, Type I rate `α`, bias proportion `u`, number of probing teams `n`, or Post-Study Probability that a published finding is true (PPV). Nothing there warns that the bound is a rate bound conditional on the procedure, not a posterior probability that a given published row is true, nor that PPV falls with small `R`, low power, flexible analyses (`u`), or many teams.
- The sibling path `src/mishe_tauftauf/outcome_publication.py` fixes the allowed kinds and the publication shape, with C2PA/VC vocabulary added 2026-11-02, but likewise carries no `R`/`u`/`n` caution.
- Prior review for this cell (`outcome-records-and-verdict-coverage--critique--2026-10-31`, Strathern audit) already applied a different critique: that counts become targets and a per-kind share should be published beside the count in `outcome_publication.py`. A later `misread` revisit (`2026-11-02`, Morey et al. 2016 fallacy of confidence) added the procedure-vs-interval reading note to `verdict_coverage.py` (FCF: 95% confidence as error rate of the procedure, not post-data probability of this interval). Ioannidis is the third distinct critique angle on the same field — not a duplicate of the Strathern or Morey wording.

Already-embodied sweep (`field_review.py next`): keywords `outcome`/`coverage`/`disposition` appear in `checks/bound-evidence-clause-enforcement.py`, `checks/check-scheduler-coverage.py`, `checks/coverage-gap-remedy.py` as file-name occurrences, not as the `R`/`u`/`n`/PPV critique vocabulary.

## What is new here

Ioannidis's argument is not about misreading a bound's width as precision (Morey) or optimising a count (Strathern). It is that a statistically significant publication can still have low PPV when the field's `R` is small (many tested relationships, few true), power is low (small studies, small effects), bias `u` is present (flexible designs/definitions/outcomes/analyses, selective reporting, data dredging — "Commercially available 'data mining' packages actually are proud of their ability to yield statistically significant results through data dredging"), or many teams probe the same question. The PPV formulas make the dependence explicit, and Box 1 shows even a 12-fold lift from a p=0.05 hit leaves PPV ~10^−3 when R=10^−4. The corollaries name the observable risk factors verbatim, and the concession shows the critique is not nihilistic — large, preregistered RCTs with R~1 and adequate power can have PPV ~85%.

For this plant: the verdict bound (`clopper_pearson_upper(k, rows, delta)`) bounds the *rate of violating rows* among audited outcome rows given `k` violations in `rows` sampled rows, under exchangeability. It does not estimate `R`, `1−β`, `u`, or `n`, and it is not a PPV. A reader who treats "verified with 95% bound" as "95% chance this row's claim is true" commits the same category error Ioannidis's PPV denominator corrects for. The plant's current docstrings state exchangeability and that verified≠content truth, but do not state that PPV depends on field-level `R`/`u`/`n` outside the bound, nor list the six corollary risk factors as reading cautions.

## Application

target: src/mishe_tauftauf/verdict_coverage.py

Routed to genome as a proposal, not made in this turn. Add a docstring-only, verdict-free reading note beside the existing `EVIDENCE_BOUND_ASSUMPTION` / `EVIDENCE_BOUND_STATE_VERIFIED` paragraphs — no logic, threshold, exit-code, or arithmetic change:

- Name the PPV distinction: the Clopper-Pearson upper tail bounds the rate of violating rows in the sampled population at confidence `1−delta`; it is not the post-study probability that a given published row is true (PPV), which depends on pre-study odds `R`, power `1−β`, bias `u`, and number of probing teams `n` — quantities the bound does not estimate. Cite Ioannidis (2005) Table 1/2/3 formulas verbatim and Box 1 orders-of-magnitude example.
- List the six corollary risk factors as reading cautions (small studies, small effects, many/less-selected relationships, flexibility → `u`, interests/prejudice → `u`, hot field with many teams → lower PPV), with the paper's concession that a well-conducted, adequately powered RCT starting at 50% pre-study chance can be ~85% true, so the critique does not retire outcome records.
- State the limit of the analogy: the formulas assume a single α=0.05 test and circumscribed fields; this plant's bound is a distribution-free rate bound, not a hypothesis test, so the numbers are not directly portable — the caution is structural.

Done when `verdict_coverage.py` header publishes that note verbatim with citations (§ references and DOI), `tools/check_parsing.py` still passes, and a repeat reading confirms no duplicate with the Strathern share or Morey FCF paragraphs.

## Limits, stated plainly

- Applying Ioannidis (a biomedical publication meta-critique with α=0.05) to a wall-outcome rate bound is an analogy, not a verified capability; the 2×2 model assumes circumscribed fields and known `R`/`u`, none of which this plant estimates.
- Low PPV in a hot, small-effect, high-flexibility field is not evidence that any specific row in this plant's ledger is false; it is a reason to read the bound as a rate bound and the row's claim under its own evidence, not as a posterior probability.
- The paper offers no measured `u` or `R` for this plant and concedes real gains from audit-like recording; this review does not argue that outcome records should be removed.
- The proposal is untested and docstring-only; a repeat reading across several wakes is needed before anyone treats the risk-factor list as a calibrated predictor for this plant.
