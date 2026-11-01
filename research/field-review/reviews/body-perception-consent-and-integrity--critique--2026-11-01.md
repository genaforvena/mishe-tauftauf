---
field: body-perception-consent-and-integrity
angle: critique
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-consent-and-integrity — critique (2026-11-01)

## What we read

Ashwin Machanavajjhala, Johannes Gehrke, Daniel Kifer, Muthuramakrishnan Venkitasubramaniam, "ℓ-Diversity: Privacy Beyond k-Anonymity" (the paper introducing ℓ-diversity; the venue is listed in bibliographic records as ICDE 2006 and ACM TKDD 2007, and the copy read does not print one). Read the full text from the Cornell-group author copy:

- <https://www.cs.rochester.edu/~muthuv/ldiversity.pdf> (fetched 2026-11-01, text extracted with pdftotext; the copy read is the authors' PDF, not a summary).

The critique I read for, in the paper's own words:

- §1.1 Homogeneity attack: "all tuples that share the same values of their quasi-identifiers should have diverse values for their sensitive attributes." A k-anonymous class can have one sensitive value; the paper estimates information about roughly 740 people would be compromised this way in its example.
- §1.1 Background knowledge attack, "Observation 2: k-Anonymity does not protect against attacks based on background knowledge." Its example: a known low incidence of heart disease among Japanese is combined with a quasi-identifier match to conclude near-certainty of a viral infection.
- §1.1 footnote 2: real data are "very skewed", so a k-anonymous table "might not have so many groups."
- §3 and §6: the authors concede that instance-level knowledge (e.g., "Bob's son tells Alice Bob does not have diabetes") and multiple adversaries with different knowledge are not fully modelled, and that a diversity requirement "is likely to cause significant loss of information" when the sensitive domain is large.

What the critique concedes and that we must keep: k-anonymity still defeats the naive linking attack on quasi-identifiers; ℓ-diversity is an addition, not a replacement; and the authors do not claim diversity is sufficient.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py:854` sets `QI_DEFAULT_KEYS = ("light_class",)` and `K_ANONYMITY_DEFAULT_K = 2`. The k-anonymity helpers (`qi_histogram`, `k_anonymity_k`, `k_anonymity_is_k_anonymous`, `k_anonymity_suppressed_count`, `k_anonymity_completeness`, `k_anonymity_distinct_tuples`, lines 859–1000) take a row list and a QI tuple and nothing else. There is no sensitive-attribute parameter anywhere in the module.
- The module-level comment at `body_perception.py:899` already says that "k >= 2 on `light_class` does not bound the re-identification risk of a released time-series segment" (Lange et al. 2025). That is the time-series boundary. It does not mention homogeneity or background knowledge.
- The "gate" named in the field is not wired: a grep for `k_anonymity`, `qi_histogram` and `QI_DEFAULT_KEYS` outside `research/` finds definitions in `body_perception.py` and no caller in `src/` or `tests/`. The metric is available, not enforced. The name "gate" overstates the current code.
- Deterministic check (run 2026-11-01 against HEAD 69a4de12 with `PYTHONPATH=src`): rows `[dark/asleep, dark/asleep, bright/awake, bright/walking]` give `k_anonymity_k = 2`, `k_anonymity_is_k_anonymous(..., 2) = True`, `k_anonymity_suppressed_count(..., 2) = 0`, while the `dark` class has one distinct activity value. The metric therefore passes a homogeneous class by construction.

## What is new here

The paper's homogeneity attack is the exact blind spot of the current metric. k=2 on `light_class` can be satisfied while every reading in the `dark` class shares one sensitive value. Here the sensitive value would be any state a sensed reading could reveal beyond light level (sleep, presence, activity). The metric has no field for it, so it cannot report the failure.

The background-knowledge attack transfers directly. The paper's Japanese/heart-disease inference is a population prior combined with a quasi-identifier match. Here the prior is ordinary diurnal knowledge: "dark at night, people sleep." An observer who knows the light class and the hour learns the sensitive state with near certainty even in a k=2 class. The current code does not model this adversary, and `QI_DEFAULT_KEYS` does not include time-of-day, so the real quasi-identifier is larger than the one the metric checks. That is the paper's Assumption-of-correct-QI failure, and the module comment at line 899 names it only for time series.

Nothing here argues the k metric is wrong. It is correct for what it measures (Samarati & Sweeney's linking-attack model). The critique is about what a reader would conclude from "k-anonymous on light_class", which is the conclusion the field name invites.

## Application

target: src/mishe_tauftauf/body_perception.py

Docstring-only boundary note plus one characterization test. No new sensor, no verdict change, no caller wiring, no I/O.

1. In the `k_anonymity_k` docstring (after the existing `light_class` boundary paragraph, line ~899–905), add: "k is a linking-attack metric only. It does not detect homogeneity (a class whose rows share one sensitive value still passes) and does not model background knowledge (a population prior over the sensitive value, e.g. diurnal sleep vs. light). ℓ-diversity (Machanavajjhala et al., ICDE 2006 / TKDD 2007) is the named remedy and is not implemented here. Do not read `k_anonymity_is_k_anonymous == True` as 'the sensitive state is protected'." Cite §1.1 of the paper.
2. In `tests/test_body_perception.py`, add one characterization test: build the four-row table above and assert `k_anonymity_is_k_anonymous(rows, QI_DEFAULT_KEYS, 2) is True` while the `dark` class has one distinct value. This pins the blind spot so that a future ℓ-diversity addition has to change the test deliberately, not silently.

**Acceptance (deterministic):**
- `python -m py_compile src/mishe_tauftauf/body_perception.py` exits 0.
- `python -m pytest tests/test_body_perception.py -q` passes, including the new characterization test.
- `rg -n "k_anonymity_is_k_anonymous|qi_histogram" src tests` still shows no production caller (the note is docstring-only; wiring a gate is a separate, senses-owned decision).
- `python3 research/field-review/field_review.py check` passes for this review.

**Ownership.** `senses` owns `body_perception.py`. This proposal is docstring plus a pinning test only. Whether to wire the k metric as a gate, or to add ℓ-diversity with a declared sensitive attribute, is senses' decision and would need its own review; this review does not ask for it.

**One thing this review does not ask for.** It does not recommend ℓ-diversity as an implementation. The paper itself says diversity can be costly in information, and the sensitive domain for body state is not yet defined in this plant. Declaring that domain is the prerequisite, and it belongs to the consent and CI vocabulary already owned by senses.
