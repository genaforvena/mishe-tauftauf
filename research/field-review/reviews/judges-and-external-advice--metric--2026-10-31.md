---
field: judges-and-external-advice
angle: metric
date: 2026-10-31
status: applied
owner: docs
target: docs/completions-judge.md
---

# Judges and external advice — metric: a length-padding invariance check for a pointwise judge

## What we read

- Yann Dubois, Balázs Galambosi, Percy Liang, Tatsunori Hashimoto, "Length-Controlled AlpacaEval: A Simple Way to Debias Automatic Evaluators", arXiv:2404.04475 (v2, revised 2025). Source: https://arxiv.org/abs/2404.04475 and the HTML version https://arxiv.org/html/2404.04475v2.
- Read: the abstract (directly), and the HTML version's Eq. 1 (the GLM with a length term) and Eq. 2 (the length-controlled win rate), the Table 1 Spearman correlations with Chatbot Arena, and the paper's own statements about what it does not correct.
- Limits of this reading: the HTML was read through a summarising fetch. The equations are a transcription of a LaTeX render, not checked against the typeset source, and the quoted phrases are the extraction's wording, not verified verbatim. The regression's coefficients and fitted values were not re-derived. No provider credentials exist here, so nothing below is measured on this plant's judge.

## What this plant already does

- `src/mishe_tauftauf/completions_judge.py` judges one document per call and returns a categorical yes/no/unknown mapped to probability 1 or 0. It is pointwise: there is no second answer to compare against.
- `src/mishe_tauftauf/judges.py` `document()` puts the `EVIDENCE` section last, and the desired-state line of each control sits inside it. Evidence length is therefore the one free-form quantity that varies between documents for the same question.
- `controls()` gives six positive/negative pairs. `docs/completions-judge.md` requires all twelve control answers to match before the verdict is trusted, and asks operators to read false `desired-state-met` per question.
- No file in the tree measures whether the verdict depends on how long the evidence is. The only length check in the judge code is the model's `model_max_length` in `laya_judge.py`, which bounds input and does not measure bias.

## What is new here

The paper's contribution is a counterfactual: estimate the preference the judge would give if the two answers had the same length, by fitting the judge's preference with a length term and then setting that term to zero. Two parts of it matter for this plant.

1. **The bias is measured, not assumed.** The paper does not declare a length preference; it fits one (a logistic model whose length term is `ϕ · tanh((len_m − len_b)/std)`, so the effect has diminishing returns on the log-odds). Its own Table 1 gives the Spearman correlation with Chatbot Arena as 0.94 for the raw win rate and 0.98 for the length-controlled one. A correction is only as good as the measured term.
2. **It is not a pairwise trick.** The regression needs many comparisons. The plant has no pairs and, with twelve controls, cannot fit anything like it. So the transferable part is not the regression. It is the counterfactual test: change length while holding the substance fixed, and see whether the verdict moves.

The plant analogue is a **length-padding invariance check** for a pointwise judge:

1. For each control, build the document as now (`judges.document`, same question, same `TOP PAIN`, same evidence).
2. Build a second document with the same question and the same evidence sentences, plus a block of neutral padding appended inside `EVIDENCE` so that its length is at least double. The padding must not contain any desired-state or prediction wording.
3. Compare the two verdicts with the existing `--cases` replay. A flip on a control whose label is fixed means the judge's answer moved with length alone.

Prediction: on a judge that tracks substance, padded and unpadded verdicts agree on all twelve controls. A flip is a measurement, not a failure of the method. It says that the control is not safe to trust at that length, and the verdict for that question should be read as `unknown` until the evidence is shortened or restructured.

Metric to record per question: the number of control flips out of twelve, with the length ratio of each pair. This is the plant's own version of the paper's length-controlled win rate reduced to its simplest form: the verdict with the length term held fixed versus the verdict that varies.

## Limits, in the paper's terms and ours

- **The paper corrects one bias.** It says: "While we focus on a more detailed study of length biases here, we note that the same approaches can be applied to other biases", and it does "not (aim to) solve any other issues associated with the use of an LLM-judge." A padding check says nothing about self-preference, list presence, or position. The order-permutation check reviewed in `judges-and-external-advice--mechanism` covers a different bias.
- **Neutral padding is not neutral to a language model.** Filler text can change how a judge reads its evidence even when its length is the only deliberate change. The padding must be checked against the unpadded text for sentiment and for any reference to the question, or the flip could come from the padding's content. This is the largest threat to the check, and it is why the review proposes the padding as a replay case, not as a verdict rule.
- **Tested on one benchmark.** The paper's correction was validated on AlpacaEval; the Arena correlation is the paper's own evidence. A flip count on twelve controls is not a calibration, and the reviewed doc already says the smoke controls are not calibration either.
- **Sample size.** Twelve controls cannot estimate a length coefficient. The plant can count flips per question, not fit a slope. We do not claim the paper's correction applies here.

## Application

target: docs/completions-judge.md

Proposed owner change (documentation only, no verdict logic changes): add a short subsection under "Judge smoke controls and replay":

1. Name the length-padding replay as an operator check for a new provider or model: build a `--cases` file in which each control appears twice, once as is and once with neutral, desired-state-free padding inside `EVIDENCE` to at least double its length, both built with `mishe_tauftauf.judges.document`.
2. Record the flips per question and the length ratio of each pair in the operator's artifact, not in chat.
3. State the rule as an operator instruction, not a code gate: a question with any flip under padding is read as `unknown` for that provider until the evidence is restructured.
4. State the limits: the padding must be checked for content, and the paper's correction addresses length only.

Owner note: `judges.py` (where `controls()` and `document()` live) still has no named owner. The padding replay is a documentation-level change, so it can go to docs without touching that file. The code change, if the replay shows flips, needs the owner to be named first.

Route to docs by addressed message, with this review file and its commit as evidence. Copy genome for the owner question. Disposition: at the first completions-judge run against a live provider. Keep the subsection if the padded and unpadded controls agree; record the flips as evidence and route a restructuring proposal to the owner of `judges.py` if they do not.
