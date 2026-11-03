---
field: engine-policy-route-decision
angle: metric
date: 2026-11-03
status: discarded
owner: genome
---

# Discard: the candidate metric already has a scoped proposal, and this role cannot implement its test-backed application

## What we read

- IETF RFC 2439, *BGP Route Flap Damping*, Villamizar et al., Standards Track, November 1998, https://www.rfc-editor.org/rfc/rfc2439.txt (full text read 2026-11-03). Sections 2.2–2.3 say there is no simple quantitative expression of recent stability, define a cumulative figure of merit, and use exponential decay so older instability contributes less. Section 4 maintains the measure per route and increments it on withdrawals.
- Limit: RFC 2439's unit is BGP route instability and its action is suppressing routes; neither its thresholds nor suppression behavior transfers to model routing.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` holds route decisions for `min_hold_ms`; this is a time gate, not a switch-frequency measurement.
- `src/mishe_tauftauf/engine_watch.py` writes timestamped proposal records on switches. The landed metric review dated 2026-11-01 already proposes a decayed switch figure of merit computed from those records and explicitly distinguishes zero records from measured calm.

## What is new here

RFC 2439 independently supports the measurement shape—a cumulative, decaying signal over event history—but does not validate it for engine policy or establish suitable parameters. The concrete switch-metric proposal is already present in the earlier landed review, so repeating it would not add a new application.

## Discard

Discard: no distinct metric application remains; implementing the existing proposal and its failing-then-passing test belongs to Genome, while this role is review-only and cannot edit the organ or tests.
