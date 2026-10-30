---
field: failure-and-recovery
angle: metric
date: 2026-10-29
status: applied
owner: health
target: src/mishe_tauftauf/recovery_policy.py
---

## What we read

**Incident Metrics in SRE: Critically Evaluating MTTR and Friends** — Štěpán Davidovič (Google). O'Reilly/Google SRE report, March 2021, 38 pp. — read full text at:

- <https://sre.google/resources/practices-and-processes/incident-metrics-in-sre> (HTML abstract page)
- <https://static.googleusercontent.com/media/sre.google/en//static/pdf/IncidentMeticsInSre.pdf> (PDF, O'Reilly 978-1-098-10313-2)

Four passages carry the metric this plant would misreport if it published a mean.

1. **The life-cycle and the definition (p. 8–10, Fig. 1).** First product impact → Detection → Mitigation (severe impact gone, still degraded) → Recovery (fully normal). MTTR = mean(recovery − first impact) across incidents; MTTM = mean(mitigation − first impact). "The reality is that applying these metrics is trickier than it seems, and these popular metrics are dangerously misleading in most practical scenarios."

2. **The distribution is heavy-tailed and the variance is the point (p. 10–13, Figs. 2–4).** Three public status-dashboard corpora (N=798,350,2186; 173,103,609 in 2019) plus Google's 1-year internal corpus (several × larger) are all positively skewed, roughly lognormal/gamma, with huge variance (Table 1: mean 2h26m ±5h16m, 2h31m ±5h1m, 4h31m ±6h53m). Most resolve quickly, some linger for days; "black swan" tail is structural, not an outlier to trim (worst 1–2% beyond 3 days kept).

3. **Monte Carlo in parallel universes — why a true 10% shorter incident is unseen, and a null change looks like one (p. 13–18, Figs. 5–6, Table 2).** 100,000 repeats: draw two samples N₁=N₂ from empirical durations, shorten one by 10%, compare MTTRs. At 2019 N (173,103,609): 38%,40%,20% of universes show MTTR *worsening* despite true improvement; chance of seeing "≥15 min better" is 49%,50%,64%. Conversely with no change at all, 19%/23%/10% show ≥30 min "improvement" by chance. 90% CIs at N₁+N₂=100 are ±1h44m/±1h39m/±2h16m; at 1,000 still ±33m/±31m/±43m — a 10% MTTR shift (≈15–27 min) sits inside the interval even at 1,000 incidents. "Having enough incidents that would allow for a robust analysis … is undesirable."

4. **Going beyond means does not save the metric, and the replacement is tailoring plus simulation (p. 20–26, Tables 3–5; p. 26–27, 31–34).** Median (Table 3: at N=1,000, 90% CI ±11m/±9m/±29m on medians 42m/67m/170m), p95, geometric mean (Table 5) all keep 10% inside CI; sum is MTTR×N/2 so same story (Table 6). Analytical z-test matches simulation only when CLT already holds (Q-Q, Fig. 9: <~1 quarter year not normal). §"Better Analysis Options" (p. 31): no silver-bullet replacement; measure the *question* (one step, e.g. time-to-detection, not end-to-end), consider direct reliability indicators (availability/SLI burned, not MTTR), and "Put Your Chosen Metrics to the Test" by re-running the same Monte Carlo on whatever you propose. Large-N exception (hardware drives, p. 31) and dramatic cuts (≈80%) are the only honest MTTx uses.

**Limits the source states.** Excludes <3 min and >3 d (1–2%); binary mitigated/impacted decision is subjective (§"shallow incident data", Allspaw); availability/SLI analysis "would likely not be broadly relevant" and is not done; incident counts themselves are erratic year-to-year (Fig. 8) and confounded by portfolio/reporting changes; result holds under ideal metadata — real-world worse.

## What this plant already does

*One bounded retry count, one deadline, no duration distribution — exactly the single-number temptation the paper measures.*

`src/mishe_tauftauf/native_recovery.py` caps work by count: `DEFAULT_RETRY_LIMIT=3` (:100), `fresh_attempt` returns `attempt-N/` or `None` (:489), `attempt_disposition` chooses `live/settled/reconcile/fresh-attempt` from `classify_attempt`'s `SETTLED/RETRYABLE/RECONCILE/UNKNOWN` (:92–95, :568). A wake that used every attempt is held with `reason: used every bounded fresh attempt (3) …` (:612). No duration is recorded: `Disposition` carries `attempt: Path|None, verdict, reason, evidence, settled, transient` (:544–565) but no `first_failed`, no `recovered_at`, no per-attempt elapsed.

