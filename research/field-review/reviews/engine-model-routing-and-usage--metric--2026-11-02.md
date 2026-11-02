---
field: engine-model-routing-and-usage
angle: metric
date: 2026-11-02
status: applied
owner: genome
---

# AIQ over the cost-quality hull as the routing metric for this plant

## What we read

- Hu, Qitian Jason et al., "RouterBench: A Benchmark for Multi-LLM Routing System", arXiv:2403.12031v2 [cs.LG] 28 Mar 2024 (Proceedings of ICML 2024), https://arxiv.org/pdf/2403.12031 -> /tmp/routerbench.pdf PDF 1878517 B sha256 1df05ba3e75ad24c3f8e00039a4a35704eb0f8df44088616f8244936e0a9aa01 (16 pp., LaTeX pdfTeX-1.40.25, CreationDate 2024-03-29T01:42:16Z). Code/data: https://github.com/withmartian/routerbench (cited p.1). Fetched 2026-11-02. Quotations via `pdftotext -layout` of the fetched PDF; figure text transcribed from captions.

- Companion dataset note for scope: same venue lists 405k inference outcomes sampled for the paper's benchmark.

Verbatim metric (headings and quotes as printed, formulas as typeset):

- Framing (Abstract, p.1): "we present ROUTERBENCH, a novel evaluation framework designed to systematically assess the efficacy of LLM routing systems, along with a comprehensive dataset comprising over 405k inference outcomes from representative LLMs" / "We further propose a theoretical framework for LLM routing, and deliver a comparative analysis of various routing approaches through ROUTERBENCH" / Cost span claim p.2: "the monetary costs of LLM services can routinely vary by factors of 2-5x for comparable levels of performance."

- Setup §3.1 (p.3): "Consider a set of models L = {LLM1,...,LLMm} a dataset D consisting of examples xi ... For each model LLMj, we evaluate its performance by generating an output o^ji = LLMj(xi) for each example xi. Each output o^ji has two associated quantities: the cost c(o^ji) of generating that output and the quality or performance q(o^ji) of the output itself." Expected forms typeset as `c_m = E[c(LLM_m(x))|x in D]` and `q_m = E[q(LLM_m(x))|x in D]`. Router `R_theta(x) -> LLM_i in L` with parameters theta including "the maximum price the user is willing to pay" (§3.1). Expected router cost `c_{R_theta}(D) = E[c(R_theta(x))|x in D]` and similarly `q`.

- Linear interpolation (p.3, Fig.2 left): "consider two routers, R_{theta1} and R_{theta2}, we can formulate a third router, R_int(R_{theta1},R_{theta2}), based on the following principle: given a prompt x select t in [0,1] such that: ( R_{theta1}(x), w.p. t  R_int(R_{theta1},R_{theta2},t)(x) = R_{theta2}(x), w.p. 1-t" and by linearity `E[c_{R_int(x)}|x in D] = t * c_{R_{theta1}} + (1-t) * c_{R_{theta2}}` and similarly for performance. Caption: "To achieve the average of A and B, we build router C which routes to A or B with 50% probability each, and it performs the average of A and B in expectation."

- Extrapolation (p.3): "which enables all routers to extend to the cost domain [0, infinity]. For a given router R_theta, we can trivially add more cost to the system without adding performance (for example repeat LLM generation k times and only take the last generation as final output) and thus extend the cost to infinity. To extend the router to a smaller cost domain, we simply interpolate the null router (zero cost, zero performance) and R_{theta1}. Thus we are able to achieve any cost level between [0, infinity] to when comparing routers with different domains."

- Non-decreasing convex hull §3.2 (p.4, Fig.2 middle): "We identify S_ch subset S as the subset of points that delineate the vertices of this convex hull. Furthermore, it's possible to configure a non-decreasing convex hull from S_ch, ensuring that for any two points (c1,q1) and (c2,q2) where c2 >= c1, it follows that q2 >= q1. Intuitively, if the extra cost of c2-c1 does not bring any performance improvement, it is advisable to simply extrapolate (c1,q1) to the domain of c2, and (c2,q2) could be (c2,q1)." Example: "Consider points A to E, we can construct the non-decreasing convex hull consisting of points A, B, and C. D and E as they can be replaced by a strictly superior affine combination of A, B, and C."

