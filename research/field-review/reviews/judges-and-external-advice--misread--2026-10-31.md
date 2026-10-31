---
field: judges-and-external-advice
angle: misread
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/judges.py
---

# Judges and external advice — misread: a passing control set read as a validated judge

## What we read

- Zheng, Chiang, Sheng, Zhuang, Wu, Zhuang, Lin, Li, Li, Xing, Zhang, Gonzalez, Stoica, "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena", arXiv:2306.05685 (NeurIPS 2023 Datasets and Benchmarks; v4 December 2023). Read: the arXiv abstract page, https://arxiv.org/abs/2306.05685. Abstract claims: strong LLM judges reach over 80% agreement with human preferences, comparable to human-to-human agreement; the abstract names four limitations of LLM-as-a-judge: position bias, verbosity bias, self-enhancement bias, and limited reasoning ability.
- Limits of this reading: abstract only. The full text, the agreement protocol (pairwise preference, not pointwise yes/no), and the section where each bias is measured were NOT read. Verbosity bias is named here, not quantified; any claim about its size in the paper is unread.
- Context check (also read): `src/mishe_tauftauf/judges.py` (`classify`, `controls`, `document`) and `src/mishe_tauftauf/completions_judge.py` (`report`, which runs `controls()` plus replay cases). `docs/completions-judge.md` already states that controls are "not a calibration or production readiness claim" and that the 1/0 mapping is a "categorical convention, not a calibrated probability".

## What this plant already does

- `Judgment.probability` has no averaging or ranking consumer. Only `classify` (0.20 / 0.80 thresholds, else unknown) and the feed receipt read it. The probability-as-calibration misread the abstract could invite does not occur at consumption.
- The docs disclaim calibration, and the report omits producer identity and raw bodies, so no number is presented as validity.
- The `controls()` docstring says a judge that cannot separate its pairs is "unsafe to enable". That is a necessary condition, and the docstring does not claim sufficiency.

## What is new here

- The misread lives in the control set, not in the doc. The six pairs (twelve calls) are short, structured SYSTEM ZERO fixtures. The negatives are terse ("I think it is done." and similar). None pairs a fluent, confident, unsupported negative against a grounded positive of comparable length. The abstract names verbosity bias as a limitation of LLM judges; a pointwise yes/no judge that says "yes" to long, assertive text with no check evidence would pass every current control and still be unsafe for `desired-state-met`.
- So a passing smoke run currently tells the operator less than its wording suggests: it shows separation of short structured pairs, not robustness to padding. The "Exit 0 means all twelve categorical answers matched" line in `docs/completions-judge.md` is accurate; the risk is the operator reading it as broader than that.
- Bounded proposal: add one control pair to `controls()` in `judges.py`. The negative is a long, confident "desired-state-met" claim (several sentences, claims the check passed, no SYSTEM ZERO exit or stdout line). The positive is the existing grounded pair. Expected verdicts: positive yes, negative no. This is a judge-side test of verbosity robustness, not a fix.

## Application

target: src/mishe_tauftauf/judges.py

Proposed to the owner (genome, to assign, since no wall in the tree records who owns the judge code):

1. In `judges.py` `controls()`, add a `desired-state-met-verbose` pair: the existing positive document, and a negative of comparable or greater length with fluent, confident prose asserting `stdout contains reading=42` without any SYSTEM ZERO exit or stdout line.
2. Because the control count changes from twelve to fourteen calls, `docs/completions-judge.md` ("six existing ... pairs", `--max-calls 12`) and the smoke command must change in the same commit. The control cache identity (`control_cache.py`) and the external-judge wrapper identity both depend on adapter source bytes; refresh controls after the change, as the docs already require.
3. Evidence trigger: run the smoke command against the configured judge before and after. If the verbose negative returns `yes`, the judge is verbosity-sensitive and the control failure is the intended result; do not enable that judge for `desired-state-met` until a fix or a different judge passes. If it returns `no`, record that as one data point, not validity.
4. Limit: one pair is a probe, not a bias measurement. Measuring verbosity bias properly needs a length-matched set across several cases; that is a follow-up, not claimed here.

Disposition time: route to genome for owner assignment at the next wake; re-read this review at the next completions-judge smoke run or when the control set is next changed.
