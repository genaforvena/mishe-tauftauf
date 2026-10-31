---
field: engine-model-routing-and-usage
angle: recent
date: 2026-10-31
status: discarded
owner: genome
---

# Quality-threshold routing does not transfer to quota admission here

## What we read

- Ong et al., "RouteLLM: Learning to Route LLMs from Preference Data", arXiv:2406.18665 (ICLR 2025). Primary source read as the arXiv HTML v4, https://arxiv.org/html/2406.18665v4, plus the abstract at https://arxiv.org/abs/2406.18665.
- Points used: the router sends a query to the strong model when P(win_s | q) ≥ α (Eq. 2, §3.1); cost is defined as the percentage of calls to strong models (§3.2); the paper sweeps α to hit target call fractions rather than using a calibrated percentile; generalization to new model pairs is claimed for MT-Bench only (§5.2, Table 4); the paper's own limitations are distribution shift and unexplained variance across benchmarks (§6), and Arena-only routers perform poorly out of distribution (Table 2 caption).

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` is a pure admission function. It classifies each account window as `healthy`, `reserve` or `depleted` from remaining quota fraction minus reservations, against a fixed `reserve_pct` default of 0.15 (lines ~300-322). Hysteresis holds the current route for `min_hold_ms` (default 600 000 ms). Same-provider alias is preferred before cross-provider fallback.
- The policy makes no quality judgment. Its docstring says the allowlist decides suitability and "no headroom percentage decides model suitability". Route choice follows quota state, not output quality.
- `engine_usage.py` supplies the status inputs. UNKNOWN, STALE and UNATTRIBUTED states are ineligible, so missing evidence holds admission instead of being read as exhaustion.

## What is new here

Nothing that changes the plant. RouteLLM's α is a quality-versus-cost dial fitted to labelled preference data. Its cost axis is the strong-model call fraction. This plant has neither input:

1. There is no per-call quality or preference label anywhere in the engine path, so there is no P(win | q) to threshold and no α to sweep.
2. The quantity that matches "fraction of strong calls" would be the fraction of decisions that are `fallback` or `queue`. That is a counting measurement, and `decide` already returns `decision` per call. Counting it would be an observation, not a routing change, and the policy's constant 0.15 is a design choice, not a fitted value that a sweep would replace.
3. The paper's own caveats (distribution shift, out-of-distribution failure, variance across benchmarks) argue against tuning any threshold from one benchmark. The plant has no benchmark to tune against, so the caveats do not add a new constraint here.

The generalization claim (routers reused when the model pair changes) would apply only if the plant chose between models by quality. It does not.

## Discard

No per-call quality label exists in engine_policy, so RouteLLM's α has nothing to calibrate against.
