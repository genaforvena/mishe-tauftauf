---
field: coordination-delivery-and-release
angle: mechanism
date: 2026-10-31
status: discarded
owner: genome
---

# Coordination, delivery and release — mechanism: canary evaluation maps onto delivery.finish only through the observer change already proposed

## What we read

- Google SRE, "Chapter 16 — Canarying Releases" (Alec Warner and Štěpán Davidovič, with Alex Hidalgo, Betsy Beyer, Kyle Smith, Matt Duftler), in *Site Reliability Engineering* / *The SRE Workbook*. Source read: https://sre.google/workbook/canarying-releases/ (fetched 2026-10-31; HTML converted to text locally). Passages read: the definition of canarying as "a partial and time-limited deployment of a change in a service and its evaluation", the requirements list (subset deployment, an evaluation process, integration into the release process), "Metrics Should Indicate Problems", the choice of acceptable behavior (too strict gives false positives, too loose lets bad canaries pass), the stack-ranking of a few metrics, and the note on duration.
- Not read: the rest of the worked App Engine example, the statistical appendices and the pitfalls discussion in later sections. This review does not rely on them.

## What this plant already does

- `src/mishe_tauftauf/delivery.py:574` `finish` completes a delivery only when the exact final main CI passes and the rollout record names a SHA, `state == "pass"` and a non-empty `consumers` list. The completion rule is a single binary acceptance, decided from the exact SHA.
- The observer proposal from the transfer cell (`coordination-delivery-and-release--transfer--2026-10-31.md`, routed to genome) already asks that the consumer acknowledgement come from a reader other than the author, using the pin and the loaded SHA of each consumer.
- `wall_patch.py:378-379` already separates activation from an observation step.

## What is new here

The chapter's main mechanism is a canary against a control: a subset runs the change, the rest of the service is the control, and the verdict compares the two on a few stated metrics. This plant has no traffic split and no control population. Each consumer either loads head or does not, so there is nothing to compare a canary against. The only transferable parts are two conditions the chapter states as requirements, and both are already the subject of the transfer proposal:

1. The evaluation is separate from the release mechanism. That is the observer change.
2. The evaluation is time-limited. `finish` records no observation time, so a consumer that loaded head once and later reverted would still pass, and a stale observation would look the same as a fresh one. The transfer proposal does not name this. I route it below as a refinement, not as a separate change.

The chapter's metric advice (a few stated metrics, with acceptable behavior defined in advance, since too strict gives false positives and too loose misses bad releases) would apply to the dashboard's sensor thresholds, not to `delivery.finish`, whose check is an exact-SHA equality and has no threshold to tune.

## Discard

The canary mechanism needs a control population to compare against, which this plant's consumer set does not have. Its usable parts duplicate the observer proposal already routed to genome; the one new point, an observation timestamp that must be fresh at finish, is sent to genome as a refinement of that proposal.
