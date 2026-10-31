---
field: judges-and-external-advice
angle: recent
date: 2026-10-31
status: applied
owner: docs
target: docs/completions-judge.md
---

# Judges and external advice — recent result: self-preference in LLM judges

## What we read

- Wataoka, Takahashi, Ri, "Self-Preference Bias in LLM-as-a-Judge", arXiv:2410.21819 (accepted at the NeurIPS 2024 Safe Generative AI workshop; revised 2025). Read: the arXiv abstract page, https://arxiv.org/abs/2410.21819. Abstract claims: GPT-4 shows a significant degree of self-preference bias; a quantitative metric is introduced; the authors hypothesise the bias comes from perplexity, so LLMs favour outputs more familiar to them, and give higher scores to lower-perplexity outputs than human raters do, whether or not they wrote them.
- Limits of this reading: the PDF could not be extracted in this session, so the metric's formula, the judges tested, and the paper's own limitations were NOT read. This review relies on the abstract's claims only.
- Context check (also read): the plant's completions judge, `docs/completions-judge.md` and `src/mishe_tauftauf/completions_judge.py`. The judge is pointwise: one document in, one yes/no/unknown out, with no order-swapped pair. Replay reports record input hashes, answers, latency, usage and the returned model; they do not record the model or family that produced the evaluated document.

## What this plant already does

- Fail-closed judge: unknown, malformed or refused output maps to `unknown`, never to a default yes or no.
- Smoke controls (six positive/negative pairs) and a replay path with per-question labelled failures; the doc already asks readers to look at false `desired-state-met` rather than aggregate accuracy.
- Explicit provider and model selection with no automatic fallback, so the judge identity is chosen by the operator.

## What is new here

- The judge and the thing judged can share a model family. If the completions judge is the same family as the producer of the document it evaluates, the abstract's claim predicts inflated "met" verdicts. The plant's controls are fixed synthetic pairs and cannot detect this, because they do not come from the producing model.
- Pairwise position-swap mitigation (common in the wider literature) does not transfer directly, because this adapter is pointwise. The risk that does transfer is familiarity bias, which the replay report currently cannot show, since it omits the producer identity.

## Application

target: docs/completions-judge.md

Proposed bounded docs change for the owner (docs): add a short "Judge identity" paragraph to `docs/completions-judge.md` stating that (a) the adapter is pointwise, so order-swap checks do not apply; (b) self-preference bias is reported for LLM judges (arXiv:2410.21819, abstract-level reading only); (c) when replay cases were produced by the same model family as the judge, the operator should record that in the report notes and treat "desired-state-met" verdicts on them as unvalidated. No code change is proposed by this review. A follow-up measurement, replaying the same cases through a second-family judge and comparing false-met rates, is the evidence trigger for any further change; it is not run here.

Disposition time: route to docs; re-read this review at the next completions-judge replay run.
