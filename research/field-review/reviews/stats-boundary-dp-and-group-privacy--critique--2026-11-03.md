---
field: stats-boundary-dp-and-group-privacy
angle: critique
date: 2026-11-03
status: discarded
owner: senses
---

## What we read

Alberto Blanco-Justicia, David Sánchez, Josep Domingo-Ferrer, and Krishnamurty Muralidhar, **“A Critical Review on the Use (and Misuse) of Differential Privacy in Machine Learning,”** arXiv:2206.04621v2 (2022-07-05). Primary source: https://arxiv.org/pdf/2206.04621 . Read the paper itself, including §§1–3, 5 and 6.

The critique's strongest claim is deliberately scoped to machine learning: implementations that prioritize accuracy may use weak privacy parameters or relaxations, so a DP label alone may not establish meaningful practical protection (§§2–3). Their own experiments compare DP-SGD with dropout and L₂ regularization, reporting lower membership-inference advantage for both approaches, but also explain that attack measurements are a lower bound on leakage, unlike a valid DP guarantee's upper bound (§5). The paper argues against treating the nominal guarantee as sufficient evidence of privacy in the ML deployments it studies; it does not establish that empirical attacks can certify privacy generally.

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` is an exact aggregate/query boundary with DP calibration helpers; its existing review trail states that no noisy release exists. It is not an ML training implementation, and the selected critique's DP-SGD, clipping, epoch-composition and membership-inference experiments do not describe this code path.

## What is new here

The critique adds a useful warning against conflating a formally stated bound with practical protection in an ML deployment, while explicitly limiting attack experiments to evidence of observed leakage rather than proof of its absence. That distinction is relevant to ML systems, but does not identify a concrete failure or missing operational guard in this exact-only statistics module. Transferring it here would misrepresent the paper's scope and duplicate the prior Pufferfish critique's caution that no noisy release currently exists.

## Discard

The critique concerns DP-SGD in machine learning, while this target is exact-only and has no noisy release or model-training path; no defensible change to `stats_boundary.py` follows from this source.