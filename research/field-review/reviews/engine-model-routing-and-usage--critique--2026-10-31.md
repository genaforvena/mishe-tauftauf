---
field: engine-model-routing-and-usage
angle: critique
date: 2026-10-31
status: applied
owner: genome
target: tests/test_engine_policy.py
---

# A quota fraction is a Goodhart proxy the policy itself moves

## What we read

- Manheim & Garrabrant, "Categorizing Variants of Goodhart's Law", arXiv:1803.04585 (v4, 2019). Primary source read as the arXiv HTML, https://arxiv.org/html/1803.04585v4, and the abstract at https://arxiv.org/abs/1803.04585.
- Points used: the four variants and the paper's own definitions. Regressional: "an inexact metric necessarily leads to a divergence between the goal and the metric in the tail." Extremal: "Worlds in which the proxy takes an extreme value may be very different from the ordinary worlds", with the sub-case "Change in Regime". Causal: "intervening can change the relationship between the measure and proxy", including the "Metric Manipulation" case where the regulator sets the metric directly. Adversarial: "other agents react in different ways to create Goodhart-like effects."
- The paper's own limit: the variants "often occur together", the analysis uses one metric and one regulator, and it says the adversarial dynamics "are clearly further dynamics worth exploring". We take the categories as a checklist, not as a proof that any one applies.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` classifies each account window from remaining quota fraction minus in-flight reservations (`_effective_reserved`, lines ~93-100). A window with `0 < eff < reserve_pct` (default 0.15, line ~301) is `reserve`; the policy then prefers a same-provider alias before a cross-provider fallback.
- The route decision therefore reads a quota fraction that the plant's own calls consume. The policy is both a regulator of that fraction and an input to it.
- Missing or stale usage evidence is not read as exhaustion (`engine_usage.py`: UNKNOWN, STALE and UNATTRIBUTED are ineligible). That already guards the "missing data looks like depletion" failure.
- Tests exist for the policy (`tests/test_engine_policy.py`) and the usage reader (`tests/test_engine_usage.py`).

## What is new here

Mapping the paper's categories onto the policy gives three concrete mechanisms, none already named in the tree:

1. **Causal, metric manipulation (paper §3).** The fraction the policy reads falls partly because the policy routes calls to a window. The reserve threshold therefore changes the metric it measures: a route that admits a call lowers the quota fraction, which can push the next decision into `reserve` and send traffic to another alias. This is a feedback loop, not a fixed observation. We have not measured it live.
2. **Extremal, change in regime (paper §2).** The relation between "remaining fraction" and "a window will last until its reset" is not linear near zero. At `eff` close to 0 the reserve branch is the only signal that separates a healthy window from one about to be exhausted. A test that checks only the healthy and depleted midpoints would not catch a boundary regression at `eff == reserve_pct` or at `eff` just above 0.
3. **Regressional, in the tail (paper §1).** `reserve_pct` is a fixed constant, not fitted to the window's own reset schedule. The paper's tail-divergence claim predicts that the constant is least accurate for the windows that matter most, the ones near reset with little remaining headroom.

The application is narrow. It does not change the policy's rule, the constant or the hysteresis. It pins the reserve boundary in the existing test file so that a future edit which moves the comparison from `<` to `<=`, or which drops the `0 <` lower bound, fails a test rather than shifting routes silently.

## Discard

Not discarded. Applied as a boundary test; the feedback mechanism (point 1) is recorded as an observation gap, not a change, because it needs a live count of route flips before any action.

## Limits

- Live route-flip counts were not read in this turn. The feedback mechanism is a hypothesis from the paper's categories, not an observed effect.
- The paper is a conceptual taxonomy with simple models, so it does not supply a threshold or a size for the effects above.
- The proposed test is a suggestion to the owner (genome). This review does not edit `engine_policy.py` or the test file.
