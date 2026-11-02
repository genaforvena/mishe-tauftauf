---
field: failure-and-recovery
angle: metric
date: 2026-11-02
status: applied
owner: health
target: src/mishe_tauftauf/recovery_io.py
---

## What we read

**Betsy Beyer, Chris Jones, Jennifer Petoff & Niall Richard Murphy (eds.), *Site Reliability Engineering: How Google Runs Production Systems*, O'Reilly 2016, Chapter 6 — Monitoring Distributed Systems** — read HTML canonical at <https://sre.google/sre-book/monitoring-distributed-systems/> (73,467 bytes, fetched 2026-11-02; `curl` + `sed` extraction 4,800 tokens), plus the front-matter page at <https://research.google/pubs/the-tail-at-scale/> (Dean & Barroso, CACM 56, 2013, pp. 74–80 — abstract surveyed for origin of the tail result the SRE chapter operationalises). The SRE chapter is the implementation the tail paper motivates; we read the operational metric it prescribes.

Four passages carry the metric this plant would misreport if it averaged a small and bursty sample:

1. **The four golden signals over the mean (Ch. 6, "The Four Golden Signals").** "The four golden signals of monitoring are latency, traffic, errors, and saturation. If you can only measure four metrics of your user-facing system, focus on these four." And: "Latency increases are often a leading indicator of saturation. Measuring your 99th percentile response time over …" — the signal is a percentile over a distribution, not a mean of probes.

2. **Worrying About Your Tail — mean hides the tail, the tail becomes the median at scale (§ "Worrying About Your Tail (or, Instrumentation and Performance)").** "When building a monitoring system from scratch, it's tempting to design a system based upon the mean … If you run a web service with an average latency of 100 ms at 1,000 requests per second, 1% of requests might easily take 5 seconds. If your users depend on several such web services to render their page, the 99th percentile of one backend can easily become the median response of your frontend." The 50× example is explicit: if 1% are 50× the mean, the rest are "about twice as fast as the mean" — the mean is hopeful thinking when you have not measured the distribution. Dean & Barroso's origin result behind this paragraph is that at warehouse scale even rare variance dominates user-visible latency.

3. **Histogram buckets, not scalars — exponential boundaries (≈×3) (§ same, para 2).** "The simplest way to differentiate between a slow average and a very slow 'tail' of requests is to collect request counts bucketed by latencies (suitable for rendering a histogram), rather than actual latencies: how many requests did I serve that took between 0 ms and 10 ms, between 10 ms and 30 ms, between 30 ms and 100 ms, between 100 ms and 300 ms, and so on? Distributing the histogram boundaries approximately exponentially (in this case by factors of roughly 3) is often an easy way to visualize the distribution." The metric is the bucket histogram, from which any percentile is derived; no single scalar is stored.

4. **Choosing resolution and the Bigtable case study — mean-driven SLO alert-spammed, fixed by a percentile (§ "Choosing an Appropriate Resolution" + "Bigtable SRE: A Tale of Over-Alerting").** "Observing CPU load over the time span of a minute won't reveal even quite long-lived spikes that drive high tail latencies." Low granularity hides the tail. The case study: "Many years ago, the Bigtable service's SLO was based on a synthetic well-behaved client's mean performance. Because of problems in Bigtable and lower layers … the worst 5% of requests were often significantly slower … Email alerts were triggered as the SLO approached, and paging alerts … were firing voluminously … Many of the pages were non-urgent." Remedy was three-pronged: improve Bigtable, "temporarily dialed back our SLO target, using the 75th percentile request latency", and disabled email alerts — the SLO moved from a mean to a percentile precisely to stop alert spam from tail-driven mean violations.

**Limits the source states, which this review keeps.** Histogram buckets trade resolution for cost — per-second bucket increments aggregated per minute give spike visibility without per-sample retention, but still need bounded storage. The tail-tolerance techniques of Dean & Barroso (hedged requests, tied requests) add load for redundancy; the SRE chapter warns monitoring itself must stay simple ("As Simple as Possible, No Simpler" — infrequently exercised collection and alerts "should be up for removal") and that the Bigtable back-off was temporary while root causes were fixed, not a permanent lowering of the bar.

## What this plant already does

*Mean and a single latest seconds value where the SRE metric says to keep a histogram.*

`src/mishe_tauftauf/recovery_io.py` `run_probe` (:93) returns `{"ok","reason","seconds": time.monotonic()-started}` — one scalar per probe. `status`/`check`/`guard`/`evaluate` (:367, :409, :600, :617) sample pressure and seed-lock availability every few seconds and emit `pressure-*.json` and `guard.json`/`delivery-*.json`, but never bucket the series: no histogram, no p50/p95/p99, no count per boundary. The headline and the `recovery/` outbox likewise treat counts as scores — the "token counts are not recovery scores" warning in `docs/recovery.md:152` without a distribution to replace the mean.