- Zero router (p.4): "we define the Zero router (R_zero) as a router that selects LLMs from {LLM1,...,LLMm} based on their collective non-decreasing convex hull. For a specified cost c, R_zero provides a probabilistic mix of LLMs that maximizes expected output quality with a simple, mathematics-driven routing strategy. R_zero serves as a basic benchmark for assessing the efficacy of other routing systems; a router is deemed significant only if it demonstrates superior performance compared to R_zero."

- AIQ §3.3 (p.4-5, Fig.2 right): "We define AIQ (Average Improvement in Quality) for one of the routing systems as follows: AIQ(R_theta) = 1/(c_max - c_min) * integral_{c_min}^{c_max} f_{R_theta} dc" (typeset as `1/(cmax - cmin) ∫_{cmin}^{cmax} R_{f_theta} dc` with f the hull-interpolated quality at cost c). Text after: "With the equation above, we can calculate AIQs for any group of routing systems to get a clear understanding of their relative performance, which is demonstrated in the right of Figure 2. Rather than performing complex graphic analysis, AIQ allows users to measure router performance in a straightforward way." Caption right: "To compare, we interpolate A and B to c_min = 0.1 and c_max = 0.8, respectively, and then calculate the area under the curve normalized by c_max - c_min to derive AIQ."

- Evaluation notes §5.3-5.4 carry AIQ numbers (e.g., Fig.4 WizardLM/MMLU etc., Fig.6 RAG: Zero 0.632 vs KNN 0.695/MLP 0.675) but the metric definition above is the cell's contribution.

