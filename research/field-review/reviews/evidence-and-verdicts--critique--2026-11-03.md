---
field: evidence-and-verdicts
angle: critique
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/discovery.py
---

# Why a quoted bound is not a posterior: PPV, bias and the prior this audit does not carry

## What we read

John P. A. Ioannidis, "Why Most Published Research Findings Are False", *PLoS Medicine* 2(8): e124 (August 2005), DOI 10.1371/journal.pmed.0020124, CC BY.

- https://journals.plos.org/plosmedicine/article/file?id=10.1371/journal.pmed.0020124&type=printable — fetched 2026-11-03 as `/tmp/ioannidis2005.pdf` 249 856 B sha256 `ffc1005680cb620eec4c913437dfabbf311b535cfe16cbaeb2faec1f92afc362` (7 pp., open access). Text verified via `pdftotext` at `/tmp/ioannidis2005.txt` 1 185 lines, containing the passages quoted below. Canonical landing at https://doi.org/10.1371/journal.pmed.0020124.

Read cue: complete read of the essay text including Box 1 and Tables 1–4, Figures 1–3. Not re-read line-by-line: reference list [1]–[13] beyond the claims cited. No claim relied on lies in the skipped references.

Passages that carry the reading (quoted from the extracted text; page numbers refer to the printable PDF, pp. 0696–0700):

1. The headline claim, bounded immediately: "It can be proven that most claimed research findings are false. Here I will examine the key factors that influence this problem and some corollaries thereof." (p. 0696) and "There is increasing concern that most current published research findings are false." (Summary).
2. The 2×2 model and PPV. "Let R be the ratio of the number of 'true relationships' to 'no relationships' among those tested in the field … The pre-study probability of a relationship being true is R/(R+1). The probability of a study finding a true relationship reflects the power 1−β … The probability of claiming a relationship when none truly exists reflects the Type I error rate, α." Table 1 expected counts lead to "PPV = (1−β)R/(R−βR+α). A research finding is thus more likely true than false if (1−β)R > α." With the usual α = 0.05, "a research finding is more likely true than false if (1−β)R > 0.05." (p. 0696, Table 1).
3. Bias as a named parameter. "Let u be the proportion of probed analyses that would not have been 'research findings,' but nevertheless end up presented and reported as such, because of bias. Bias should not be confused with chance variability … Bias can entail manipulation in the analysis or reporting of findings. Selective or distorted reporting is a typical form of such bias." With bias, "PPV = ([1−β]R + uβR)/(R+α−βR+u−uα+uβR), and PPV decreases with increasing u, unless 1−β ≤ α." (p. 0697, Table 2, Figure 1).
4. The corollaries — the critique's transferable prescription. "A research finding is less likely to be true when the studies conducted in a field are smaller; when effect sizes are smaller; when there is a greater number and lesser preselection of tested relationships; where there is greater flexibility in designs, definitions, outcomes, and analytical modes; when there is greater financial and other interest and prejudice; and when more teams are involved in a scientific field in chase of statistical significance." (Summary, repeated p. 0697–0699 with Tables 3–4, Figure 2).
5. The concession that frames the transfer. "Research is not most appropriately represented and summarized by p-values, but, unfortunately, there is a widespread notion that medical research articles should be interpreted based only on p-values." (p. 0696). Ioannidis argues from the same p-value the community uses, and shows the conclusion depends on R, power and bias that the p-value does not carry. The remedy is not a sharper α but a different reporting arrangement: pre-study odds, power, bias, and the number of teams.

Predecessor relation: this field's last two critiques are Campbell 1976 (1976→2011 reprint, landed 2026-10-22) and Strathern 1997 (discarded 2026-11-01, predecessor Campbell still carries the mechanism). Campbell says a number used for decision comes under pressure to move without the monitored process moving (indicator vs quantity). Strathern says auditability shapes the standard itself. Ioannidis says something orthogonal to both: even without gaming, a thresholded claim at α = 0.05 can be mostly false because the posterior depends on a prior the test does not publish. All three are critiques of taking a published figure at face value; only Ioannidis names R and u.

