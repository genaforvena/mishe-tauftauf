---
field: judges-and-external-advice
angle: critique
date: 2026-10-31
status: applied
owner: docs
target: docs/completions-judge.md
---

# Judges and external advice — critique: a judge that favours fluent text passes our controls

## What we read

- Minghao Wu and Alham Fikri Aji, "Style Over Substance: Evaluation Biases for Large Language Models", arXiv:2307.03025 (first submitted 6 July 2023; later revisions November 2023). Read: the arXiv abstract page, https://arxiv.org/abs/2307.03025. The abstract reports that LLM evaluators rate answers containing factual errors more favourably than answers that are too short or contain grammatical errors, calls this a concerning bias in evaluation, and proposes multi-dimensional, independent scoring (the Multi-Elo Rating System).
- Limits of this reading: abstract only. The PDF was not readable through my fetch tool (binary stream), so the methods, judge set, sample sizes, the tables behind the bias claim, and the paper's own limitations section were NOT read. Any claim about their size or scope is unread. The critique is taken at the level of its stated finding, the same level as the sibling misread review.

## What this plant already does

- The completions judge is opt-in and explicit (`docs/completions-judge.md`, "Explicit external-judge selection"). The default judge is unchanged and no judge output is a gate without a configured wrapper.
- Failures are categorical and fail closed: malformed, refused, truncated or transport-failed output maps to `unknown`, not to `yes`.
- The docs already say to compare "labeled failures per question, especially false `desired-state-met`, rather than just aggregate accuracy".
- The six smoke pairs in `controls()` (`src/mishe_tauftauf/judges.py`) are paired on substance, not style. For `desired-state-met` the negative says `stdout: reading=41` against a positive that says `reading=42`. A judge that tracks the stated value separates them.

## What is new here

- The critique's failure is a judge that rewards surface properties (fluency, length, assertive tone) over substance. Our controls cannot detect that. Each control differs in exactly the substance that matters (`reading=42` vs `reading=41`, a real `SYSTEM ZERO` block vs none), so a fluency-biased judge would pass all twelve calls and still be unsafe for `desired-state-met`.
- The docs say "Exit 0 means all twelve categorical answers matched; this is not a calibration or production readiness claim." That disclaims calibration but does not say what the controls fail to test. An operator reading "exit 0" could take it as evidence against style-driven false positives. The critique shows that reading would be wrong.
- The sibling misread review (`judges-and-external-advice--misread--2026-10-31.md`) proposes a verbose-negative control pair. That is a test, not yet landed. This review's application is narrower and separate: a scope note that the current controls do not test fluency-driven error, so the exit-0 wording cannot be stretched to cover it. It is a disclosure, and it does not replace the test.
- Adopting an LLM judge "blindly" here would mean treating a `yes` on `desired-state-met` as evidence that a desired state is met. The doc already forbids the calibration reading; it does not forbid the substance-over-style reading. That is the gap.

## Application

target: docs/completions-judge.md

Proposed to the owner (docs), as a one-sentence scope note in the section "Judge smoke controls and replay", directly after the sentence that begins "This runs the six existing positive/negative smoke-control pairs":

> These controls pair substance (for example `reading=42` against `reading=41`), so they do not test whether a judge is swayed by fluent, long or confident text that asserts the desired state without evidence. Exit 0 says nothing about that failure mode.

Evidence trigger: the verbose-negative control from the sibling misread review. If it lands, this sentence is narrowed to name what it now covers. If it does not land, the sentence stays. The docs change alone is a disclosure, not a measurement of bias, and is not claimed as one.

Disposition: route to docs at this wake. Re-read this file at the next completions-judge smoke run or the next change to `controls()`.
