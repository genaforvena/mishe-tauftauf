---
field: judges-and-external-advice
angle: mechanism
date: 2026-10-31
status: applied
owner: docs
target: docs/completions-judge.md
---

# Judges and external advice — mechanism: an order-permutation check for a pointwise judge

## What we read

- Lianmin Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena", arXiv:2306.05685 (NeurIPS 2023 Datasets and Benchmarks). Source: https://arxiv.org/abs/2306.05685 and the full text at https://arxiv.org/html/2306.05685v4.
- Read: the abstract (verified directly: it names position, verbosity and self-enhancement biases and says strong judges reach over 80% agreement with human preferences), and Sections 3.3 and 3.4 (limitations, and the swap mitigation), read through the HTML version.
- Limits of this reading: the HTML extraction was read through a summarising fetch, and the quoted sentences below are that extraction's wording, not independently checked verbatim. The PDF could not be extracted as text. The paper's MT-Bench result tables were not re-derived. The source was reached; nothing here rests on a secondary summary of the paper.

## What this plant already does

- `src/mishe_tauftauf/completions_judge.py` sends one document per evaluation and maps the model's yes/no/unknown to a categorical value. It is pointwise: it judges one document against one question and never sees a second answer.
- `src/mishe_tauftauf/judges.py` `document()` builds that document in a fixed section order: `QUESTION`, `INSTRUCTIONS`, `TOP PAIN`, `EVIDENCE`, then an optional `PREDICTION`. The desired-state line of a control sits inside `EVIDENCE`, after the instruction text.
- `controls()` provides six positive/negative smoke pairs, and `docs/completions-judge.md` says the verdict is only accepted when all twelve control answers match. It also tells operators to compare failures per question, especially false `desired-state-met`.
- Nothing in the tree reorders the sections of a document and asks whether the verdict survives.

## What is new here

The paper's position-bias mitigation does not transfer as written. Its swap is pairwise: call the judge twice with answers A and B exchanged, and count a win only when both orders agree. The plant's judge has no pair of answers, so the literal swap has no referent. Adopting it would be a category error, and this review does not propose it.

What does transfer is the underlying test: **a pointwise judge's verdict should not depend on where a piece of its input sits in the prompt.** The paper defines position bias as a propensity "to favor certain positions over others", which in a pointwise setting becomes a sensitivity to section order. The mechanism is a permutation check:

1. For each control, build the document in the current order and in a permuted order (for example, `EVIDENCE` placed before `INSTRUCTIONS`, or the desired-state line moved below the evidence block).
2. Compare the two verdicts. A flip on a control whose label is fixed means the judge is reading layout, not substance.
3. Count flips per question in a replay, using the existing `--cases` mechanism, which already accepts arbitrary documents built with `judges.document`.

Prediction: on a judge that tracks substance, permuted and original verdicts agree on all twelve controls. A disagreement on any control is a result, not a failure of the method: the control is unsafe to trust under that order, and the verdict for that question should be treated as `unknown` until the layout is fixed. Keep the check if it runs as a replay without changing any verdict logic; revise the claim if the permuted order is itself malformed and the flip only reflects a bad prompt; revert the doc change if it proves unmeasurable.

Limits, in the paper's own terms and ours:

- The paper's swap is a pairwise remedy. This is a pointwise analogue of it, and its effect size is unmeasured here. It is a hypothesis about this adapter, not a transfer of the paper's result.
- Self-enhancement bias, which the paper also names, does not apply in the same way: the judge is an external provider and is not asked to rate its own output. We do not claim that it is absent for the provider's model family.
- A permutation check cannot tell position sensitivity from a real change in what the question asks. The permuted document has to keep the same question text and the same evidence. That is why the check is a replay with fixed labels.
- The completions judge has no credentials in this environment, so the check cannot be run here. The review claims no measurement.

## Application

target: docs/completions-judge.md

Proposed owner change (documentation only, no verdict logic changes): add a short section under "Judge smoke controls and replay" that does three things.

1. Names the order-permutation replay as an operator check for a new provider or model. The operator builds a `--cases` file in which each control appears twice, once in the current order and once with `EVIDENCE` placed before `INSTRUCTIONS`, using `mishe_tauftauf.judges.document` with the same arguments.
2. States the rule: if the two verdicts for one control disagree, that question is not enabled for that provider until the disagreement is explained. Expressed as an operator rule, not a code gate.
3. Says plainly that the swap mitigation from the MT-Bench paper is pairwise and does not apply to this pointwise judge.

Route to docs. The owner of the `controls()` schema question is still unassigned (see the earlier transfer review for the MTMM schema). Copy genome, since that owner question is open.

Disposition: re-read this file at the first completions-judge run against a live provider. If the controls pass on original order and the permuted replay shows zero flips, the claim stands as measured. If any flip appears, record the control and the question as evidence and route the layout change to the owner of `judges.py` once it is named.
