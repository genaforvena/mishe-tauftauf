---
field: judges-and-external-advice
angle: mechanism
date: 2026-11-02
status: applied
owner: docs
target: docs/completions-judge.md
---

# Judges and external advice — mechanism: verbalized confidence and its calibration (vs categorical 1/0)

## What we read

- Katherine Tian et al., "Just Ask for Calibration: Strategies for Eliciting Calibrated Confidence Scores from Language Models Fine-Tuned with Human Feedback", arXiv:2305.14975v2 (EMNLP 2023, 24 Oct 2023). Source: https://arxiv.org/abs/2305.14975 and PDF https://arxiv.org/pdf/2305.14975v2 — fetched 2026-11-02 as /tmp/tian2023-calibration.pdf 964830 B sha256 dd4c8e43690ed8ce1a566d9cc1df01b558749379e4339156113ba93906562633, 10 pp. Creator pdfTeX-1.40.25, extracted via pdftotext -layout to 647 lines retained at /tmp/tian2023-calibration.txt.
- Read: abstract, §1–§3 with Tables 1–5, Fig.1–2, §4 Discussion and Limitations verbatim (pp.1–8); References scanned for Guo et al. 2017 calibration, Lord et al. 1985 / Mussweiler et al. 2000 considering-the-opposite, Brier 1950. Appendix Tables 6 prompts and Figs 3–5 scanned but not re-derived.
- Verbatim as printed (mechanism definitions):
  - §2 Metrics: "We measure calibration with multiple metrics. To measure ECE (expected calibration error; Guo et al. (2017)), we bin model predictions by their confidence and measure the average accuracy of predictions in each confidence bin. The ECE is defined as the average (squared) error between the average accuracy and confidence within each bin, where each error is weighted by the fraction of samples falling within the bin. We report raw ECE as well as ECE with temperature scaling (ECE-t). Temperature scaling fits a single temperature value β to the model's confidences to minimize negative log likelihood (NLL) on the data, giving scaled probability p̃_i of class i as p̃_i ∝ p_i^β." Also Brier Score "mean squared error between the confidences and the correctness labels" (BS-t on scaled confidences, a proper scoring rule, Ovadia et al. 2019) and AUC "area under the curve of selective accuracy and coverage" (Geifman and El-Yaniv 2017).
  - Method taxonomy: Label prob. p(y|x) via n=10 samples; 'Is True' prob. p(True|x,ŷ); Verb. 1S top-k "prompts the model to produce k guesses and a probability that each is correct all in a single response (i.e., '1 stage')" taking highest-probability prediction; Verb. 2S top-k same in two stages (answer then probability); Verb. 2S CoT; Ling. 1S-human / Ling. 1S-opt. mapping {Almost certain, Likely, …, Almost no chance} via human survey (Fagen-Ulmschneider 2023) or held-out optimized mapping.
  - Psychological transfer: "prompting a model to produce several answer choices before giving its confidence scores significantly improves calibration of verbalized probabilities" — inspiration from Lord et al. 1985 Considering the Opposite and Mussweiler et al. 2000 anchoring literature; Fig.1 caption "Considering more answer choices (bottom-right) further improves verbalized calibration (as in 'Considering the Opposite' in psychology; Lord et al. (1985))."
  - Key numbers (ChatGPT Table 1): Label prob. TriviaQA ECE 0.140, ECE-t 0.097 vs Verb. 1S top-1 0.068/0.076, Verb. 1S top-2 0.050/0.053, Verb. 2S CoT 0.110/0.123; GPT-4 Table 2 TriviaQA Label 0.078/0.067 vs Verb. 1S top-1 0.024/0.038; Claude-1 weaker at verbalizing than GPT family.
- Limits as stated by the authors: "experiments are focused on factual recall-oriented problems" (TriviaQA/SciQ/TruthfulQA short-form QA), lack of technical detail for closed RLHF-LMs limits causal attribution, limited to short-form generation not long-form decomposition, chain-of-thought does not consistently improve verbalized calibration (Appendix Fig.5), and prompt sensitivity between 1S vs 2S remains open. No log-probabilities assumed available for RLHF-LMs; sampling estimates used.