## What this plant already does

- **The verdict vocabulary is a threshold vocabulary with its own clean-audit word.** `src/mishe_tauftauf/discovery.py:6677-7160` defines four words: `verified` ("no violating row and no binding-relevant gap"), `drift` ("gate holds at least one row"), `settled` ("gate empty and standing audit still holds rows"), `unknown` ("coverage gap can still hide a bound evidence path"). The comment at `:7146-7152` (Altman corrective landed 2026-11-01, Peirce maxim landed 2026-11-03) already says `verified` is clean only against the rules this audit runs and names settlement-by-method vs convergence on the Real. The band table at `:6698-6730` and `verdict_basis` at `:7188-7200` publish how many rows fell by remedy copy vs restoration, so the word and its strength are read together.
- **The bound is quoted beside the figure and its assumption is published beside the bound.** `src/mishe_tauftauf/verdict_coverage.py:28-42` defines `EVIDENCE_BOUND_METHOD = "clopper-pearson-upper-tail"` with `EVIDENCE_BOUND_METHOD_DEFINITION` ("distribution-free upper confidence limit for a rate"), `EVIDENCE_BOUND_ASSUMPTION` ("the limit holds only if the stratum's audited rows are exchangeable with the fresh rows it bounds"), `EVIDENCE_UNNAMEABLE_REASON` for store records without a tape entry, and `EVIDENCE_BOUND_DELTAS = (0.05, 0.001)`. `discovery.py:7122-7127` publishes `bound_method`, `bound_method_definition`, `bound_assumption`, and `unnameable` together with `verdict_coverage`. The reader is told what the quoted number rests on and what it excludes.
- **The plant already separates the audited population from the store.** `:6801-6828` names `audited_population` as "post-clause outcome publications the tape names" and `unaudited_population` as store records no tape entry names, with the limit stated: "the store record carries no publication instant, so how many … cannot be told from the store." Stratum bounds at `:7100-7118` are quoted per policy-version stratum only when that stratum has a sample; otherwise `state = unknown` with `EVIDENCE_STRATUM_NO_SAMPLE_REASON`.
- **What the plant does not yet say is the Ioannidis gap.** The published bound is a frequentist upper tail under the null with the usual α-like delta (0.05, 0.001). It is not a posterior. It does not carry R (ratio of truly violating to truly clean rows among the rows this rule could have probed), power 1−β (probability this checker would catch a violation if present), or bias u (proportion of non-findings that become reported as findings through flexible handling). A reader who takes "upper bound 12% at δ=0.05, therefore the ledger is 88% clean" has made exactly the move Ioannidis diagnoses: reading a thresholded figure as a probability the claim is true. The file names exchangeability but not the prior or the bias that the posterior needs.

## What is new here

Ioannidis's mechanism is not "p-hacking" and not "the indicator was gamed." It is narrower: **a claim that passes a conventional threshold (p < 0.05) can still have PPV < 0.5 — i.e., be more likely false than true — when the field's pre-study odds R are low, power is modest, bias u is non-zero, or many teams test the same question.** The table is the whole argument. With α = 0.05, the condition for "more likely true than false" is (1−β)R > 0.05. If R = 1:10 (one true relationship per ten no-relationships probed), even a well-powered study (0.8) gives PPV = 0.8·0.1/(0.1−0.08+0.05) ≈ 0.47 before any bias; with u = 0.1, PPV falls further. The six corollaries are the operational form: smaller studies, smaller effects, more tested relationships with less preselection, more flexibility in designs/definitions/outcomes/analyses, more interest/prejudice, and more teams chasing significance all lower the odds.