`src/mishe_tauftauf/recovery_policy.py` `transition(previous, *, result, now, owner, backup, deadline, stable_seconds)` (:8) carries the time the paper needs — `first_failed`, `passing_since`, `restored_at`, `episode`, `checked_at` — and flips among `HEALTHY/FAILED/MITIGATED/RECOVERING/RESTORED/UNKNOWN` with a `deadline` backup request (:46–48). `capacity()` (:58) does the same for host pressure. `compare(before, after)` (:80) even warns that its verdict is `causation: uncontrolled before/after; attribution unproven` (:93) — the plant already knows a before/after delta is not proof. But none of these publish an *incident duration* (`restored_at − first_failed`), let alone its distribution.

`src/mishe_tauftauf/recovery_io.py` `status`/`check`/`guard`/`evaluate` (:229, :253, :402, :419) sample pressure and lock availability every few seconds and emit `pressure-*.json`; `evaluate` compares baseline vs current but not incident durations. The dashboard headline `GREEN — mind ok idle 18s; patches attempted=60 checked-delivery=45 …` and the `recovery/` outbox treat counts as scores — the exact "commit, message and token counts are not recovery scores" warning in `docs/recovery.md:152`.

*Consequence the metric predicts.* With ~4–20 incidents per year per role (wakes that actually need `fresh-attempt`), any `MTTR = mean(restored − first_failed)` will have 90% CI width of hours (paper Table 2 at N=10: ±5h41m for Company A). A real 10% faster recovery after a jitter or breaker change would be invisible, and a null change would look like a win one time in five — the simulation of Fig. 6 run on the plant's own durations.

*The plant's best nearby practice.* `recovery_policy.compare`'s `causation unproven` note and `native_recovery`'s `(value, reason)` evidence tuples (`_evidence` :361, `read_blocked_state` :682 returning `(dict|None, str|None)`) are the honest vocabulary the metric needs: unknown stays unknown rather than silently defaulting. They just do not yet carry the duration distribution that would make the honesty measurable.

## What is new here

**Do not publish mean time to recovery across wakes — measure the step you changed, publish the distribution, and ship the simulation that proves the number cannot tell you what you claim.**

Three ideas transfer directly, each with a line number in this repository where it lands and each bounded by what the paper concedes.

1. **The rate-limiting defect is not the mean but N and skew.** The paper's Fig. 2–4 shows N modest, tail heavy, variance ≈2× mean. The plant's N is smaller still: at most a handful of `RETRYABLE` wakes per window. Table 2's CI at N₁+N₂=10 (±5–7 h) already exceeds the plant's `deadline` / `stable_seconds` / `max_gap=90` scales. Any `MTTR` headline would therefore violate the paper's central test: run the Monte Carlo on your own durations and see whether a 10% change would sit inside the CI — it will, even at N=100. The mechanism is not "retry smarter" but "measure narrower."

2. **Tailor to the question: time-to-detection and time-to-mitigation beside time-to-recovery.** §"Tailor Your Metric to the Question" (p. 32): "If you are improving one step of the journey, including all other steps in the aggregate makes your ability to understand the impact … worse." The plant has three steps with distinct owners: detection (`transition` flipping `HEALTHY→FAILED`, `first_failed` set), mitigation (`FAILED→MITIGATED`, severe impact gone but degraded), recovery (`RECOVERING→RESTORED`, `restored_at` set). The jitter change (`failure-and-recovery--mechanism`) shortens contention, not detection; the pilot rehearsal (`--recent`) shortens error handling, not mitigation. Publish `TTD = detected_at − first_failed`, `TTM = mitigated_at − first_failed`, `TTR = restored_at − first_failed` separately — narrow enough that variance per step is smaller than end-to-end.

3. **If you must publish a single number, test it the paper's way before you believe it.** §"Put Your Chosen Metrics to the Test" (p. 33): determine what change is meaningful (e.g. 10% or 15 min), run the parallel-universes simulation on your empirical durations, and require that the CI excludes zero at the chosen α (paper uses α=0.10 → z≈1.644). The replacement for a headline is therefore not another headline but a *report*: mean, median, p95, geometric mean, plus the 90% CI of the difference and the call "insufficient N to detect 10%." Large-N hardware-drive-style aggregation (p. 31, tens of thousands, low variance) is the named exception that does not apply here; dramatic cuts (≈80%) are the other — neither is the plant's regime.

**Limits kept.** Median/p95/geometric mean also fail at practical N (Tables 3–5); the review does not propose p95 as the fix. Incident counting is erratic and confounded (Fig. 8, Branson citation) — not proposed as the reliability score. The paper does not analyse availability/SLI aggregates; this review reuses `SLI burned` only as the "direct reliability indicator" alternative § suggests, without claiming a CI for it. Counts stay in the dashboard as volume, never as the recovery score.

## Application

target: src/mishe_tauftauf/recovery_policy.py

**Publish the step durations and the distribution-aware report beside `transition`/`capacity`/`compare` — additive, pure functions, same `(value, reason)` vocabulary, no new branch.**

