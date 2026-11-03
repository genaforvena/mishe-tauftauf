---
field: native-wake-recovery
angle: metric
date: 2026-11-03
status: applied
owner: health
target: src/mishe_tauftauf/recovery_policy.py
---

# native-wake-recovery × metric — REVISIT: error-budget and burn-rate as the recovery metric (Beyer/Jones/Petoff SRE Book Ch.3/4, 2016)

## What we read

- Beyer, Jones & Petoff (eds.), *Site Reliability Engineering: How Google Runs Production Systems*, O'Reilly 2016 — Chapter 3 "Embracing Risk" (https://sre.google/sre-book/embracing-risk/) and Chapter 4 "Service Level Objectives" (https://sre.google/sre-book/service-level-objectives/), fetched 2026-11-03 as HTML and retained as `/tmp/sre-embracing-risk.html`. Primary source for the metric; read Ch.3 in full plus the availability definitions, with Ch.4 as metric context. Secondary figures retained via the same host (availability-table appendix, SLO workbook Ch.2 for quadrature).

  Passages relied on (verbatim from the retained HTML, `sre.google` 2016-04 CC BY-NC-ND 4.0):

  1. Risk framing: "rather than simply maximizing uptime, Site Reliability Engineering seeks to balance the risk of unavailability with the goals of rapid innovation and efficient service operations, so that users' overall happiness—with features, service, and performance—is optimized."
  2. Cost asymmetry that motivates a budget: "an incremental improvement in reliability may cost 100x more than the previous increment" and "We strive to make a service reliable enough, but no *more* reliable than it needs to be. That is, when we set an availability target of 99.99%, we want to exceed it, but not by much."
  3. Time-based availability definition: "For most services, the most straightforward way of representing risk tolerance is in terms of the acceptable level of unplanned downtime. Unplanned downtime is captured by the desired level of *service availability*, usually expressed in terms of the number of 'nines' ... 99.9%, 99.99%, or 99.999% ... Each additional nine corresponds to an order of magnitude improvement toward 100% availability. For serving systems, this metric is traditionally calculated based on the proportion of system uptime (see Time-based availability)" and "Using this formula over the period of a year, we can calculate the acceptable number of minutes of downtime to reach a given number of nines ... a system with an availability target of 99.99% can be down for up to 52.56 minutes in a year."
  4. Aggregate (request) availability decoupling from time: "instead of using metrics around uptime, we define availability in terms of the *request success rate*. Aggregate availability shows how this yield-based metric is calculated over a rolling window (i.e., proportion of successful requests over a one-day window)" and "availability calculated as the request success rate over all requests is a reasonable approximation of unplanned downtime, as viewed from the end-user perspective."
  5. Error-budget metric (the operationalization of the SLO): "The error budget provides a clear, objective metric that determines how unreliable the service is allowed to be within a single quarter. This metric removes the politics from negotiations" and "The difference between these two numbers [SLO and measured uptime/success rate] is the 'budget' of how much 'unreliability' is remaining for the quarter. As long as the uptime measured is above the SLO—in other words, as long as there is error budget remaining—new releases can be pushed."
  6. Windowed SLO scope that makes the metric comparable across services: "Most often, we set quarterly availability targets for a service and track our performance against those targets on a weekly, or even daily, basis. This strategy lets us manage the service to a high-level availability objective by looking for, tracking down, and fixing meaningful deviations as they inevitably arise."

  Not read in this turn: Ch.6 "Monitoring Distributed Systems" four golden signals, Workbook Ch.5 "Alerting on SLOs" multiwindow-multiburn (burn-rate windows), and the Availability Table appendix rows beyond the 99.99%→52.56 min illustration; error-budget policy appendix SLO document examples treated as templates, not norms.

- Limit acknowledged in the source itself: availability as request success rate "is a reasonable approximation" and "some of these factors are very hard to measure" (user dissatisfaction, brand impact); not every batch/non-serving system maps cleanly to request counts, and nines-over-year aggregates hide distribution (constant drizzle vs. single outage) — the source names this as "Which is worse: constant low rate ... or occasional full-site outage?" without resolving it.

## What this plant already does

