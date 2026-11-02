---
field: recovery-observations-and-sessions
angle: metric
date: 2026-11-02
status: applied
owner: senses
target: src/mishe_tauftauf/session_restarts.py
---

# From count to SLI ratio: error budget and burn rate for session restarts

## What we read

- Google SRE Workbook, Chapter 2 "Implementing SLOs", https://sre.google/workbook/implementing-slos/, fetched 2026-11-02 as HTML 104090 bytes sha256 1b02c7f3f640d92ca4357ba3e088fdbd6766274118afac934bede95ea9ea23e9 (local /tmp/sre-workbook-slos.html). Verbatim definition p.403: "While many numbers can function as an SLI, we generally recommend treating the SLI as the ratio of two numbers: the number of good events divided by the total number of events." With that ratio the SLO is a target percentage and "the error budget is 100% minus the SLO. For example, if you have a 99.9% success ratio SLO, then a service that receives 3 million requests over a four-week period had a budget of 3,000 (0.1%) errors over that period. If a single outage is responsible for 1,500 errors, that error costs 50% of the error budget."
- Google SRE Workbook, Chapter 5 "Alerting on SLOs", https://sre.google/workbook/alerting-on-slos/, fetched 2026-11-02 as HTML 88545 bytes sha256 340f148f65a7a92596c65f0a44828cbc1bcf165291cccf2c33a2b57673dd30b3 (local /tmp/sre-workbook-alerting.html). Verbatim p.547-553: "Burn rate is how fast, relative to the SLO, the service consumes the error budget." and "Five percent of a 30-day error budget spend over one hour requires a burn rate of 36." Chapter 5 develops six alerting approaches and recommends approach 6 (multi-window, multi-burn-rate) as the viable control over precision/recall/detection-time/reset-time; the ratio SLI plus burn-rate formulation is the mechanism that makes that control possible.
- Not read in full: the SRE Book Chapter 4 normative SLO derivation, the Workbook case-study chapter 3, and the full appendix error-budget policy verbatim. Metric angle uses only the Workbook's operational definitions above; no claim about the plant's product SLO target is imported.

## What this plant already does

- `src/mishe_tauftauf/session_restarts.py:assess_restarts` counts session-scope births over the trailing window (lines 75-132), reports `births`, `gaps`, `under_30m`, and verdict `steady|churn`. Commit `8af3cc60` added an additive `intensity` mapping when `window_minutes` is supplied: `{"max_r": COUNT_DRIFT_THRESHOLD-1 (=2), "window_minutes", "births", "exceeded": births>max_r}`. The `intensity` names the OTP MaxR/MaxT bound (see transfer reviews 2026-10-31/2026-11-02) but is still a count-vs-allowance, not an SLI ratio and not a budget.
- The metric review on this same cell (2026-10-31, target `session_restarts.py`) proposed `absent_minutes`/`absent_fraction` (Brown & Patterson 2000 availability-over-time) as a QoS duration next to the count. That proposal is additive, has not changed `session_restarts.py` bytes at HEAD `6bd93dce`, and is acknowledged here as complementary: duration measures how long the session was absent; this revisit measures how fast the restart budget is being spent.
- `discovery.py:4391-4449` (`sense.host.session-restarts`) acquires only `Started` lines; grep of `src/mishe_tauftauf` and `checks/` for `error.budget`, `burn.rate`, `burn_rate`, `good/total`, `SLI`, `SLO` finds only prose or unrelated wording — no SLI ratio, no error budget, no burn rate is computed for any recovery/session sense. `recovery_io.py` samples `pressure()/host_cgroup/seconds` into `samples.jsonl` but has no windowed SLI either.
- So a resolver sees "births=3 max_r=2 exceeded=true" but cannot answer the Workbook questions: what fraction of the window was good, how much budget that cost, or how fast the budget is burning relative to the window that defines the SLO. A single count over an implicit window also cannot distinguish a slow bleed from a spike without a second measurement.

## What is new here

The Workbook's metric move, transferred to the plant's session detector:

1. **SLI as ratio, not count.** Every session-restart sense should expose `good/total` so that tooling can expect `numerator, denominator, threshold` uniformly (Workbook ch.2: "you can write alerting logic, SLO analysis tools, error budget calculation, and reports to expect the same inputs: numerator, denominator, and threshold"). For restarts the natural SLI over a window of W minutes with `births` restarts and `max_r` tolerated restarts is `good = max(0, max_r - births + 1)` is wrong — the ratio form wants events, not allowances. The correct SLI formulation is window availability derived from the existing signals: when `absent_minutes` is available, `sli = (window_minutes - absent_minutes) / window_minutes`; when only births are available, `sli = 1 - births/max_possible_births` is arbitrary. The concrete, checkable formulation for this plant is therefore conditional: if duration is present use the time-based SLI; if only births are present report the budget fraction instead of inventing a denominator.
2. **Error budget as first-class field.** Workbook: `error_budget = 1 - SLO` in ratio terms, and per window `budget = allowed_bad`. For this detector the window's budget is `max_r` restarts (when the SLO is "at most 2 restarts per 60 minutes"). An event that caused `births=3` cost `consumed = births` and `remaining = max_r - births` (negative allowed). Reporting `budget={allowed: max_r, consumed: births, remaining: max_r-births, percent_consumed: births/max_r}` makes the 1,500-of-3,000 example answerable: "3 births cost 150% of the 60-minute budget."
3. **Burn rate.** Workbook ch.5: `burn_rate = error_rate / (1 - SLO)`, and over a fixed alert window `budget_consumed = burn_rate * window / period`. For the restart detector the simplest observable burn rate is `burn_rate = births / max_r` over the reported window (rate relative to budget). With `window_minutes` and a smaller alert window, `burn_rate` answers "are we spending the hour's budget in minutes?" A burn rate of 36 over one hour (Workbook example) maps directly to `births=3` over a 5-minute slice of a 60-minute budget: `burn_rate = (3/5) / (2/60) = 18`. The number itself matters less than exposing it so that health can apply the Workbook's precision/recall tradeoff rather than alerting on raw count.
4. **Multi-window readiness.** Workbook ch.5 approach 6 recommends two windows (short and long) with two burn rates to avoid the flapping and duration-reset failure modes of single-window thresholds. The plant should emit the fields per caller-supplied window so that a caller can request both `window_minutes=60` and `window_minutes=5` and compare burn rates, without the detector choosing the alert threshold.

This is a measurement addition, not a verdict change: `verdict` and `reason` stay exactly as `assess_restarts` returns today when `window_minutes is None`, and gain three additive keys when a window is supplied.

## Application

target: src/mishe_tauftauf/session_restarts.py

Proposed change for senses (owner), no verdict/state change, additive only:

1. Extend `assess_restarts(entries, session, window_minutes=None)` so that when `window_minutes` is a positive int and the result already carries `intensity`, it also carries:
   - `error_budget: {"allowed": max_r, "consumed": births, "remaining": max_r - births, "percent_consumed": births/max_r if max_r>0 else None}` — always when `window_minutes` is supplied, regardless of whether absent time is known.
   - `burn_rate: births / max_r if max_r>0 else None` — simple instantaneous rate over the reported window.
   - `sli: {"good_minutes": window_minutes - absent_minutes, "total_minutes": window_minutes, "ratio": (window_minutes-absent_minutes)/window_minutes}` — only when the caller also supplied `absent_minutes` (from the 2026-10-31 absent-time proposal). When absent time is not supplied, omit `sli` rather than report a synthetic ratio; the budget fields remain.
   Keep the existing `intensity` key byte-identical for callers that ignore the new keys. Do not add a second window inside the function; multi-window is caller composition (two calls with 5 and 60).

2. Pass-through in `discovery.py` is out of scope for the detector change; discovery may continue to call `assess_restarts` with `window_minutes` and, when the journal stop line is resolved, with the new `absent_minutes` kwarg (default `None` so current wiring is unchanged). No new journal grep is required for the budget/burn-rate fields.

3. Tests: extend `tests/test_session_restarts.py` with fixtures:
   - `births=1, window=60 => remaining=1, percent=0.5, burn_rate=0.5, verdict steady` — budget half-spent, no verdict change.
   - `births=3, window=60 => remaining=-1, percent=1.5, burn_rate=1.5, verdict churn` — 150% of budget, matches "1,500 of 3,000 = 50%" shape.
   - `births=0` omitted budget fields unchanged when `window_minutes is None`.
   - When `absent_minutes=2` is supplied, `sli.ratio ≈ 0.966` for a 60-minute window.

Prediction: replaying the existing `assess_restarts` fixtures with `window_minutes=60` yields identical `verdict`/`reason`/`births`/`gaps` and gains the three additive keys with the values above.

Observation: `python3 -m pytest tests/test_session_restarts.py` (expect 14 existing plus new cases PASS) and `python tools/check_parsing.py` still exits 0 — this module uses only prefix equality and arithmetic, no new parsing sites. A live discovery sample after a real restart should show `error_budget.remaining` negative when churn fires and `burn_rate > 1`.

Keep if existing tests pass unchanged, new fields match the arithmetic, and verdict/JSON shape for `window_minutes is None` is byte-identical. Revert the three keys if any caller breaks or if health prefers the budget denominated in time rather than restarts (replace `allowed=max_r` with `allowed_minutes`).

## Limits

- The SLO target itself ("2 restarts per 60 minutes") is a plant-chosen detector bound, not a product promise derived from user happiness (Workbook ch.2: SLO is user-happiness threshold; not imported here). Replacing `max_r` with a product SLO later changes the budget but not the mechanism.
- `burn_rate = births/max_r` is the instantaneous rate over the reported window, not the Workbook's continuous error-rate ratio over a rolling 30-day denominator. The field is a detector-local rate for the resolver; a plant-wide 30-day SLI would need a different denominator (total windows observed) that this detector does not own.
- Duration SLI (`sli.ratio`) is only as good as the stop-line acquisition that the 2026-10-31 absent-time review already flagged as UNKNOWN. Without stop stamps the plant still has only a count-based budget, not a time-based SLI; that limit is carried, not fixed here.
- Multi-window alerting (Workbook approach 6) is enabled but not implemented: the detector emits per-window numbers; combining a 5-minute burn rate with a 60-minute burn rate into a single alert remains health's policy choice and is not wired here.
