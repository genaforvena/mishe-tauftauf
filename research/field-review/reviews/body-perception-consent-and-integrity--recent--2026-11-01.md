---
field: body-perception-consent-and-integrity
angle: recent
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-consent-and-integrity — recent (2026-11-01)

## What we read

- Lange, Schreieder, Christen, Rahm, "Slice it up: Unmasking User Identities in Smartwatch Health Data", ACM ASIA CCS 2025 (Hanoi, 25–29 Aug 2025), DOI 10.1145/3708821.3710834. Preprint: https://arxiv.org/abs/2308.08310 (full text read at https://arxiv.org/html/2308.08310v3, first 100,000 of 125,248 characters; the appendix tables were truncated in the read).
- Read for: the threat model, the segment lengths at which re-identification reaches perfect accuracy, what the paper says about k-anonymity and de-identification, and the noise defence with its trade-off numbers.
- Reported in the paper: on the 15-subject WESAD set, Slicing-DTW reaches p@1 = 1.0 from one window of about 16 s (random baseline 1/15 ≈ 6.7%). On 10,000-subject synthetic sets it reaches perfect p@1 from w = 32–34 windows (about 9 min), in about 2 CPU-minutes. The attacker needs only a short attack sample and no training data.
- On k-anonymity the paper says only that it "could be considered", that "its application to time series data is complex and often unsuitable", and does not evaluate it. Its position is that "de-identification alone is insufficient". Its suggested stronger defence is differential privacy, not k-anonymity.
- Noise defence (Laplace, σ on WESAD): p@1 falls from 1.0 to about 0.21–0.42 at σ=1 and 0.25–0.42 at σ=2, while stress-detection F1 falls from 0.88 to about 0.71. The authors say noise "can significantly reduce re-identification risk" but does not prevent it.
- The paper's own limitations: 15 real subjects, GAN-generated scale sets with less DTW variation than real data (so possibly an underestimate of real risk), one task (stress detection), and a threshold that is hard to pick without dataset access.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py` implements a k-anonymity disclosure metric (`k_anonymity_k`, `k_anonymity_is_k_anonymous`, `k_anonymity_suppressed_count`, around line 824–910). Its docstring already states the correct limits: the QI is a modelling assumption (Assumption 2.1), and "No generalisation/suppression here."
- Its default QI is `QI_DEFAULT_KEYS = ("light_class",)`, a single coarse categorical attribute, with `K_ANONYMITY_DEFAULT_K = 2`.
- Contextual integrity and consent receipts already guard flows (`contextual_integrity_allows`, `personal_consent_allows`, `PERSONAL_CONSENT_RECEIPTS`), so the release decision does not rest on the k metric.
- A grep of `src/` and `tests/` finds no callers of the k-anonymity functions outside their own definitions. The metric is a measurement that nothing enforces.

## What is new here

The k metric is computed over one categorical attribute per row. It says nothing about whether a sequence of raw body readings identifies the person who produced it. The 2025 attack re-identifies from short time-series segments with no training data, and the paper reports that de-identification and k-anonymity do not address this. So a reader of the module's `k=2` could take it as a re-identification bound for released sequences. The module's own docstring does not say that, and the new reading makes the gap concrete. This is a documentation gap, not a code defect: the metric is correct for what it measures.

## Application

target: src/mishe_tauftauf/body_perception.py

Owner `senses` adds a boundary note beside `k_anonymity_k` (around line 824–840). The note states:

- the k value is over `QI_DEFAULT_KEYS` only, and k ≥ 2 on `light_class` does not bound the re-identification risk of a released time-series segment;
- sequence-level re-identification from short segments (Lange et al., ASIA CCS 2025) is outside this metric, and a release of raw body sequences needs a separate check or differential privacy, not this k value.

Test: a unit check that asserts the note is present is not useful; the check is a review of the wording against the paper's stated limits. Activation: a docstring-only change, so no runtime behaviour changes. Revert: `git revert` of that commit. Disposition after landing: the senses role reads the note against the paper and keeps or revises it.