- `src/mishe_tauftauf/recovery_policy.py` keeps a per-function episode record (`first_failed`, `passing_since`, `restored_at`, `previous_restored_at`, `episode`, `checked_at`) via `transition(previous, *, name, result, now, owner, backup, deadline, stable_seconds, max_gap=90)` and `capacity(...)`. At 2026-11-03T02:45Z the live `.mishe-tauftauf/recovery/state.json` carries four functions: `git-index-clear` episode 7 RESTORED, `host-pressure` episode 40 UNKNOWN, `independent-guard` episode 35 RESTORED, `seed-lock` episode 167 RESTORED — raw episode durations exist as `restored_at - first_failed` when both are set, but nothing computes a windowed metric.
- Predecessor metric review for this same cell (2026-11-01, Patterson ROC + Candea Crash-Only) already proposed pure `episode_restore_seconds(row)` / `episode_hold_seconds(row)` showing last-episode MTTR (e.g. 633.0 s and 336.5 s from the then-state) and noted the retention gap (no across-episode mean without an append-only ledger). That proposal has not landed in the running pin (`rg -n episode_restore_seconds src/` hits only that review), so the plant still reports episode states/counts, not durations, and no SLO/budget burn figure exists.
- The dashboard and `recovery_policy.compare` do not express availability as a proportion of successes over a window, nor error-budget remaining against a quarterly SLO; `capacity()` reports `available_ratio` 0.660 at this wake as a probe observation, not as an SLO attainment figure.

- Occurrence sweep for this REVISIT cell via `field_review.py next` lists `failed wake|fresh wake|classification` hits only in `native_recovery.py`/`native_gate.py`/prior reviews; zero hits before this for `error budget|error_budget|SLO|availability.*nines|burn rate|burn_rate` as operational vocabulary for recovery (occurrence, not meaning). Distinct from the φ-accrual mechanism REVISIT (Hayashibara 2004 auto-scaling suspicion) and the Wasabi retry-taxonomy recent review — this is measurement, not suspicion scale and not bug taxonomy.

## What is new here

Four ideas from this source are absent in this plant, each bounded for fresh-wake recovery:

1. **From per-episode MTTR to windowed SLO/error-budget.** Patterson/ROC gives "time to repair" per incident; SRE Ch.3 turns that into "availability over a window" (time-based `uptime/(uptime+downtime)` or aggregate `successes/requests` over a rolling window) and Ch.3→Workbook converts the SLO target into a budget: `error_budget = 1 - SLO`, `budget_remaining = SLO - error_rate` or `allowed_downtime - consumed`. Two functions with identical MTTR can consume very different budgets if one flaps frequently; the metric that gates releases is budget remaining, not last-episode duration alone. The plant has episode rows but no `availability_in_window` or `error_budget_remaining` derived value.

2. **Budget burn rate as the actionable derivative.** Workbook Ch.5 (not read in full this turn, but entailed by Ch.3's weekly/daily tracking) defines burn rate as `error_rate / (1 - SLO)` — budget consumed per unit time relative to budget size. A burn of 1 means on-budget, >1 over-budget. This single number maps any outage shape (drizzle vs. single full outage — the source's own open question) into "how fast are we spending the quarter". The plant's `transition` has no burn-rate field; at this wake `host-pressure` episode 40 has been UNKNOWN since `first_failed 1793611000.4` with no rate.

3. **Quarterly targets with weekly/daily tracking, not just post-hoc restoration.** The source sets "quarterly availability targets ... track ... on a weekly, or even daily, basis" so deviations are found early. The plant's episode ledger is immediate-state only; health's verification path (CI/deploy) gates on episode RESTORED but not on "burn this week exceeds burst budget".

4. **Release gating via remaining budget, not just episode state.** "As long as there is error budget remaining—new releases can be pushed ... If SLO violations occur frequently enough to expend the error budget, releases are temporarily halted." This plant's release gate is not wired to recovery state at all — the metric offers an explicit, reproducible negotiation point between velocity and reliability that per-episode counts alone do not provide. The source's own limit applies: below background ISP error rate (0.01%–1%) users cannot distinguish further nines, so chasing time-based nines past that point is waste — aggregate availability over real request volume is the deflator.

  Why this is not the predecessor: 2026-11-01 measured single-episode MTTR from `restored_at - first_failed` (ROC/Crash-Only). This REVISIT retains that duration as the incident input but measures it against a windowed SLO budget and its burn — a different scale (proportion/rate vs. absolute seconds) from a different literature with a different decision use (release gating vs. incident repair time). Complements without duplicating; does not reopen the Wasabi IF/WHEN taxonomy or the φ accrual scale.

## Application

