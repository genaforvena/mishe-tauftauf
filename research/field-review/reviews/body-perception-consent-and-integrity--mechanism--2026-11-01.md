---
field: body-perception-consent-and-integrity
angle: mechanism
date: 2026-11-01
status: discarded
owner: senses
---

# body-perception-consent-and-integrity — mechanism (2026-11-01)

## What we read

- Ninghui Li, Tiancheng Li, Suresh Venkatasubramanian, "t-Closeness: Privacy Beyond k-Anonymity and l-Diversity", ICDE 2007, pp. 106–115. Read the full 10-page author-hosted PDF, text extracted with `pdftotext` (fetched 2026-11-01 from <https://www.cs.purdue.edu/homes/ninghui/papers/t_closeness_icde07.pdf>). Read: §4 Definition 2 (the t-closeness principle), §5 (EMD for numerical and categorical attributes), §8 "Limitations of using EMD in t-closeness" (the paper's own admission about the meaning of t).
- Not read: the EMD computation details in §5 beyond the formulas for ordered and categorical domains; the experiments in §6–§7 were skimmed for the ground-distance setup only.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py` has `k_anonymity_k` and the k helpers over `light_class`. It declares no sensitive attribute and has no per-class distribution check. senses' docstring note (commit `0d0cf033`, accepted on senses' reply in chat 153898) states the homogeneity and background-knowledge blind spot and says no sensitive attribute is declared.
- Sensitive-attribute declaration and any ℓ- or t-style check are explicitly left open by senses in chat 153898 ("declaring a sensitive attribute ... stay open for their own review").

## What is new here

Not applied. The mechanism is a valid next step only after two prerequisites exist: a declared sensitive attribute with a ground distance (senses' open decision), and a measured choice of t against an observed information-gain proxy, which the paper says is not known. Adding a t-closeness check now would be a gate with no declared input and an unexplained threshold. This is not a recommendation to wire it.

Re-open trigger: senses declares a sensitive attribute for `PERSONAL_CONSENT_RECEIPTS`-covered flows. Then a follow-up review can propose a measured t, with the limit in §8 stated in the proposal.

t-closeness is the one mechanism in this family that addresses the critique's background-knowledge case, not just homogeneity: it compares each class's sensitive distribution to the whole table's distribution, so a prior that is "dark means asleep" shows up as distance from the population, which a diversity count cannot see. As an operational mechanism it is a per-class EMD against a whole-table baseline with a threshold t.

Its own limits, from the paper, bound what could be implemented here:

- §8: "the relationship between the value t and information gain is unclear." EMD gives 0.1 for both (0.01, 0.99)→(0.11, 0.89) and (0.4, 0.6)→(0.5, 0.5), though the first is a 1000% relative shift. A threshold on EMD therefore does not have a calibrated privacy meaning, and the plant would have to pick t with no measured basis.
- §4 and §5: the mechanism needs a declared sensitive attribute and a ground distance on its values. For body state (sleep, presence, activity) neither exists here. Neither can be chosen without senses' decision on the sensitive domain.

## Discard

t-closeness needs a declared sensitive attribute and a ground distance this plant lacks, and its own paper (§8) says the meaning of t is unclear, so no threshold can be justified now; re-open when senses declares the sensitive domain.