1. **Add `recovery_durations(rows)` and `mttx_report(durations, *, alpha=0.10)` beside `transition` (:8) and `capacity` (:58) in `src/mishe_tauftauf/recovery_policy.py`.** `recovery_durations` maps a sequence of `transition` rows (each with `first_failed`, `restored_at`, `mitigated_at`/`passing_since`, `checked_at`) to three interval samples `ttd/ttm/ttr` per completed incident (`restored_at − first_failed` etc., `None` while still `FAILED/RECOVERING`). `mttx_report` is the paper's table: for each sample it returns `{n, mean, median, p95, geometric_mean, std, min, max, ci90_diff_vs_baseline, sufficient_n_for_10pct}` computed by the same Monte Carlo the paper uses (resample with replacement 10,000× or closed-form `±z·σ·√(2/N)` when Q-Q normal; Q-Q check via `statistics.NormalDist` or a small-sample fallback to simulation). No I/O, no new dependency (`statistics`, `math`, `random` only). The headline `MTTR` is not added; the report is.

2. **Carry the row that proves the number is not trusted.** `mttx_report` returns the 90% CI for the *difference* of two samples (current window vs baseline window), not just one mean — the paper's Fig. 6 test. When `n < 30` or CI straddles zero, the report's `reason` field says `insufficient N to detect 10% at α=0.10; CI [−X; +Y] contains 10%` rather than publishing a green delta. This reuses `transition`'s `UNKNOWN` / `compare`'s `causation unproven` vocabulary instead of inventing a new verdict word.

3. **Let `recovery_io.evaluate` and the readiness gate consume it without changing thresholds.** `recovery_io.evaluate` (:419) already compares before/after pressure; after the change it also calls `recovery_durations`+`mttx_report` over the `recovery/` incident rows and attaches `mttx: {ttd, ttm, ttr}` to its artifact — the probes, `deadline`, `stable_seconds`, and `BLOCKED_STATE` thresholds stay where they are. The wall/headline never shows `MTTR` alone; it shows `ttr n=7 mean=43m median=18m p95=2h10m ci90 ±1h44m` so the reader sees the variance the paper warns about.

4. **Keep the existing compare warning.** `compare` (:80) keeps `causation: uncontrolled before/after; attribution unproven` — the report's CI is a *necessary* check for a claim, not a sufficient proof of it. User-study / narrow-step measurement (§"Tailor", p. 32) remains the stronger evidence for a specific change (jitter, breaker, phantom rehearsal).

**Acceptance (deterministic, runnable from this repository):**

- `python -m py_compile src/mishe_tauftauf/recovery_policy.py` and `python tools/check_parsing.py` still exit 0 — `recovery_policy.py` is not a `parsing-policy.json` boundary file and the new symbols are additive with no change to `transition`/`capacity`/`compare`/`breaker_transition` signatures.
- `recovery_durations` on a synthetic sequence of 5 `transition` rows ending in `RESTORED` returns `ttr` with `n=5`, durations matching `restored_at − first_failed` per row; an open `FAILED` row contributes no duration and the report says `n=0 reason: no completed incidents` (UNKNOWN, not zero).
- `mttx_report` on synthetic `ttr = [5m,6m,4m,120m,7m]` (skewed like Fig. 2) reports `mean ≈28m median=6m p95≈120m geometric_mean≈10m` and `ci90` width > mean; on the paper's Company A scale at N₁+N₂=100 the computed `±1h44m` reproduces Table 2 within 10% (simulation vs closed-form both accepted).
- With `n=3` (plant-typical per-role year) and a 10% shorter synthetic sample, `mttx_report` returns `sufficient_n_for_10pct=False` and `ci90` straddling zero — the 38% false-negative / 19% false-positive behaviour of Figs. 5–6 is reproduced rather than hidden. Existing `tests/test_recovery*.py`-equivalent still pass — the only observable difference is the artifact's extra `mttx` field.

**Ownership.** `src/mishe_tauftauf/recovery_policy.py` is the pure-function recovery discipline (`transition`/`capacity`/`compare`/`breaker_transition`) consumed by `recovery_io` and by `native_recovery`'s `attempt_disposition` loop; `health` is the resolver for `failure-and-recovery / native recovery, health, supervisors, watchdogs` and for substrate `UNKNOWN` delivery gaps that a misleading MTTR headline would hide. The incident rows live in `SITE/recovery/` beside `recovery_io`'s probe outputs.

**One thing this review does not ask for.** It does not change queue discipline, commit policy, retry count, or breaker thresholds to minimise mean duration, and does not add an availability/SLI aggregation or a hardware-drive-scale sample — those are optimisation and scale the paper names as next work once the measurement is honest. It only publishes the honest measurement the optimisation would need, in the module that already defines what "recovered" means.
