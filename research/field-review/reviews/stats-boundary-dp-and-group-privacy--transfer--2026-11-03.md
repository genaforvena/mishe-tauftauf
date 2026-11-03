---
field: stats-boundary-dp-and-group-privacy
angle: transfer
date: 2026-11-03
status: discarded
owner: genome
---

## What we read
Rogers, Roth, Ullman, and Vadhan, “Privacy Odometers and Filters: Pay-as-you-Go Composition,” arXiv:1605.08294v3 (revised 2021-08-05), abstract and introduction. https://arxiv.org/abs/1605.08294

## What this plant already does
`src/mishe_tauftauf/stats_boundary.py` exposes sequential composition over caller-provided epsilon/delta values and a group-privacy helper. Its stats query reads exact aggregate rows; the module has no DP mechanism, adaptive analysis workflow, or privacy-release stop point.

## What is new here
The paper distinguishes adaptive privacy filters, which stop before a pre-specified budget is exceeded, from odometers, which report realized privacy loss without a pre-specified budget. Under adaptively chosen privacy parameters these are distinct guarantees, not interchangeable forms of composition.

## Discard
The adaptive filter/odometer machinery requires a sequence of differentially private analyses whose parameters or length depend on prior outcomes; this exact-only stats reader has no such release path to transfer it onto.