`src/mishe_tauftauf/scan_freshness.py` `aoi_summary` (:150–294) already computes a distribution summary for stamp ages — `mean_age, p95, t_p95, t_max, t_median` with `p95_index = ceil(0.95*n)-1` (:260–291) — but only for Age-of-Information over a window, not for recovery probe latencies or the seed-lock hold times that matter on the recovery path. The `SEED_HOLDER_STUCK_SECONDS=120.0` comment (:188) cites measured holds "seed yield 15.8-30.2 s, seed run 0.5-2.1 s, one hold of 86 s" — a spread where a mean of ~20 s would hide the 86 s tail that outlasts the 120 s stability window, exactly the SRE paragraph's 1% at 50×.

`src/mishe_tauftauf/trial_metrics.py` `collect` (:101) records delivery latency per patch as `latency[path.stem] = (at - prepared_at).total_seconds()` (:154) — again one scalar per delivery, reported as a JSON map, not as bucket counts. No module bucket-counts latencies or exposes a percentile SLO; `page`/`over-alert` risk from Bigtable recurs if a future SLO is set on a mean of these scalars.

*Consequence the metric predicts.* With ~4–20 recovery-relevant holds per year per role, a mean "probe latency" of ~15 s with one 86 s excursion is indistinguishable from a healthy mean with no excursion — the SRE example's "most requests twice as fast as the mean" while the 1% dominates the user tail. Without bucketed retention, the per-minute CPU/lock spike that creates the tail is invisible at the `interval`/`checked_at` granularity (the "Choosing Appropriate Resolution" warning).

*The plant's best nearby practice.* `scan_freshness.aoi_summary`'s p95 and `recovery_policy.transition`'s explicit `UNKNOWN` when evidence is missing are the honest vocabulary the metric needs: unknown stays unknown rather than imputing a mean. They just do not yet carry the SRE prescription — bucket counts with roughly exponential boundaries — to the recovery probe and lock-hold measurements where the tail matters.

## What is new here

**Do not publish mean probe or hold latency — keep the SRE histogram of bucket counts with exponential boundaries (≈×3) and derive percentiles from the histogram; set the SLO on p75/p95, not the mean.**

Three ideas transfer directly, each with a line number where it lands and each bounded by what the sources concede.

