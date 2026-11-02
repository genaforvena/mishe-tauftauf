---
field: judges-and-external-advice
angle: recent
date: 2026-11-02
status: applied
owner: docs
target: docs/completions-judge.md
---

# Judges and external advice — recent result: pairwise vs pointwise and distracted evaluation (REVISIT)

## What we read

- Tripathi, Tuhina, Manya Wadhwa, Greg Durrett, Scott Niekum, "Pairwise or Pointwise? Evaluating Feedback Protocols for Bias in LLM-Based Evaluation", arXiv:2504.14716v2 [cs.LG] 21 Aug 2025, published as a conference paper at COLM 2025, https://arxiv.org/pdf/2504.14716.pdf -> /tmp/pairwise-pointwise.pdf PDF 484742 B sha256 1257158555afd20ec4c52e9ae37d26ee70c4448b6070f64a54592b8021d7412e (19 pp., arXiv GenPDF tex2pdf, code https://github.com/UMass-SCALAR-Lab/distracted_evaluation). Quotations via `pdftotext -layout` 484742 B; figure/formula text as typeset.
- Verbatim core definitions (headings/quotes as printed, formulas as typeset):
  - §3 Distracted Evaluation p.3: "We refer to this phenomenon as distracted evaluation: a bias towards irrelevant features known as distractor factors, when the evaluator is explicitly instructed to judge based on a well-defined criterion." "For a given prompt x and response y, let p denote the primary criterion … We define a distractor feature f as an extraneous feature that is irrelevant to p. Here, f is a feature that modifies y -> y_f, but the modification does not alter y and y_f under p. Let R(y| x, p) represent the LLM's evaluation … An LLM evaluator exhibits distracted evaluation when … R(y| x, p) != R(y_f | x, p)" (displayed inequality).
  - §3.1 Fixed quality p.4: "R(y| x, p) != R(y_f| x, p) where p(y) = p(y_f)" with three distractor types: "1. Assertiveness – Confident, authoritative phrasing (Hosking et al., 2024). 2. Prolixity – Increased verbosity and lexical complexity. 3. Sycophancy – Overly agreeable or flattering tone (Sharma et al., 2023)."
  - §3.2 Variable quality p.4: "R(y_high | x, p) < R(y_low+f | x, p)" — low-quality + distractor outranking unmodified high-quality.
  - Feedback protocols §3.3 p.4–5: pairwise `R_pairwise(x, y_a, y_b) -> {y_a, y_b, tie}` (two inferences with reversed order, logits for A/B/C aggregated) vs absolute `R_absolute(x, y) -> s where s in {1..7}` weighted by token likelihoods (Prompts 3.1/3.2, Likert 1932).
  - Result p.1/§1/§4: "Pairwise preferences flip in about 35% of the cases, compared to only 9% for absolute scores." "absolute scoring is more robust to such manipulation" (Abstract, Fig.1 caption: pairwise favors assertive B, absolute gives identical 5.5). Datasets: IFEval-TweakSet (Zhou et al. 2023 modified, 3 non-conflicting instructions, content+format split, Table 1) and MT-Bench human-evaluations split (naturalistic multi-turn). Generator exploitation §5: distracted evaluation as leaderboard vulnerability.
  - Limits §2.2 p.3: absolute "may lack comparative information, making it hard to distinguish subtle differences" and "can be inconsistent across different raters and have difficulties in calibration" (Zwislocki 1983). Relative brings amplified tone/style, intransitivity, choice-set effects. Code/data note p.1 fn.1 retained.
- Context check (also read): `docs/completions-judge.md` (pointwise categorical `probability 1`/`0`/unknown, six smoke pairs, order/length/confident-claim replay checks, "categorical conventions, not calibrated probabilities") and `src/mishe_tauftauf/judges.py` `document()`/`controls()` + `src/mishe_tauftauf/completions_judge.py` single-document HTTP.

## What this plant already does