Limits stated in source (§6, p.8): "ROUTERBENCH currently only focuses on performance and economic cost. It is meaningful to include more evaluation criteria, such as latency, throughput, and others" / "There are also many LLMs and tasks that are not included in ROUTERBENCH due to the limitation of time" / retrieval two-stage routing for HotpotQA/NaturalQuestions remains future work. WTP and judge-error parameters are synthetic controls, not production calibration.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py:119-165` `_classify_account` grades a window by level only: `effective = remaining_fraction - _effective_reserved(alias, window, reservations)` with `reserve_pct` default 0.15 -> healthy/reserve/depleted/hold. No cost c_m, no quality q_m, no c-q plane, no interpolation, no hull.

- `src/mishe_tauftauf/engine_usage.py:46-57,151-162,228-292` `_window_verdict` (FRESH/STALE/UNKNOWN by age), `_save_last_good` overwrites `engine/last-good.json` with one row per alias (one retained sample), `status()` surfaces remaining fraction + reset. It keeps no (c,q) series per routing decision; the metric the predecessor needed (burn rate) failed for the same reason — history not kept — and this cell's metric needs a comparable two-dimensional series.

- `src/mishe_tauftauf/token_usage.py:16-65,68-294` aggregates token counts and cost from OMP `stats.db` over windows `24h/7d/30d` via `report()` -> totals, per-model and per-folder breakdowns, and formats cost as dollars. It exposes the cost axis (dollars per window) but has no quality axis, no router parameterization over theta (e.g., WTP), and no aggregation that forms (c_m, q_m) points or their hull. `stats_boundary.py` enforces DP queries over the same store but likewise does not form a routing curve.

- Routing in `engine_policy.decide()` chooses by allowlist order + health, emitting `next_reconsider_ms = now+600s` for hold; there is no scalar analogous to AIQ that would summarize one routing configuration's trade-off over a shared cost domain [c_min, c_max].

## What is new here

RouterBench's AIQ is a different metric from the predecessor burn-rate for this same cell, with a disjoint formalism and dataset. Predecessor (2026-10-31 metric, SRE Workbook ch.5 "Alerting on SLOs", https://sre.google/workbook/alerting-on-slos/) defined burn rate as consumed error-budget fraction / elapsed window fraction with a two-window example (1h+5m at burn 14.4 and 6h+30m at burn 6 for a 99.9% SLO) and was discarded because it needs per-window sample history the plant overwrites. This review defines AIQ as the normalized area under the non-decreasing convex hull of (cost, quality) points: `AIQ = integral f(c) dc / (c_max - c_min)` over a shared domain after linear interpolation, extrapolation to [0, infinity] via null router, and hull filtering (covering dominated points D/E). Its zero router is the hull over individual LLMs (or, by analogy, over accounts/routes) and is the "do no harm" benchmark: a learned router is significant only if its hull beats R_zero's. The paper's benchmark instantiates it with 11 LLMs x 8 datasets (MMLU, GSM8K, MBPP, Hellaswag, Winogrande, ARC-Challenge, MT-Bench, RAG) scoring AIQ differences that reveal 2-5x cost spread at matched quality.

For this plant the transfer is the cost-quality plane itself and the hull-normalized scalar: the plant's "cost" maps naturally to token dollars per decision interval (already in `token_usage.report()`), while "quality" would need a per-decision scorer (task correctness, judge score, or downstream verdict attainment) that the engine path does not currently log alongside cost. Without a paired (c,q) series there is no hull to integrate, so AIQ cannot be computed from current persisted state — the same persistence gap as burn rate, but in two dimensions rather than one. The value for this plant is the decision rule the metric encodes: interpolate between configurations, compare on a common [c_min, c_max], and beat the zero-router hull — rather than ranking single thresholds.

## Application

target: src/mishe_tauftauf/token_usage.py

Additive header note (no behavior change) citing Hu et al. 2024 arXiv:2403.12031v2 PDF 1878517 B sha256 1df05ba3e75ad24... (ICML 2024, https://github.com/withmartian/routerbench) and carrying four gated claims into the module docstring: (a) the c-q plane mapping for this plant: `c_m = E[c(LLM_m(x))]` maps to dollars per routing interval from `report()` and `q_m = E[q(...)]` maps to a not-yet-logged per-decision quality verdict that would need to be paired per interval to form (c, q) points; (b) the hull construction pipeline: linear interpolation `R_int(t)` with `E[c]=t*c1+(1-t)*c2` and extrapolation to [0, infinity] via null router (0,0) and wasteful repetition, then non-decreasing convex hull filtering (`c2>=c1 => q2>=q1`, dominated points replaced by affine combos as in Fig.2 middle); (c) the zero router `R_zero` as the probabilistic hull over individual routes/accounts at cost c, which bounds any learned policy — significant only if its hull beats `R_zero`; (d) AIQ as `1/(c_max-c_min) * integral_{c_min}^{c_max} f(c) dc` over the shared domain (Fig.2 right, `c_min=0.1` to `c_max=0.8` example) as the plant's scalar for "routing efficiency at matched quality," with the paper's finding that costs vary 2-5x at comparable quality indicating the headroom a router can capture.

Limits retained: the paper's cost is LLM inference dollars and its quality is task accuracy over 405k RouterBench outcomes; the plant's cost is quota-window dollars and its quality signal is not yet defined or paired, so the numeric `c_min/c_max` and hull shape from the paper do not transfer — only the construction does. The note documents the missing paired history, not a computed AIQ. No change to `report()`/`render()` purity or to `engine_policy.decide()`; translating to quota-window routing (e.g., WTP analog over remaining-fraction) needs a separate sizing pass and a persisted (c,q) interval log.

## Limits

- The c_min=0.1/c_max=0.8 numbers in Fig.2 are an illustrative shared domain for ABC vs DEF, not a calibrated cost range for this plant's provider windows.
- The paper's quality is dataset accuracy (MMLU etc.); the plant has no single q — a downstream verdict or judge score would need to be chosen and its sampling bias documented.
- Source formulas were transcribed from `pdftotext -layout` of the 1df05b... PDF; figure curves are raster and were read from captions and §3 text, not re-digitized.
- Code was read at HEAD 1f905bdc; running pin 054d4c64 may differ.