1. **The metric is the histogram, not the scalar.** The SRE recipe: choose boundaries `b₀=0 < b₁ < b₂ < …` with `b_{i+1}/b_i ≈ 3` (example 10,30,100,300 ms scaled to this plant's seconds) and keep `counts[i] = #{ samples with b_i ≤ t < b_{i+1}}` rather than keeping each `t`. Percentiles are then cumulative over counts. This is cheaper than per-sample retention and survives the "per-minute aggregation hides spikes" problem when probes run every `interval` seconds but holds are tens of seconds. For recovery, boundaries around `0.5, 1.5, 5, 15, 30, 90, 120, 300 s` cover the observed `0.5-86 s` holds while staying exponential.

2. **Same 1% becomes the median with fan-out — and this plant fans out.** SRE: one backend's p99 becomes the frontend median when the page depends on several backends. Here the "page" is a wake settlement that touches `recovery_io.run_probe` + `seed_lock_probe` + `controller_lock` + one or two journal replay probes. Each is fast at the median; one 86 s hold at p99 in any of them is the user-visible tail for the whole settlement. A mean over settlements conflates which hop tailed — the histogram per hop does not.

3. **If the SLO is on the mean, the alert is tail-driven spam; move it to p75/p95 while fixing the tail.** Bigtable: mean-driven SLO paged voluminously on the worst 5%; moving to p75 stopped the spam and gave room to fix Bigtable and the storage stack. The analogue: a hypothetical "mean hold must be < 5 s" or "mean probe < 200 ms" SLO would page on one 86 s excursion. Replace it with `p95(holds) < 30 s` and `p99(probe_latencies) < 150 ms`-style objectives, with the histogram retained so the tail is visible in dashboards.

**Limits kept.** Histogram buckets are a coarse approximation — exact order statistics need sorted samples (as `scan_freshness` keeps for `p95`). The review does not propose hedged/tied requests (Dean & Barroso's redundant-execution tail-tolerance) for lock holds — the cost of double-writing a lock holder is not warranted here. The Bigtable 75th-percentile back-off was temporary; the SLO returns tighter once the tail is fixed. Small N per role-year makes any p99 noisy; the report should still publish `n, mean, p95, bucket counts` so the reader sees the width (as the prior metric review's `mttx_report` does for durations).

## Application

target: src/mishe_tauftauf/recovery_io.py

**Add the SRE histogram beside `run_probe`/`seed_lock_probe` — additive, pure, no change to the probe's success predicate or to `recovery_policy.transition` thresholds.**

1. **Add `histogram_buckets(samples, boundaries=None)` and `latency_histogram_summary(counts, boundaries)` beside `run_probe` (:93) and `seed_lock_probe` (:297) in `src/mishe_tauftauf/recovery_io.py`.** Default `boundaries = (0.5, 1.5, 5, 15, 30, 90, 120, 300)` seconds scaled from the SRE ×3 recipe to cover the 0.5–86 s holds in the file's own comment (:185–188); caller may supply custom boundaries (seconds) — pure functions, testable without I/O. `histogram_buckets` maps `samples: list[float]` → `dict{bucket_index: count, total, boundaries}`; `latency_histogram_summary` walks cumulative counts to return `{n, p50, p75, p95, p99, buckets}` without sorting every probe after the fact. Reuse `scan_freshness`'s `ceil(0.95*n)-1` index logic for consistency, but over cumulative bucket counts rather than sorted scalars.

2. **Feed the histogram from the evaluations the plant already emits.** `evaluate` (:617) already collects baseline vs current rows; after the change it also appends each `run_probe(... )["seconds"]` and each `seed_lock_probe(...)["seconds"]` into rolling `samples.jsonl`-adjacent `histogram-*.json` artifacts (bounded file, `MAX_BYTES` same as other recovery files, 4 MiB). No new I/O path — same `save`/`read_json` helpers. The wall/headline never shows a single mean alone; it shows `probe_latency n=42 p50=1.1s p95=18s buckets {≤1.5:31, ≤15:9, ≤90:2}` so the tail the SRE chapter warns about is visible.

3. **Let the SLO gate consume it without changing thresholds.** The `deadline`/`stable_seconds`/`STUCK_SECONDS` thresholds stay where they are. A separate `slo_check(histogram, *, p=0.95, limit=30.0)` helper (same file) returns `(ok, reason)` like `guard`'s `(ok, reason)` vocabulary, evaluating `p95 < limit` rather than `mean < limit`. The existing `guard(check)` and `policy.transition(deadline)` remain the availability gate; the histogram SLO is an additional observability/alerting check, not a second latch — so the Bigtable lesson is embodied (tail-aware SLO beside availability) without adding a new blocking state.

4. **Keep the existing compare warning.** `recovery_policy.compare` keeps `causation: uncontrolled before/after; attribution unproven` — a histogram's p95 shift is a *necessary* check for a claim, not a sufficient proof of it. Narrow-step measurement (jitter change shortens contention, not detection) remains the stronger evidence, as in the prior metric review.

**Acceptance (deterministic, runnable from this repository):**

- `python -m py_compile src/mishe_tauftauf/recovery_io.py` and `python tools/check_parsing.py` still exit 0 — the new symbols are additive with no change to `run_probe`/`seed_lock_probe`/`status`/`check`/`guard`/`evaluate` signatures.
- `histogram_buckets([0.4, 0.6, 1.0, 2.0, 18.0, 86.0])` with defaults returns `counts {0:1, 1:2, 2:1, 4:1, 5:1, total 6}` (boundaries 0.5,1.5,5,15,30,90,120,300) and `latency_histogram_summary` reports `p50≈1.0s, p95=86s, p99=86s` — the 86 s tail is not averaged away.
- `latency_histogram_summary` on a synthetic uniform 100-sample SRE example at 100 ms mean reproduces the chapter's illustration: `mean≈100ms p95≫means` when one bucket at `>300ms` holds 1 count, while `mean` stays ~100 ms — proving the scalar is misleading.
- With `n=3` (plant-typical per-role year) the summary still publishes `buckets` and `n`, with `reason: small N; p99 = max(bucket)` rather than a precise quantile — honest about width, as `scan_freshness` is on age.

**Ownership.** `src/mishe_tauftauf/recovery_io.py` is the literal recovery boundary (`run_probe`, `seed_lock_probe`, `status/check/guard/evaluate`) consumed by every recovery decision; `health` is the resolver for `failure-and-recovery / native recovery, health, supervisors, watchdogs` and for `SLO / monitoring` delivery gaps that a mean-based headline would hide. The histogram rows live in `SITE/recovery/` beside the probe outputs the SRE chapter measures.

**One thing this review does not ask for.** It does not add hedged/tied requests, speculative re-execution, or a new availability blocker to minimise mean latency — those are Dean & Barroso's extra-cost tail-tolerance techniques and the SRE chapter names them as separable from the measurement. It only publishes the honest histogram the optimisation would need, in the module that already defines what "slow" means.