## What this plant already does

- `src/mishe_tauftauf/completions_judge.py` is a pointwise categorical judge: SYSTEM_PROMPT (lines 31–35) requires `{"verdict":"yes"}` / `{"verdict":"no"}` / `{"verdict":"unknown"}` only, with no confidence field. `Result.probability` maps yes→1.0 no→0.0 unknown→None, and `docs/completions-judge.md:211-214` says explicitly "Those endpoints are **categorical conventions, not calibrated probabilities**. Unknown, malformed output, refusal, token-limit termination and transport failure return `unknown`."
- The adapter exposes no calibration metric. Reports under `report()` write per-row categorical `verdict`, `matched`, `document_sha256`, `identity`, latency, `usage`; there is no bin, no ECE/BS/AUC column, no temperature β, no confidence histogram. The contract is pass/fail on 12 fixed controls (`controls()` six positive/negative pairs) plus optional `--cases` replay, with `max_calls` bounding but no scoring of confidence.
- `src/mishe_tauftauf/judges.py` `document()` and `controls()` supply the smoke set; `docs/completions-judge.md:72-172` already documents replay checks for order permutation, length padding and confident-claim style cases via `extra-judge-cases.py` and per-question flip counting, but does not mention calibration, ECE, Brier or verbalized confidence, and `field_review.py next` sweep for this field finds no prior "calibration" / "ECE" / "Brier" occurrence.
- The plant therefore cannot answer whether a judge that passes the 12 categorical controls is overconfident on borderline evidence, nor whether asking the same provider for a number would track accuracy.

## What is new here

Tian et al. show that for widely used RLHF-LMs (gpt-3.5-turbo, gpt-4, Claude) the cheapest way to obtain a better-calibrated score is to ask for it in token space, not to read logits: verbalized numerical probabilities (Verb. 1S top-k) are often better-calibrated than conditional probabilities even after temperature scaling, often halving ECE (e.g. ChatGPT TruthfulQA Label 0.451→Verb 1S top-4 0.203, SciQ 0.256→0.065). The mechanism is operational, not philosophical:

1. Elicit confidence verbally in the same turn as the answer (1-stage), or in a second turn after the answer (2-stage), optionally with k>1 candidates before the confidence.
2. Score calibration by binning: ECE = Σ_b (|B_b|/N)·(acc(B_b)−conf(B_b))² (weighted squared error in the paper's wording), Brier = mean (conf−correct)² on scaled confidences, AUC = selective accuracy-coverage trade-off. Obtain scaled confidences by fitting one β on a held-out split minimizing NLL: p̃_i ∝ p_i^β (Guo et al. 2017).
3. Improve calibration by forcing the model to enumerate alternatives before scoring ("considering the opposite") — Verb. 1S top-2/top-4 consistently beats top-1 across models and datasets in Tables 1–5, mirroring the human debiasing literature the paper cites.

The paper also warns: RLHF worsens logit calibration (Fig.2 Llama-70B base vs chat), CoT does not help, linguistic hedges without numeric grounding are model-dependent (Claude-1 weaker), and results are factual-recall only with equivalence judged by GPT-4/3.5 (Appendix C), so transfer to a verification judgement `desired-state-met` is a hypothesis, not a replication.

This is distinct from the predecessor transfer in the same cell (2026-10-31 Zheng et al. MT-Bench position bias, 2306.05685, pairwise answer-order swap vs pointwise permutation check) and from the recent transfer REVISIT (Raykar et al. 2010 EM aggregation without gold standard, 233472 B sha d14532… in `docs/completions-judge.md`). Different venue/authors/years/mechanism/metrics: Zheng = position/verbosity/self-enhancement biases, 80% human agreement, swap mitigation; Tian = ECE/Brier/AUC, temperature β, verbalized top-k, considering-the-opposite. Only the field and target overlap because the judge contract lives in `docs/completions-judge.md`.

## Application

target: docs/completions-judge.md

Proposed owner change (documentation only, no default-judge or verdict-logic change): add a short subsection under "Comparing two judges on one question set" (or adjacent to the `probability 1/0 are categorical conventions` paragraph at 211-214) titled "Calibrated verbalized confidence (operator experiment)" that does five things additively, using Tian et al. §2–§3 as the mechanism specification:

1. Names the experiment: a verbalized-confidence variant of the same completions adapter that, instead of `{"verdict":"yes"}` alone, requests `{"verdict":"yes","confidence":0.73}` (or `no` with confidence) in one stage, with an optional top-2/top-4 enumeration prompt mirroring Verb. 1S top-k ("produce 2–4 guesses for the verdict, then a probability each is correct, take the highest"). State that the default adapter still returns categorical 1/0 and this variant is for calibration measurement only.

2. Defines how to score it without code: reuse the existing 12 controls plus the three replay sets (order-permuted, length-padded, confident-claim) as a calibration set. Bin the returned confidences, compute ECE, temperature-scaled ECE-t (fit β on 20% hold-out minimizing NLL, evaluate on 80% as in Appendix, or leave β=1 for raw ECE), BS-t and selective AUC as defined verbatim above. Record per-bin counts, not a single threshold. A verbalized score that tracks accuracy will have low ECE/BS and high AUC; a high ECE with 0.95-confidence on negatives is overconfidence even if categorical accuracy looks high.

3. States the expected effect and decision rule: if Verb. 1S top-2 halves ECE relative to categorical 1/0 or to Verb. 1S top-1 on the same controls (as in Table 1: 0.068→0.050 TriviaQA, 0.234→0.132 SciQ), keep the elicitation wording and adopt temperature scaling for reporting; if CoT or 2-stage does not improve (as the paper finds), prefer 1-stage top-k; if no improvement over categorical 1/0, discard the variant for this provider. The verdict for production remains categorical; confidence is a diagnostic, not a gate — `unknown` on low confidence alone would discard the signal you are trying to measure.

4. Carries the paper's limits verbatim: factual-recall benchmark only, closed-model details unavailable, long-form generation not evaluated, equivalence-judgement method (GPT as grader) not applicable to `desired-state-met`, and prompt sensitivity between 1S/2S. Note the plant's categorical 1/0 endpoints are *not* probabilities, so they must not be temperature-scaled — only verbalized confidences are scalable via p̃_i ∝ p_i^β.

5. Gives a minimal operator recipe (no adapter code change required to measure): duplicate the `SYSTEM_PROMPT` with an added sentence "Also return confidence as a number between 0 and 1" and validate `0≤confidence≤1` is finite, else treat row as `unknown` for calibration (do not impute 1/0). Run `extra-judge-cases.py` cases through both prompts at same `--model` and compare ECE/BS side by side, keeping `document_sha256` grouping by `identity` so the comparison is per control. The report's `identity` hash will differ for the two prompts; that is intended — do not reuse a cached control result across prompts.

Prediction: a provider that can verbalize will show lower ECE-t for Verb. 1S top-2 than for categorical 1/0 on the same controls, and top-4 will not worsen it substantially — but this is unmeasured on any provider until an operator runs it with credentials. Keep the doc paragraph if the recipe runs as a replay with no verdict-logic change; revise the wording if the provider refuses the JSON confidence schema or emits non-numeric confidences; revert the wording if the calibration set (12+5 cases) is too small for stable binning and the ECE estimate is dominated by noise.

Route to docs. Notify genome as the grid successor owner.

Disposition: re-read at the first live completions run that requests verbalized confidence. If binned ECE-t/BS are recorded and temperature β fitted, the mechanism is embodied as measured; if confidences are absent or non-numeric, record the provider's refusal and retain categorical-only wording.
