---
field: body-perception-consent-and-integrity
angle: critique
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-consent-and-integrity — critique (2026-11-03)

## What we read

- Daniel Kifer, Ashwin Machanavajjhala, "Pufferfish: A Framework for Mathematical Privacy Definitions," *ACM Transactions on Database Systems* 39(1):1–36 (2014). Author-hosted TODS PDF fetched 2026-11-03 as `/tmp/pufferfish_tods.pdf` 999,736 B sha256 `35813373d1e5f96f251e43d255c31012dab92d3858a283b275c21e8a3c90fb8f` via <https://users.cs.duke.edu/~ashwin/pubs/pufferfish_TODS.pdf> (also at <https://www.cse.psu.edu/~duk17/papers/pufferfish_fullversion.pdf>). 36 pp. Read in full for this REVISIT: Abstract and §1 Introduction pp. A1–A3 (correlated data claim), §3 Pufferfish framework pp. A5–A7 (S / Spairs / D and Definition 3.4), §6 Differential privacy as Pufferfish pp. A11–A13, and §8 continuous/aggregate discussion p. A3–A4; skimmed §10 prior releases and §5 composition. The 2006 ℓ-diversity and 2007 t-closeness papers read for the 2026-11-01 critique and mechanism are not re-read as primary here.

The critique I read for, in the paper's own words:

- §1 p. A2: "user activity streams like GPS traces and social networks produce records that are correlated with each other. Recent work [Kifer and Machanavajjhala 2011; Gehrke et al. 2011] shows that attackers can use these correlations to improve their inferences about individuals (even when the data is protected by strong notions such as differential privacy [Dwork et al. 2006b; Dwork 2006])." The field site is that GPS trace; the formal claim is that record-level independence fails, so a definition that quantifies over neighboring databases differing in one record does not bound correlated inference.
- §3 p. A5: Pufferfish requires the domain expert to specify three components — "a set of potential secrets S," "a set of discriminative pairs Spairs ⊆ S×S," and "a collection of data evolution scenarios D." S is what should be protected; Spairs says how (which distinctions must stay indistinguishable); D is "a conservative collection of plausible data generating distributions" that encodes what the attacker may believe about correlations. The paper stresses (§3 p. A7) that without D the definition is either too strong (protects false beliefs) or too weak.
- Definition 3.4 p. A7 (Pufferfish privacy): for all θ ∈ D, all (si,sj) ∈ Spairs, all w in range(M), the odds ratio P(M(D)=w | si,θ)/P(M(D)=w | sj,θ) is bounded by exp(ε). When D is all independent-record distributions, this recovers differential privacy (§6 p. A11–A13); when D contains correlated distributions, differential privacy "leaks" under that D by construction. The framework's point is that privacy without a stated D is not a privacy claim at all.
- Concession the critique keeps: Pufferfish does not propose one D. It is a framework for stating it. The hedging-privacy instantiation (§1.1 p. A3) is deliberately introduced to rule out attackers whose priors are inconsistent with the observed data, and §6 shows that differential privacy *does* satisfy Bayesian semantics when D is independent — the critique is not that DP is wrong, but that its D is independent records, which the plant's data do not satisfy.

What the critique concedes and that we must keep: Pufferfish with independent D collapses to differential privacy; hedging privacy's filtering of implausible priors is computationally non-trivial; and composition (§5) and continuous-attribute protection (§8) require an explicit ground distance and Spairs — the same prerequisites the t-closeness mechanism review left open for this plant.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py:1107–1125` defines the dense spool window: `DENSE_TAIL_BYTES = 262144` (~2500 lines at ~101 B/line, ~3.5 h at 5 s cadence), `DENSE_WINDOW_ROWS = 2000` (~2.8 h). The comment at lines 1110–1121 already cites Narayanan & Shmatikov arXiv:cs/0610105v2 Thm 1–3 for high-dimensional sparse de-anonymization ("m = log N / -log(1-δ) random attributes ... at N~2500 this is 5–10 auxiliary attributes") and states that publishing raw tail rows or exact `T[QI]` histograms "preserves the sparsity the attack uses."
- `QI_DEFAULT_KEYS = ("light_class",)` and `K_ANONYMITY_DEFAULT_K = 2` at line 1171, with helpers `qi_histogram`, `k_anonymity_k`, `k_anonymity_is_k_anonymous` etc. (lines 1188–1356) take a row list and QI tuple at exact distance vector `[0,…,0]`. The k metric is measurement-only; `rg -n "k_anonymity|qi_histogram" src tests` finds definitions in `body_perception.py` and no caller in `src/` or `tests/` (see 2026-11-01 critique); the "gate" is not wired.
- The 2026-11-01 critique already pins the homogeneity/background-knowledge blind spot (Machanavajjhala ℓ-diversity §1.1) and proposes a docstring that `k == True` does not imply sensitive-state protection. The 2026-11-01 mechanism discard notes that t-closeness (Li et al. ICDE 2007 §8, EMD, "relationship between t and information gain is unclear") and any ℓ/t check wait on a declared sensitive attribute and ground distance that `L_DIVERSITY_SENSITIVE_KEY = None` (line 1185) leaves open. None of those reviews names the correlated-record failure.
- No Pufferfish vocabulary exists: `rg -n "Pufferfish|evolution scenario|discriminative|Spairs|correlated.*record" src` returns only research/ hits; `D` (evolution scenarios), `S` (potential secrets) and `Spairs` are not typed in `body_perception.py` or `stats_boundary.py` outside research/. The dense spool comment cites t-closeness/EMD limits (§8) but not the temporal-correlation limit.

## What is new here

