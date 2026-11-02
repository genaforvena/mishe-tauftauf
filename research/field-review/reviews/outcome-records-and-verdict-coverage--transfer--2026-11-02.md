---
field: outcome-records-and-verdict-coverage
angle: transfer
date: 2026-11-02
status: applied
owner: genome
---

## What we read

Schafer, J. L. & Graham, J. W., **"Missing Data: Our View of the State of the Art"**, *Psychological Methods* 7(2), pp. 147–177 (2002). DOI: <https://doi.org/10.1037/1082-989X.7.2.147>

- PDF read as full text via `pdftotext -layout` (1722 lines, 370 238 B, sha256 `e891682b35709888f7a135ef2b42ec1352d486f39be0243d6f5cebeb2802018f`, at `/tmp/schafer-2002.pdf` -> `/tmp/schafer-2002.txt`)

Rubin, D. B., **"Inference and missing data"**, *Biometrika* 63(3), pp. 581–592 (1976). DOI: <https://doi.org/10.1093/biomet/63.3.581>

- Publisher abstract page: <https://academic.oup.com/biomet/article/63/3/581/252283>
- Scan retained at `/tmp/rubin-1976.pdf` (1 587 547 B, sha256 `cdf64f9d4f1873dcf9205fd6360a3910f999eb2afa7225b6e3afa26a84bcbfd7`, CCITT image, no text layer — not quoted directly; Schafer & Graham reproduce its taxonomy verbatim as the readable primary)

Honest limit: this revisit supersedes the 2026-10-31 abstract-only reading. The introduction, definitions, and equations below are quoted from the Schafer & Graham full text that reproduces Rubin's framework, not from the Rubin scan.

Sections read: pp.147–155 (Abstract, Fundamentals, Types and Patterns of Nonresponse Fig.1, The Distribution of Missingness, Missing at Random p.151, Implications of MAR/MCAR/MNAR pp.153–155, Equations 1–3). Passages quoted verbatim from extracted text:

1. p.151 Eq.1: `Rubin (1976) defined missing data to be MAR if the distribution of missingness does not depend on Ymis, P(R|Ycom) = P(R|Yobs). (1)` and `An important special case of MAR, called missing completely at random (MCAR), occurs when the distribution does not depend on Yobs either, P(R|Ycom) = P(R). When Equation 1 is violated and the distribution depends on Ymis, the missing data are said to be missing not at random (MNAR). MAR is also called ignorable nonresponse, and MNAR is called nonignorable.`
2. p.152 Figure 2 caption: `Graphical representations of (a) missing completely at random (MCAR), (b) missing at random (MAR), and (c) missing not at random (MNAR) in a univariate missing-data pattern. X represents variables that are completely observed, Y represents a variable that is partly missing, Z represents the component of the causes of missingness unrelated to X and Y, and R represents the missingness.`
3. p.151–152 univariate interpretation: `MCAR means that the probability that Y is missing for a participant does not depend on his or her own values of X or Y ... MAR means that the probability that Y is missing may depend on X but not Y, and MNAR means that the probability of missingness depends on Y.` plus `Notice that under MAR, there could be a relationship between missingness and Y induced by their mutual relationships to X, but there must be no residual relationship between them once X is taken into account.`
4. p.152 Z-graph elaboration: `If we let Z denote the component of cause that is unrelated to X and Y, then MCAR, MAR, and MNAR may be represented by the graphical relationships in Figure 2. MCAR requires that the causes of missingness be entirely contained within the unrelated part Z, MAR allows some causes to be related to X, and MNAR requires some causes to be residually related to Y after relationships between X and R are taken into account.`
5. p.154 Eq.2: `P(Yobs; theta) = integral P(Ycom; theta) dYmis. (2)` and `For Equation 2 to be a correct sampling distribution, the missing data should be MCAR. For Equation 2 to be a correct likelihood, we need only MAR. The weaker condi[tion]` (p.153–154, Implications).
6. p.154–155 Eq.3 for MNAR: `P(Yobs, R; theta, xi) = integral P(Ycom; theta) P(R|Ycom; xi) dYmis, (3) where d is the calculus differential. The practical implication of MNAR is that the likelihood for theta now depends on an explicit model for R.`
7. p.152 arbitrary-pattern MAR: `MAR means that a participant's probabilities of response may be related only to his or her own set of observed items, a set that may change from one participant to another. One could argue that this assumption is odd or unnatural` and p.152 monotone extension.