- Chooses the absolute-like pointwise protocol: `src/mishe_tauftauf/completions_judge.py` judges one document per call against one question (yes/no/unknown) and maps to probability 1/0 categorically; `src/mishe_tauftauf/judges.py` `document()` fixes section order QUESTION/INSTRUCTIONS/TOP PAIN/EVIDENCE/[PREDICTION]. No pairwise comparator exists in the judge path.
- Says explicitly that the mapping is "categorical conventions, not calibrated probabilities" (`docs/completions-judge.md:212`), that exit 0 on the twelve smoke controls "is not a calibration or production readiness claim", and that the MT-Bench pairwise position-swap "does not have" a referent here — the order check is described as the pointwise analogue (`docs/completions-judge.md:89-92`).
- Already warns and tests the distractor family this paper names: `docs/completions-judge.md` "confident claim" and length-padding checks (`§ Replay checks`) plus the verbose-negative `desired-state-met` gap noted in the sibling misread review; controls pair substance `reading=42` vs `reading=41` but with no distractor term in the evidence.
- Fail-closed: malformed/refused/truncated/transport-failed -> unknown; no auto-fallback; report records input hashes, latency, usage, returned model.

## What is new here

- A protocol-level measurement that was missing from the plant's rationale: the same distractor feature flips a pairwise judge in 35% of cases but an absolute (pointwise) judge in only 9% (Tripathi et al. §4, averaged over IFEval-TweakSet and MT-Bench). The gap is not asserted; it is measured across fixed-quality (y vs y_f with p(y)=p(y_f)) and variable-quality (y_high vs y_low+f) setups, using the formal condition R(y|x,p) != R(y_f|x,p) and the ranking inversion R(y_high|x,p) < R(y_low+f|x,p).
- The paper's three distractor types map exactly onto the plant's known failure modes: assertiveness -> the "confident claim without SYSTEM ZERO" negative; prolixity -> length-padding; sycophancy -> fluent/overly-agreeable acknowledgment. The paper shows all three exploit the comparative nature of pairwise evaluation ("subtle and often unimportant differences such as tone or style get amplified"), while absolute retains comparative blindness and calibration inconsistency as its own costs (§2.2).
- This does not replace the existing checks. It explains why the plant's choice of pointwise is the robust side of the trade, and why the 9% residual means those checks are still required even after choosing it. The absolute score the paper studies (1..7 Likert, weighted likelihood) is not the plant's categorical 1/0; the reduction to yes/no/unknown loses granularity (harder to distinguish subtle differences, §2.2) but avoids the Likert calibration problem the plant already disclaims. The transfer is the inequality and the flip-rate ordering, not the scale.
- Predecessor distinctness: 2026-10-31 recent = Wataoka et al. arXiv:2410.21819 self-preference bias, abstract-only reading, hypothesis perplexity->familiarity, no PDF extraction, docs target about producer-family recording. This = Tripathi et al. arXiv:2504.14716v2 COLM 2025 19 pp. 125715... pairwise vs pointwise, distracted-evaluation definitions with R(y|x,p) inequalities, three distractors, 35% vs 9% flips over IFEval-TweakSet+MT-Bench, IFEval Table 1, §5 leaderboard exploitation. Different venue/year/authors/pages/formulas/datasets/metric. Only shared family is judges-and-external-advice; the proposed target overlaps (docs/completions-judge.md) because both inform judge docs, but the claim, formula, and evidence are disjoint.

## Application

target: docs/completions-judge.md

Proposed bounded docs change for the owner (docs), additive, no behaviour change:

1. In `docs/completions-judge.md`, add a short "Why pointwise" paragraph (near the "Explicit external-judge selection" / judge description) stating: the adapter is pointwise (absolute) by design; Tripathi et al. COLM 2025 (arXiv:2504.14716v2, PDF 484742 B sha256 125715..., pp.1/3-5, 19 pp.) defines distracted evaluation as R(y|x,p) != R(y_f|x,p) where p(y)=p(y_f) and measures pairwise flips in ~35% of cases vs ~9% for absolute scoring across IFEval-TweakSet and MT-Bench (Fig.1, §4); the three distractor types (assertiveness, prolixity, sycophancy) correspond to the existing confident-claim, length-padding, and fluency checks; the residual 9% and the absolute-scoring limits (lack of comparative context, calibration inconsistency, §2.2) mean those checks remain required even with a pointwise judge.
2. Carry limits verbatim: pairwise vs absolute comparison is between 1..7 Likert weighted scoring and tie-inclusive pairwise (two-order aggregation) on those datasets; the plant's yes/no/unknown is coarser and not calibrated (already stated), so the 35%/9% ordering transfers but not the scale; absolute still flips, and a single adapter against fixed pairs cannot show discrimination (needs cross-adapter comparison, cf. Campbell & Fiske 1959 transfer review).
3. Evidence trigger: no new code or measurement in this review; revisit the paragraph after the next live-provider replay that exercises the three distractor cases (assertive, padded, sycophantic) — record flips per question with length/ratio as the metric already requires.
