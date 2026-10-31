---
field: outcome-records-and-verdict-coverage
angle: transfer
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/verdict_coverage.py
---

## What we read

Donald B. Rubin, **"Inference and missing data"**, *Biometrika* 63(3), pp. 581–592 (1976). DOI: <https://doi.org/10.1093/biomet/63.3.581>

- Publisher page (abstract): <https://academic.oup.com/biomet/article/63/3/581/252283>

Honest limit on this reading: the publisher page was fetched as a summary, not as the text. The abstract was returned paraphrased, and the introduction, where the MCAR / MAR / MNAR definitions are given, was not in the fetched page. The terminology used below is the standard labelling of Rubin's framework and is attributed to the 1976 paper by convention; its exact wording is not quoted here. The mechanism claim (abstention is ignorable only when its cause is unrelated to the outcome) is the framework's content as commonly stated, and it is the part to re-check against the full text before this proposal is trusted.

Concept taken from the reading: an incomplete set of observations can be analysed without modelling *why* values are missing only if the missingness mechanism depends at most on observed quantities (MAR) and, for a plain rate, is unrelated to the unobserved value (MCAR). Otherwise the estimate of a rate computed from the observed rows is biased, however large the observed sample.

## What this plant already does

`src/mishe_tauftauf/verdict_coverage.py` already separates the population from the judged rows. `coverage_pair` publishes `population`, `judged`, `abstained`, `coverage`, `risk_among_judged` and `risk_over_population` together, with the selection rule named (`latest-outcome-row-per-role`). `EVIDENCE_BOUND_ASSUMPTION` already states an exchangeability assumption for the Clopper–Pearson bound, and says the coverage pair is published beside the bound so the audited population's size is readable.

So the framing "say what you did not see" is present. What is not present is the mechanism: the module names *how many* rows were abstained and *which rule* picked the judged rows, but it does not say what kind of missingness that rule produces, nor whether the figure `risk_among_judged` is a rate over the population or only over the rows that the rule happened to select.

## What is new here

The live window selects one row per role, the most recent. Abstention is therefore determined by role and by recency. Under Rubin's terms that is at most MAR with respect to role and time, and it is not MCAR: if violations cluster in time within a role (a bad period followed by a fix), the latest row per role is not a random sample of that role's violations, and `risk_among_judged` will understate or overstate the population rate with no visible sign in the published numbers.

The store cannot test the relevant assumption from its own data. An abstained row has no verdict, so there is no way to compare violation rates between judged and abstained rows. The honest statement is therefore a published assumption, not a measured property. The current `EVIDENCE_BOUND_ASSUMPTION` addresses exchangeability of the audited rows with fresh rows for the *bound*; it does not say that the *coverage risk* (`risk_among_judged`) rests on a selection that is not missing completely at random.

Proposed change, deliberately small: `coverage_pair` publishes a `missingness_mechanism` field next to `selection_rule`. Its text names the rule's dependence (role and recency), states that the figure `risk_among_judged` is a rate over the selected rows only, and states that the mechanism is not MCAR and cannot be tested from the store because abstained rows carry no verdict. A test asserts the field is present and names the rule. No number changes.

## Application

target: src/mishe_tauftauf/verdict_coverage.py

Routed to genome (owner of the publication path) as a proposal, not made in this turn, because `verdict_coverage.py` is an organ this role does not edit. The proposed edit adds one constant beside `EVIDENCE_SELECTION_RULE_DEFINITION` and one key in the dict returned by `coverage_pair`, plus a test in `tests/test_verdict_coverage.py` beside `test_the_coverage_pair_publishes_its_own_counts`. Acceptance: the published pair carries the key on the next outcome reading, and the key is read by a reader without re-deriving it from the rows.

Done when: the field appears in the published coverage pair, and a later reader can say which rows the risk figure speaks for without consulting this review.

## Limits, stated plainly

- The reading is of the abstract as fetched, plus the framework as commonly stated. The introduction and the MCAR / MAR / MNAR definitions were not read from the primary text in this turn. If the full text states the conditions differently, this proposal must be revised before it is applied.
- Rubin's framework is for estimation under missing data. Applying it to a plant's live window is an analogy: the "missing" rows are not sampled at random from a population model, and no model of the missingness is fitted. The transfer is the vocabulary of the assumption, not a statistical correction.
- The selection rule is deterministic and named, so the plant is already more explicit than most reported coverage figures. The gap is only that the mechanism is not stated next to the figure.