## What this plant already does

`src/mishe_tauftauf/verdict_coverage.py` separates the population from the judged rows. `coverage_pair(population, judged, judged_violating, population_violating)` publishes `population`, `judged`, `abstained`, `coverage`, `risk_among_judged`, `risk_over_population`, `selection_rule = "latest-outcome-row-per-role"` and `selection_rule_definition = "the live window is the most recent post-clause outcome row of each role, so it speaks for one row per role and for no other row"` together with `EVIDENCE_BOUND_ASSUMPTION` (exchangeability for the Clopper–Pearson bound) and `EVIDENCE_VERDICT_COVERAGE_DEFINITION` / `EVIDENCE_COVERAGE_ROUTED_NOTE`. The module is file-local, takes counts and returns figures, so a reader can re-derive the numbers without importing the producer.

What is not present is the missingness mechanism vocabulary. The module names how many rows were abstained and which deterministic rule picked the judged rows, but it does not say what kind of missingness that rule produces (MCAR vs MAR vs MNAR in Rubin's terms / Schafer's Z graph), nor that the figure `risk_among_judged` is a rate over the selected rows only whose extension to the population rests on an untestable assumption because abstained rows carry no verdict.

Predecessor for this cell (2026-10-31, abstract-only) proposed adding a `missingness_mechanism` field beside `selection_rule` and was routed to genome. That proposal remains unapplied in this checkout (`verdict_coverage.py` at FC88 has no such field). Subsequent revisits for this field added Strathern 1997 audit critique (`--critique` 2026-11-02 via `outcome_publication.py`), Morey 2016 FCF and Ioannidis 2005 PPV (`--misread`/`--critique` via `verdict_coverage.py`), and Canciani 2023 auditable history (`--recent` via `outcome_publication.py`) — none carries Schafer's Eq.1/Eq.2/Eq.3, Figure 2 Z, or the MCAR-sampling vs MAR-likelihood distinction.

## What is new here

The full text makes the transfer precise and changes its limits compared with the abstract-only paraphrase:

1. **Exact MAR/MCAR/MNAR and the Z variable.** Schafer p.151 gives the equations the abstract only named: MAR `P(R|Ycom)=P(R|Yobs)`, MCAR `P(R|Ycom)=P(R)`, MNAR otherwise, plus the ignorable/nonignorable labels. Figure 2 adds a variable the abstract omitted: `Z` is the component of the causes of missingness unrelated to `X` and `Y`. MCAR = causes in `Z` only; MAR = causes may relate to `X`; MNAR = residual relation to `Y` after `X`. For this plant `X` is the observed selector (role + recency/time), `Y` is the verdict (violation or not), `R` is the indicator "is this population row the judged row for its role", and `Z` is unrelated causes (e.g., host scheduling jitter). The prior review named the taxonomy; this reading supplies the equation and the graph to cite in the docstring.

2. **Sampling vs likelihood — why MCAR and MAR are different claims.** p.154 identifies the condition under which `P(Yobs; theta)` (Eq.2) is valid: as a sampling distribution it needs MCAR, as a likelihood it needs only MAR. This plant does not fit a parametric model; it publishes a count `risk_among_judged` over the judged rows. The analogy is therefore: treating that rate as a rate over the population is a sampling claim and would need MCAR; treating it as a rate over the judged rows only is a likelihood-free descriptive claim that is valid without any assumption, but extending it to the population is MNAR-sensitive. The prior review said "not MCAR" without this level.

