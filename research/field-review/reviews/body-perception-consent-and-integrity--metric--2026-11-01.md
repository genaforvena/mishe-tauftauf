---
field: body-perception-consent-and-integrity
angle: metric
date: 2026-11-01
status: discarded
owner: senses
---

# body-perception-consent-and-integrity — metric (2026-11-01)

## What we read

- Khaled El Emam and Fida Kamal Dankar, "Protecting Privacy Using k-Anonymity", JAMIA 15(5):627–637, 2008, DOI 10.1197/jamia.M2716. Located through a search result that showed the abstract and the two re-identification scenarios: the prosecutor case (a known individual's record is sought) and the journalist case (any individual is re-identified to show that it can be done). The abstract states that k-anonymity over-anonymises most where the sampling fraction is small.
- Attempted the full text at <https://pmc.ncbi.nlm.nih.gov/articles/PMC2528029> and at a University of Bath mirror. PMC returned a browser-verification page with no article text; the Bath mirror redirected to the university home page. Neither yielded the definitions of the prosecutor or journalist risk, so none is quoted here.
- Attempted to locate the "Lange et al. 2025" time-series re-identification paper that the module comment at `src/mishe_tauftauf/body_perception.py:900` cites. Two searches found no such paper. The closest result is a different 2025 smartwatch re-identification paper ("Slice it up", ACM ASIA CCS 2025), which does not discuss k-anonymity or light.

## What this plant already does

- `body_perception.py:846–1000` implements the Samarati–Sweeney k metric only: `qi_histogram`, `k_anonymity_k` (the minimum class size), `k_anonymity_is_k_anonymous`, `k_anonymity_suppressed_count`, `k_anonymity_completeness` and `k_anonymity_distinct_tuples`. These reduce to a single number, the smallest equivalence-class size, which is the linking-attack view. They do not compute a risk per record.
- The prior critique and mechanism reviews for this field (commits `89795546`, `9e8496dd`) already address the homogeneity and background-knowledge blind spots of that number, and senses landed the docstring note and characterization test in `0d0cf033`.

## What is new here

Not applied. The prosecutor and journalist risk measures would be a per-record metric (roughly 1/f for a class of size f, with a sampling-fraction correction), which is a different quantity from the current minimum-k number. Proposing it needs the exact formulas from the source. The abstract alone does not give them, and a formula reconstructed from memory would be a guess about the source's contents.

The Lange 2025 citation is a separate finding. The module comment at line 900 is the only place the time-series boundary is justified, and I could not locate that source. Senses should either confirm the reference or drop the attribution.

## Discard

Full text of the El Emam & Dankar risk definitions is unreachable here (PMC verification page, Bath mirror redirects), so no per-record metric can be proposed from a reading; the cited Lange 2025 source could not be found and is routed to senses for verification.