Pufferfish names the exact limit the dense spool embodies and that the current metric cannot detect even when it fails homogeneously on time: **records are not independent**. The paper's motivating example is literally the plant's data shape — "GPS traces ... correlated with each other" where "attackers can use these correlations to improve their inferences about individuals (even when ... protected by strong notions such as differential privacy)." The dense queue (2500 rows, 5 s cadence, same body) is a GPS-trace-equivalent time series: knowing row n strongly predicts row n+1's light class, activity and presence. An adversary with a plausible D that includes diurnal/Markov correlation (sleep↔dark, movement↔very-bright, autocorrelation across seconds) learns the sensitive state of a target row from its neighbors even if each QI class has size k=2 and even if ℓ-diversity holds per class.

This is distinct from the 2026-11-01 ℓ-diversity critique (single-class homogeneity) and the t-closeness §8 EMD critique (threshold calibration): Pufferfish keeps both critiques but adds a cross-row prior that a per-window `qi_histogram` cannot represent because it projects each row independently over `light_class` alone. The paper's §6 reduction makes the point formally: differential privacy guarantees its Bayesian semantics only when D is independent-record distributions; change D to include temporal correlation and the same ε mechanism leaks. The plant has no D to state, so it cannot claim either protection or a priced relaxation — it is in what the paper calls the "no assumptions" warmup (§3 p. A7) where D is all distributions, which is too strong to achieve utility and therefore exactly the wrong default for a 3.5 h trace.

The sensitive-secret phrasing transfers directly: S would be claims like "reading r at time t is dark/asleep" vs "reading r at time t is bright/awake"; Spairs would be the distinctions the receipt must keep indistinguishable (not every value pair — Pufferfish's customization point); D would be the plausible generative processes (i.i.d. vs correlated walk vs skewed population prior like the ℓ-diversity Japanese/heart-disease prior). The current module declares none of these, so a future diversity/DP release cannot be compared to a stated adversary.

Limits the source states and kept here: Pufferfish is not a mechanism; the paper does not prescribe a D for body signals and does not claim hedging privacy filters all implausible posteriors efficiently. Adding S/Spairs/D is policy (and, for continuous aggregates, needs a ground distance per §8), not a docstring fix alone. The finding complements, not replaces, the 2026-11-01 homogeneity note and the 2026-11-01 t-closeness discard.

## Application

target: src/mishe_tauftauf/body_perception.py

Owner `senses` adds a docstring-only boundary note beside `DENSE_TAIL_BYTES` / `DENSE_WINDOW_ROWS` (around lines 1107–1125), complementary to the existing Narayanan-Shmatikov / k-boundary paragraph and the 2026-11-01 ℓ-diversity note at `k_anonymity_k`. State (cite Kifer & Machanavajjhala, *TODS* 39(1) 2014, Pufferfish):

- The TODS Pufferfish §1 p. A2 correlated-data limit: record-level definitions (k-anonymity, ℓ-diversity, ε-DP under independent-record D) do not bound inference when rows are correlated — explicitly "GPS traces and social networks ... produce records that are correlated ... attackers can use these correlations ... even when the data is protected by strong notions such as differential privacy." The dense spool (`DENSE_TAIL_BYTES` ~2500 rows, ~3.5 h at 5 s) is that shape; temporal correlation (dark↔sleep, consecutive rows predictive) is not represented in `QI_DEFAULT_KEYS = ("light_class",)` or in any per-class diversity count.
- The Pufferfish framework (§3 p. A5, Definition 3.4 p. A7): a privacy claim requires an explicit S (potential secrets), Spairs (which secret-pairs must stay indistinguishable) and D (plausible data-generating distributions / attacker priors including correlations). This plant currently declares none of them: `L_DIVERSITY_SENSITIVE_KEY = None`, no Spairs, no D. Without D, Pufferfish's "no assumptions" D is all distributions — too strong for utility — and the §6 independent-record D that recovers differential privacy is not the dense queue's D.
- Therefore: do not read `k_anonymity_is_k_anonymous == True` or "ℓ-diverse" as bounding correlated time-series disclosure; raw tail rows, exact `T[QI]` / `qi_sensitive_histogram` slices, or wall outcomes that quote tail rows are releases over correlated rows, not independent records. Any such release reviewed as DP must name its D (independent vs correlated), or be handled as a revisited per-type consent receipt under `src/mishe_tauftauf/stats_boundary.py` (Dwork & Roth Def. 2.4) with explicit ε/δ/composition — not as "k ≥ 2 therefore safe." The t-closeness §8 EMD calibration limit ("relationship between t and information gain is unclear") is a separate, still-open threshold problem; Pufferfish's correlation limit is not solved by choosing a different t.
- Keep the 2026-11-01 homogeneity/background-knowledge note and the Narayanan-Shmatikov sparsity note; this note is the correlated-record complement.

**Acceptance (deterministic):**
- `python -m py_compile src/mishe_tauftauf/body_perception.py` exits 0.
- `python3 research/field-review/field_review.py check` passes for this review.
- `rg -n "Pufferfish|evolution scenario|Spairs" src/mishe_tauftauf/body_perception.py` hits the new note and nowhere else in `src/`.
- `rg -n "k_anonymity|qi_histogram" src tests` still shows no production caller (note is docstring-only; wiring a correlated gate or a Pufferfish instantiation is a separate, senses-owned decision).

**Ownership.** `senses` owns `body_perception.py`. This proposal is docstring plus no behaviour change. Whether to type S/Spairs/D, to add a correlated adversary model, or to instantiate hedging privacy is senses' decision and needs its own review; this review does not ask for it.

**One thing this review does not ask for.** It does not recommend implementing Pufferfish or hedging privacy now. The paper itself leaves D to the domain expert and notes composition and continuous-attribute distance must be chosen. Declaring the sensitive domain is the prerequisite, per the 2026-11-01 t-closeness discard, and remains open.
