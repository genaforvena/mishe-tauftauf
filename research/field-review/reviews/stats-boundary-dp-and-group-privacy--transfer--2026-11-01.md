---
field: stats-boundary-dp-and-group-privacy
angle: transfer
date: 2026-11-01
status: discarded
owner: senses
---

# stats-boundary-dp-and-group-privacy — transfer

## What we read

- Ilya Mironov, "Rényi Differential Privacy", CSF 2017, arXiv:1702.07476. Source: https://arxiv.org/abs/1702.07476 (full text read via https://arxiv.org/pdf/1702.07476). Read: Proposition 1 (RDP composition adds the ε terms at fixed order α), Proposition 3 (RDP to (ε + log(1/δ)/(α−1), δ)-DP), and the group-privacy passage.
- Martín Abadi et al., "Deep Learning with Differential Privacy", CCS 2016, arXiv:1607.00133. Source: https://arxiv.org/abs/1607.00133 (full text read via https://arxiv.org/pdf/1607.00133). Read: Theorem 2 (moments accountant: composability α_M(λ) ≤ Σ α_Mi(λ) and the tail bound δ = min_λ exp(α_M(λ) − λε)), and the Theorem 1 comparison with strong composition at L=0.01N, σ=4, δ=1e-5, T=10000 (ε≈1.26 vs ≈9.34).

## What this plant already does

`src/mishe_tauftauf/stats_boundary.py` holds pure, verdict-free DP vocabulary: `laplace_scale` (Δf/ε), `is_valid_dp_params`, `dp_composition` (sequential sum of ε and δ, advanced composition explicitly not used), and `group_privacy_epsilon` (k·ε). `query()` releases exact aggregates only. No caller outside `tests/test_stats_and_token_usage.py` uses the DP helpers (checked by grep across `src`, `tools`, `tests`). The senses trigger from chat 156015 is "a noisy release added to query()".

## What is new here

Rényi and the moments accountant are the tighter accountants for a ledger of noisy releases. They trade the linear ε sum for a per-order curve (RDP) or a log-moment sum (moments accountant), and the source's own numbers show the gap is large at many compositions (Abadi Thm 1 comparison: ε≈1.26 vs ≈9.34 at T=10000). That is a real contrast with `dp_composition`'s linear sum, and its docstring already flags advanced composition as not used.

The transfer does not land in this plant today. The plant releases no noisy value, so there is no sequence of releases for an accountant to track. Pure Laplace releases are (ε,0)-DP, and a pure ε-DP mechanism is (∞, ε)-RDP, so for the Laplace case RDP adds nothing the linear sum does not already give. The gain appears only for Gaussian or subsampled releases, which the plant does not have. Building an accountant now would add an unused path, which the doctrine forbids.

## Discard

No noisy release or caller exists to track, so an RDP or moments-accountant helper would have no receiver; re-open on a noisy/Gaussian/subsampled release in `query()` (trigger: senses, chat 156015).