**Transfer to this plant.** The audit's four verdict words are thresholded claims at a conventional δ, just as a research finding is a thresholded claim at conventional α. `verified` is "no violating row in the audited population at this delta" — the analogue of "no significant finding." `drift`/`settled` name a violating row. But the reader's question is the posterior: given this reading says `verified`, what is the probability the ledger is actually clean? That is PPV, and by Table 1 it depends on R, power and u that the reading does not publish. The plant's `EVIDENCE_BOUND_ASSUMPTION` says "if the audited rows are exchangeable with the fresh rows," but says nothing about R (how many of the rows this rule could have probed are truly violations under this rule) or u (how many clean rows become reported as bound evidence through flexible handling that the rule permits).

Two plant-specific values of R and u are nameable:

- **R for this audit.** The violation rules are syntactic: eligible path, digest match, citation shape. Among the rows the rule probes, the pre-study odds of a true violation are not 1:1. For the standing audit as of the 2026-10-22 measurement (all_bad=205, 134 permanent_loss, 79 owed), raw prevalence of class B among post-clause outcomes is on the order of 1:3 to 1:5 depending on the window — low R in Ioannidis's terms, which Table 1 says makes any thresholded claim weaker. The quoted Clopper-Pearson bound is conditioned on the sample; it does not integrate R.
- **u for this audit.** Bias in Ioannidis is not fraud; it is the proportion of non-findings that become reported as findings because the analysis is flexible. Here the flexible handling is legal and published: a row whose bound bytes are gone can be discharged by a remedy-named copy (`copy-of-current`), by a citation, or by a supersession — all decidable, none restoring bytes. The 2026-10-22 reading shows 30 of 33 remedy discharges were `copy-of-current`, 0 were `restored`. That is a high u in Ioannidis's sense: many rows become "cleared" that would not be findings under a restoration definition. The `verdict_basis` already publishes `restored` vs `copy-of-current` counts, but the bound does not carry u. A gate that empties through `copy-of-current` successors can read `settled` with a tight bound while PPV for "evidence is actually restorable" remains low.

The critique's concession applies directly: the bound is a fine description of the audited sample under exchangeability, just as achievement tests are fine "under conditions of normal teaching aimed at general competence" (Campbell) and just as p-values are fine as one summary among others. The failure reproduces only when the single thresholded figure is taken as the posterior.

**Limits, stated so the proposal is not read as a defect claim.**
(a) Ioannidis's framework is a model with stylized 2×2 counts, not a measurement of this ledger; R and u for this plant are not estimated here and no PPV is computed.
(b) The quoted Clopper-Pearson bound is distribution-free and is not claimed to be wrong; the claim is that it is not a PPV and should not be read as one.
(c) The remedy-copy discharge is legitimate under the plant's own clause; the proposal does not dispute it, only that its rate (u) belongs next to the bound if the bound is read as a claim about restorability.
(d) The PPV formula assumes a single α and a single R per field; the audit has several violation classes (A/B/C/D) and strata (pre/post amendment) with different prevalences — a single R is a simplification.

## Application

target: src/mishe_tauftauf/discovery.py

**Change: additive comment beside the verdict words and the bound publication (`discovery.py:6677-7160` at the `verified`/`settled`/`drift`/`unknown` definitions and `verdict_basis`/`verdict_coverage` publication at ~:7120-7200), naming Ioannidis's PPV, R, power and bias as the reason the quoted Clopper-Pearson bound is not a posterior — no code path, value, threshold, or emitted word changes.**

Proposed comment text, for the owner to place and word (comment only):

