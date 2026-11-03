---
field: engine-policy-route-decision
angle: misread
date: 2026-11-03
status: applied
owner: genome
---

# engine-policy-route-decision × misread — applied

## What we read

- Yavatkar, Pendarakis, and Guerin, RFC 2753, *A Framework for Policy-based Admission Control* (January 2000), primary source: https://www.rfc-editor.org/rfc/rfc2753
- Sections 1, 3, and 4 distinguish a policy decision point (PDP), where policy decisions are made, from a policy enforcement point (PEP), where they are enforced. Section 3 explicitly says the framework specifies mechanisms, not any particular policy behavior. Its motivating architecture is RSVP bandwidth reservation; it also calls for monitoring/accounting, fault tolerance, scalability, and identity/trust between communicating PDPs and PEPs.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` describes `decide()` as a pure admission decision and returns a decision, selected route, reason, and evidence IDs; it does not apply the route.
- `src/mishe_tauftauf/engine_watch.py` calls `engine_policy.decide()` per role, stores decisions, and emits proposal records. Its `apply_proposal()` only loads and returns a record; neither it nor the `--apply` CLI path changes the current route.
- The reviewed policy's resource is provider-account quota state, not an RSVP bandwidth reservation; its inputs are account windows and reservations, allowlists, current route, and freshness state.

## What is new here

- RFC 2753 makes explicit that admission decisions and their enforcement are distinct responsibilities. The local source documents an intended enforcement stage, but the present `--apply` implementation only returns the stored proposal; it does not implement that PEP responsibility.
- This does not justify importing the RFC's RSVP signaling, remote PDP/PEP trust, preemption, or accounting mechanisms into a local account-quota policy. It does expose a concrete documentation mismatch in the target file.

## Application

target: src/mishe_tauftauf/engine_watch.py

Correct the module docstring's `--apply`/“milestone 3” claim to describe the observed behavior: it reads and prints the stored proposal; no CAS launcher change is implemented by `apply_proposal()`.