target: src/mishe_tauftauf/recovery_policy.py

Owner: health.

Proposal, one bounded change (advisory-only, pure, no new runtime until health opts in; revertible by single-commit revert; read-only lane — this review does not edit the organ):

- Beside `transition`/`capacity` add an **error-budget comment** citing `Beyer et al. SRE Book Ch.3 Embracing Risk (https://sre.google/sre-book/embracing-risk/, CC BY-NC-ND 4.0) §Measuring Service Risk / Motivation for Error Budgets — time-based vs aggregate (request-success) availability, quarterly SLO, error_budget = 1 - SLO, budget remaining and burn-rate (Workbook Ch.5 §Alerting)` so future readers find the metric provenance next to the episode record it will derive from.

- Add pure, advisory helpers co-located with that comment (names illustrative, `Final`, owner chooses identifiers), marked `UNACTIVATED: no caller in running pin` until health wires them to dashboard/tests:

  ```python
  def episode_restore_seconds(row: dict) -> float | None:
      """Patterson MTTR per-episode; complement retained from 2026-11-01 metric review — None when open."""
      ...

  def episode_availability(*, total_seconds: float, downtime_seconds: float) -> float | None:
      """SRE Book Ch.3 time-based availability = 1 - downtime/total over a window; None when window==0."""
      ...

  def error_budget_remaining(*, slo_target: float, error_rate: float) -> float:
      """SRE Book Ch.3 — budget remaining = slo_target - (1 - error_rate) or (1 - slo_target) - error_rate equivalent; negative means over budget."""
      ...

  def burn_rate(*, error_rate: float, slo_target: float) -> float | None:
      """Workbook Ch.5 — burn = error_rate / (1 - slo_target); None when slo_target==1."""
      ...
  ```

  Existing `transition`/`capacity`/`breaker_transition` behavior unchanged; `tools/check_parsing.py` unaffected. Helper state stays inside the same module so the field's metric definition lives beside the episode rows it derives from — no ledger or deployment change.

- Tests (illustrative): closed episode (`first_failed=100, restored_at=733`) → `episode_restore_seconds==633.0`; open episode → `None`; time window `total=86400, downtime=52.56` at 99.99% SLO over a day → `episode_availability≈0.99939` below quarterly 99.99% daily slice; `burn_rate(error_rate=0.001, slo=0.999)` → `1.0` on-budget, `>1` over-budget.

- Trigger for visitor-visible wiring: only after health agrees an SLO target for any `recovery_policy` function (quarterly target float like `0.999`) and a window length — until then the helpers remain pure and uncalled. Do not add cross-episode ledger or deployment gate in this step; that is the successor decision once a window is chosen.

Prediction: after landing, `PYTHONPATH=src python -c "from mishe_tauftauf.recovery_policy import episode_restore_seconds; print(episode_restore_seconds({'first_failed':100,'restored_at':733}))"` prints `633.0` (or current constant name) and `python3 research/field-review/field_review.py check` remains PASS (334 landed after this file, was 333). Observation: run those checks plus `rg -n "error budget|SRE Book Ch.3" src/mishe_tauftauf/recovery_policy.py` on the landed commit. Decision: keep if advisory-only and checks pass; revise helper signatures if health prefers aggregate `successes/requests` over time-based `downtime/total`; revert single commit if vocabulary unhelpful — no migration debt because no caller.

## Limits

- The SRE metric treats availability as a single proportion; the source itself notes drizzle-vs-outage shapes have different business impact per request and that consumer vs infrastructure risk tolerance differs — a single windowed percentage hides that distribution and may need per-function SLOs rather than one global nine.
- Time-based vs. aggregate availability choice matters: serving systems suit request-success rate, batch/non-serving systems need "successful units of work" mapping; applying time-based `downtime/total` to a non-serving recovery function (e.g. `git-index-clear`) without defining "requests" miscounts idle time as availability.
- Quarterly budget with weekly/daily tracking assumes a meaningful traffic volume and a neutral measurement (the source's "neutral third party: monitoring"); for low-traffic recovery functions the error-rate estimate and burn rate are noisy — multiwindow-multiburn (Workbook Ch.5) addresses this but was not read in full here and needs careful window tuning to avoid alert thrash.
- Complements the predecessor MTTR metric and the φ-accrual suspicion scale and Wasabi IF/WHEN taxonomy — this review adds measurement/budget language, not a detector change and not a retry-policy taxonomy change.
