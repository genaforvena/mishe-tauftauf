---
field: engine-model-routing-and-usage
angle: recent
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/engine_policy.py
---

# RouterBench NDCH/AIQ as a measurement discipline for engine routes

## What we read

- Hu et al., "RouterBench: A Benchmark for Multi-LLM Routing System", arXiv:2403.12031v2 [cs.LG] 28 Mar 2024. Primary source read as HTML https://arxiv.org/html/2403.12031v2 (250654 B, sha256 4fcea2ee262e02b980... full 4fcea2ee262e02b9) and PDF https://arxiv.org/pdf/2403.12031v2 (1878517 B, sha256 1df05ba3e75ad24c... full 1df05ba3e75ad24c3d9b...), plus `pdftotext -layout` 115383 B. Retained at /tmp/routerbench.html, /tmp/routerbench.pdf, /tmp/routerbench.txt and /tmp/rb-core.txt 2026-11-02.
- Points used (paper's own wording via pdftotext): §3.1 Setup — router `R_θ(x) → LLM_i ∈ L`, expected cost `c_{R_θ}(D)=E[c(R_θ(x))|x∈D]` and expected quality `q(R_θ(x))` analogously; §3.1 Linear Interpolation — `R_int(R_{θ1},R_{θ2},t)(x)=R_{θ1}(x) w.p. t else R_{θ2}(x)` with `E[c_{R_int}]=t·c_{R_{θ1}}+(1-t)·c_{R_{θ2}}` so any point on the segment `(c1,q1)-(c2,q2)` is attainable; §3.1 Extrapolation — extend to `[0,∞]` by adding wasteful cost or interpolating the null router `(0,0)` with `R_θ`; §3.2 Non-Decreasing Convex Hull — from a point set `S` an affine combination reaches any point in its convex hull, `S_ndch ⊆ S` keeps only the non-decreasing hull vertices (Fig.2 middle: A,B,C keep, D,E replaced by affine combination of A,B,C; Fig.2 caption); §3.2 `R_zero` — probabilistic mix of `L` on their `S_ndch` at cost `c`, "significant only if it demonstrates superior performance compared to R_zero"; §3.3 AIQ — `AIQ(R_θ)=1/(c_max-c_min) ∫_{c_min}^{c_max} \tilde{R_θ} dc` after convexifying and interpolating each system to common `[c_min,c_max]` (Fig.2 right ABC extrapolated to (0.1,0)); benchmark construction — 405k inference outcomes from 11 LLMs × 8 datasets (Fig.1 left, Appendix A.3), costs in dollars, quality vs gold.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py:1-46` — pure admission function `decide(role,status,allowlist,current_route,now_ms,reservations,reserve_pct,reserve_policy,min_hold_ms)→{decision,selected,reason,evidence_ids,next_reconsider_ms,binding_reset?}`. Classifies each FRESH window by `effective = remaining_fraction - reserved` against fixed `reserve_pct` 0.15: `0` or `provider_status=="depleted"` → depleted, `0<effective<reserve_pct` → reserve, otherwise healthy. `UNKNOWN`/`STALE`/`UNATTRIBUTED`/`NOT_CONFIGURED` are in `_INELIGIBLE_STATES` and produce `hold`, not exhaustion. Hysteresis: compatible healthy current → `stay` with `next_reconsider_ms=now+min_hold_ms` (600000 ms default); same-provider alias preferred before cross-provider fallback. Allowlist decides suitability, "no headroom percentage decides model suitability."
- `src/mishe_tauftauf/token_usage.py` and `src/mishe_tauftauf/engine_usage.py` aggregate usage but emit only totals per window/model/folder; no cost-quality plane, no hull, no baseline.
- Occurrence sweep ≠ measurement: keyword hits for `engine/route/quota` do not constitute a cost-quality hull.

## What is new here

RouterBench formalises what this plant lacks: a cost–quality plane and a discipline for comparing routers that differ in cost domain.

1. **Plane, not point.** The paper plots routers as `(c,q)` and reaches any convex point by randomised interpolation. The plant's `decide` has one operating point (reserve 0.15, hold 600s) and no record of where it sits on any hull. Measuring future routing work on a `(c,q)` plane would break the single-threshold habit that the 2026-10-31 RouteLLM discard (Ong et al. §3.1 `α` on `P(win|q)`) left unmeasured.

2. **NDCH domination filter.** The non-decreasing hull discards dominated routers (Fig.2 D,E) — a router is wasteful if a probabilistic mix of others is cheaper and better. Applied here, a candidate `engine_policy` variant is useful only if it survives the hull, not merely if it beats one scalar.

3. **Zero router baseline.** `R_zero` (NDCH of the underlying LLMs alone) is the benchmark a router must beat. No analogue exists for engine routes; the plant has no baseline that says "random healthy-alias mix at cost c" to beat.

4. **AIQ over common interval.** After convexifying, each system is interpolated/extrapolated to `[c_min,c_max]` (null router to 0, wasteful extension to ∞) and scored by area under the hull. This avoids the paper's noted failure of single-λ (`willingness to pay`) comparisons. The plant currently has no such normalisation.

Distinct from 2026-10-31 recent (RouteLLM, Ong et al. 2406.18665, quality-threshold `α` on preference data, discard for missing `P(win|q)`): this source is RouterBench 2403.12031v2, a benchmark + §3 theoretical framework (interpolation, extrapolation, NDCH, R_zero, AIQ with 405k/11×8 dataset and dollar costs). Different mechanism family (measurement hull vs threshold dial), different equations cited (c_R/q_R, R_int(t), S_ndch, AIQ integral), different evidence files (pdf/html shas distinct).

## Application

target: src/mishe_tauftauf/engine_policy.py

Proposed to genome as additive docs-only header citation (no logic, threshold, or hysteresis change):

- Cite Hu et al. RouterBench arXiv:2403.12031v2 (HTML 250654 B 4fcea2ee..., PDF 1878517 B 1df05ba3..., Fig.2, §3.1-3.3) with the four instruments verbatim: `c_{R_θ}, q_{R_θ}`, `R_int(t)` with `t·c1+(1-t)·c2`, `S_ndch`, `R_zero`, `AIQ=1/(c_max-c_min)∫\tilde{R} dc` and the `405k/11×8` construction note.
- Note the receiver gap explicitly: the plant's current `q` is undefined (no per-output graded quality/gold; `q(o)` against gold in the paper), so AIQ is not computed in this change; the header records that future routing work that introduces a quality proxy (e.g. task success, absence of `queue`/`hold`) should evaluate candidates via NDCH/AIQ over a common cost interval via interpolation to `R_zero`/`(0,0)` and wasteful extrapolation, and should report domination vs `R_zero` rather than a single reserve threshold point.
- Retain existing hold semantics (`UNKNOWN/STALE` → `hold` ineligible) — the paper's null-router extrapolation to cost 0 is the analogue of holding on missing evidence, not a fallback to a second policy.

## Limits

- Paper assumes graded `q(o)` against gold and stationary `E[·|D]`; the plant's `c` is quota fraction/provider-reported, not dollars, and `q` would be a proxy (e.g. `1-queue_rate`) with no gold. Interpolation randomisation assumes i.i.d. prompts.
- AIQ needs a shared `[c_min,c_max]` and convexified hulls — a single-point policy (current `decide`) has no hull without variants to compare.
- Benchmark costs are inference dollars; the plant's costs are quota entitlements with resets, so dollar accounting does not transfer. No live hull was measured this turn.