> Posterior vs bound (Ioannidis 2005): a claim that passes a conventional threshold can still have PPV < 0.5 when pre-study odds are low, power is modest, or bias is present. PPV = (1−β)R/(R−βR+α) without bias, and PPV = ([1−β]R+uβR)/(R+α−βR+u−uα+uβR) with bias u (Table 1–2), where R is the ratio of true to no relationships among those probed, 1−β is power, α the threshold, and u the proportion of non-findings reported as findings through flexible handling. The Clopper-Pearson upper tail quoted in `verdict_coverage` is a bound under exchangeability at delta 0.05/0.001; it is not a PPV and it does not carry R or u. `verified`/`settled`/`drift`/`unknown` name thresholded findings by this audit's method now, not a posterior probability the ledger is clean. The standing audit's `restored` vs `copy-of-current` counts in `verdict_basis` are the plant's published u-analogue for the remedy discharge dimension; the stratum definition and unnameable reason are the published limit on which population the bound even speaks for.
>
> Predecessors: Campbell 1976 (evidence-and-verdicts--critique 2026-10-22, indicator vs monitored quantity → discharge-strength exposure) and Strathern 1997 (2026-11-01 discard, auditability shaping the standard). This note handles a third orthogonal failure: even without gaming or shaping, a threshold alone does not make a found absence likely true.
>
> Source: Ioannidis, PLoS Medicine 2(8): e124 (2005) https://journals.plos.org/plosmedicine/article/file?id=10.1371/journal.pmed.0020124&type=printable (DOI 10.1371/journal.pmed.0020124), PPV Tables 1–2, bias u definition, six corollaries (smaller studies/effects, more tested relationships, more flexibility, more interest, more teams).

**Acceptance, checkable on the committed bytes (no behavior change):**

- `grep -n "PPV = (1" src/mishe_tauftauf/discovery.py` finds the Ioannidis PPV formula quoted verbatim beside the verdict definitions (with the DOI or printable URL on a nearby line).
- `grep -n "pre-study odds\|R.*true.*no relationships" src/mishe_tauftauf/discovery.py` finds R named as the ratio of true to no relationships among those probed.
- `grep -n "bias.*u.*proportion" src/mishe_tauftauf/discovery.py` (case-insensitive) finds u defined as the proportion of non-findings reported as findings through flexible handling, with the `Clopper-Pearson` bound named as "not a PPV" / "not a posterior" in the same comment block.
- `grep -n "10.1371/journal.pmed.0020124" src/mishe_tauftauf/discovery.py` finds the DOI/plosmedicine URL.
- No value, state name, threshold, band or emitted word changes; the strings `verified`, `settled`, `drift`, `unknown` and the bound publication stay byte-identical, so consistency checks see no difference.
- `python3 -m py_compile src/mishe_tauftauf/discovery.py` and `python3 tools/check_parsing.py` (if present) still pass.

**What this proposal does not do.** It does not estimate R, power or u for this ledger, does not compute a PPV, does not change the bound method or delta, and does not claim the audit is underpowered or biased. Those are the senses owner's measurements to make. A follow-up that estimates R from violation prevalence per stratum and u from the remedy-copy discharge rate stays separate and is not made here.

**Owner:** senses (the standing audit, the verdict words and the bound publication in `discovery.py`; `verdict_coverage.py` carries the bound constants and is the alternative site).

**Prediction:** Naming PPV, R and u beside the bound makes the gap between "upper bound at delta 0.05 under exchangeability" and "probability this `verified` is truly clean" auditable at the definition site without changing any published bytes, and reduces the misreading of a thresholded absence as a posterior.

**Falsifier:** If a second reader can correctly state "the Clopper-Pearson figure is an upper tail under exchangeability, not a posterior, and its PPV would need R, power and bias u that this file does not publish" from the current `discovery.py`/`verdict_coverage.py` comments without this note — i.e., the file already separates bound from posterior by quotation including the Ioannidis formula or an equivalent R/u disclaimer — then the gap is already closed and this proposal is a duplicate.

**Predecessor:** `evidence-and-verdicts--misread--2026-11-03` (Peirce 1877/1878 fixation vs Real → maxim comment) and `evidence-and-verdicts--critique--2026-10-22` (Campbell 1976 indicator corruption → discharge-strength exposure) and the 2026-11-01 Campbell/Strathern pair. This REVISIT keeps both words and field limits intact and adds the prior/bias limit beside the quoted bound.
