---
field: engine-policy-route-decision
angle: recent
date: 2026-11-03
status: discarded
owner: genome
---

# Adaptive routing needs signals this policy deliberately lacks

## What we read

- Panda et al., “Adaptive LLM Routing Under Budget Constraints,” arXiv:2508.21141v2 (2025), primary source: https://arxiv.org/html/2508.21141v2.
- Sections 2.1–2.2 formulate query routing as a contextual bandit: choose one model for a query, learn from feedback on that chosen response, and constrain aggregate token cost. The paper pretrains a query/model embedding from human preferences, then adapts from online reward feedback; its online bandit learning phase explicitly has no budget constraint, while a separate online cost policy enforces budget.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py:decide` is a pure account-admission policy, not a query router. It classifies account quota windows and reservations, retains a healthy current route, then selects same-provider or ordered healthy fallbacks; unknown or stale account evidence yields `hold`, while gated accounts can yield `queue`.
- Its input and output contract contains no query content, response-quality label, preference feedback, or learned state. Suitability is fixed by the caller's allowlist; route policy guards account availability rather than predicting answer quality.

## What is new here

PILOT separates two mechanisms that are easy to conflate: preference/reward learning chooses a model for a query, while a distinct cost policy enforces aggregate budget. It requires query representations and feedback on the chosen answer, and the paper's reported gains are tied to its datasets, preference prior and modeled feedback. None of those signals exists in this plant's engine admission contract; importing only its bandit vocabulary would not create a meaningful quality signal and could turn quota admission into an unvalidated quality policy.

## Discard

No query or response-quality feedback enters `engine_policy.decide`, so adaptive quality routing is not applicable to this account-quota admission boundary.