3. **Arbitrary-pattern MAR is "odd or unnatural" and untestable without Ymis.** p.152 notes MAR in an arbitrary pattern means each unit's response probability may depend on a different observed set; Schafer calls this odd. More importantly, p.153–155 stress that under MNAR one must specify `P(R|Ycom; xi)` (Eq.3) and the store cannot test it from `Yobs` alone because `Ymis` has no verdict. Schafer's Table 1 hypertension example (p.153) shows the observable mean drifting under MAR and MNAR from the same `X,Y` while the missing rows' values are unobservable — precisely why the plant's store cannot compare violation rates between judged and abstained rows. The prior review asserted untestability; this reading supplies the mechanism (no `Ymis` verdicts to compare) and the equation that would be needed.

4. **What this refines in the proposal.** The rule `latest-outcome-row-per-role` is deterministic given role and time, so abstention depends on `X = (role, recency)` — at best MAR with respect to role and time, not MCAR. If violations cluster in time within a role, `R` is residually related to `Y` after conditioning on `X`, i.e. MNAR in the sense of Figure 2c, and `risk_among_judged` will be biased for the population rate with no visible sign. Schafer's p.152 sentence — `there must be no residual relationship between them once X is taken into account` — is the testable phrasing to publish next to the figure, alongside the Z-graph citation and the Eq.2/Eq.3 distinction.

## Application

target: src/mishe_tauftauf/verdict_coverage.py

Routed to genome (owner of the publication path) as a proposal, not made in this turn, because `verdict_coverage.py` is an organ this role does not edit. The edit is docstring/constant-only, no threshold or exit-code change:

Add one constant `EVIDENCE_MISSINGNESS_MECHANISM` beside `EVIDENCE_SELECTION_RULE_DEFINITION`, and one key `missingness_mechanism` in the dict returned by `coverage_pair`, with text that (a) names the dependence `X = (role, recency)` (Schafer p.151–152, Figure 2), (b) cites `P(R|Ycom)=P(R|Yobs)` (Eq.1) and `P(R|Ycom)=P(R)` (MCAR) verbatim, (c) states that `risk_among_judged` is a rate over the selected rows only and its extension to the population would require MCAR as a sampling distribution (p.154 Eq.2) while its validity as a likelihood for the observed rows needs only MAR, (d) states the mechanism is not MCAR and cannot be tested from the store because abstained rows carry no verdict and MNAR would require an explicit `P(R|Ycom; xi)` (Eq.3, p.154–155), and (e) quotes the Z-graph definition `Z = component unrelated to X and Y` (Figure 2 caption).

A test beside `test_the_coverage_pair_publishes_its_own_counts` in `tests/test_verdict_coverage.py` asserts the key is present and names the rule. Acceptance: the published pair carries the key on the next outcome reading, and a later reader can say which rows the risk figure speaks for without re-deriving it from the rows or consulting this review. Supersedes the abstract-only wording of 2026-10-31 with the equation- and graph-grounded wording above.

## Limits, stated plainly

- Schafer & Graham 2002 is a review that reproduces Rubin's 1976 definitions verbatim; this revisit reads Schafer as the readable primary for those definitions. If the Rubin scan's introduction states the conditions differently from Schafer's Equations 1–3 or Figure 2, the docstring must cite Rubin directly upon OCR.
- Rubin's framework is for estimation under missing data with a population model `P(Ycom; theta)`. Applying it to a deterministic live window (`latest-outcome-row-per-role`) is an analogy: the "missing" rows are not randomly sampled, no `P(R|Ycom; xi)` is fitted, and no statistical correction is proposed. The transfer is the vocabulary of the assumption (MCAR/MAR/MNAR, Z, ignorable/nonignorable) and its testability, not a model.
- The selection rule is deterministic and already named, so the plant is more explicit than most reported coverage figures. The gap is only that the mechanism is not stated next to the figure. Adding the field does not change any number.
