---
field: engine-policy-route-decision
angle: transfer
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/engine_policy.py
---

# engine-policy-route-decision × transfer — hold-as-stay for an unconfirmed current route

## What we read

**3GPP TS 36.331 (LTE RRC), clause 5.5.4.1 "Measurement report triggering" and clause 5.5.4.4 "Event A3 (neighbour becomes amount of offset better than PCell)"**, read through the clause mirror at <https://www.itecspec.com/3gpp/36.331/s/5.5.4.1> and <https://www.itecspec.com/3gpp/36.331/s/5.5.4.4>. The mirror is a copy of the 3GPP text. I did not open the 3GPP archive or its PDF, and the A3 inequalities are images on the mirror page, so I read them only as far as the text gives them. Passages used:

1. Clause 5.5.4.1, entering condition: the condition "is fulfilled for one or more applicable cells for all measurements after layer 3 filtering taken during timeToTrigger". For the other events the wording is "is fulfilled during timeToTrigger defined within the VarMeasConfig for this event". The event triggers only when the condition holds for the whole time-to-trigger window, not at one sample.
2. Clause 5.5.4.1, leaving condition: the same "for all measurements … taken during timeToTrigger" wording, so the report is withdrawn when the condition stops holding across that window.
3. Clause 5.5.4.4: the parameters are `Hys` ("the hysteresis parameter for this event") and `Off` ("the offset parameter for this event"). The entering condition is a margin of `Hys` and `Off` on the neighbour, in dB. Hysteresis is a separate band on the entry side, not the same threshold as the exit side.

Limits: the mirror's fragments do not include the exact sentence "fulfilled for a time duration", and I did not check the inequality text. The reading above is the quoted wording only.

## What this plant already does

`src/mishe_tauftauf/engine_policy.py` decides a route for each role from a pure function, `decide()`. Its docstring (lines 32–36) states the intent:

> Hysteresis: while the current compatible route remains eligible, ``stay``. A switch occurs only on hard failure (current becomes ineligible) or when the hold expires.

Three things in the code do not match that intent. I checked each one by reading the code and by running the pure function, not from the docstring.

- The `stay` branch (lines 438–449) is the only use of the hold. `min_hold_ms` is written into `next_reconsider_ms` and never checked on the switch path. The grep for `min_hold` in `src/` finds no comparison against elapsed time.
- `_classify_account` maps `UNKNOWN`, `STALE`, `UNATTRIBUTED` and `NOT_CONFIGURED` to health `hold` (lines 290–296, the `_INELIGIBLE_STATES` branch). The docstring says these "are not evidence of exhaustion" and that the decision "is `hold`". But `decide()` treats any health other than `healthy` on the current route as `current_healthy = False` (lines 416–425), so an unconfirmed read counts as a hard failure. The next branch selects a healthy alternative. The comment at about line 487, "unknown does not mean exhausted … decision is hold", is only reached when no alternative exists.
- `engine_watch._maybe_propose` (src/mishe_tauftauf/engine_watch.py:197–218) writes a proposal on every tick where the decision is `select` and the selection differs from the current route. It does not check the hold either. The caller runs `decide()` every tick and does not schedule on `next_reconsider_ms`.

The existing test `test_hysteresis_no_churn_within_min_hold` (tests/test_engine_policy.py:253) only checks that a healthy current route stays. No test covers a switch during the hold window.

## What is new here

The 36.331 mechanism gives the plant two separate things that `decide()` currently merges. Event triggering uses a persistence requirement, the time-to-trigger, so one sample cannot move a connection. It also uses a hysteresis band, so the entry and exit thresholds are not the same number. `decide()` has neither. Its only threshold for the current route is `reserve_pct` (0.15) applied to `remaining − reserved`. Its only signal for an unconfirmed read is the same `hold` as a confirmed failure.

I ran the pure function with `min_hold_ms=600000` and the current route `go-a` (probe script `.mishe-tauftauf/artifacts/field-review-engine-hold-probe/hold_probe.py`, output beside it in `output.txt`, run with `python3 -I`; no repository state changed):

- t+0 s, both accounts FRESH and healthy → `stay` on go-a.
- t+1 s, go-a reads STALE once → `select` go-b, inside the hold.
- t+2 s, go-a FRESH again → `stay` on go-b. The switch is kept.
- Alternating one stale read per account, 1 s apart: `stay` go-a at 3 s, then `select` go-b at 4 s, then `select` go-a at 5 s. That is two switches in two seconds, with `next_reconsider_ms` reporting a 600 s hold each time.

This is a property of the code path, not a live measurement. Live proposal records are absent from the site (`.mishe-tauftauf/engine/proposals` does not exist), and `usage-errors.jsonl` holds one record from 2026-10-23, so the live rate of stale reads on a current route is unmeasured. The earlier critique discard (`engine-policy-route-decision--critique--2026-11-01.md`) named its re-open trigger as "two or more switches for one role inside one min_hold_ms window". That trigger is now reachable from one stale read, but it is not shown to fire in live operation.

## Application

target: src/mishe_tauftauf/engine_policy.py

Proposed change for genome (the organ owner). field-review has not edited the file.

1. **Hold is stay, not switch.** In `decide()`, when the current route's health is `hold` (UNKNOWN, STALE, UNATTRIBUTED, NOT_CONFIGURED), return `stay` with the reason "current {alias} unconfirmed: {reason}" and `next_reconsider_ms = now_ms + min_hold_ms`. Keep `depleted` and `reserve` as the only signals that can move a fresh current route. This matches the existing docstring and the existing comment about unknown not meaning exhausted. A stale read cannot confirm exhaustion, because depletion is classified only from FRESH data.
2. **Persistence before a fresh switch (second step, not in this change).** If a FRESH current route is `reserve` or `depleted`, a switch still happens at once, as today. Once live data shows that reserve or depleted readings flip, add a `current_unhealthy_since_ms` input and switch only after it has held for `min_hold_ms`. That is the time-to-trigger part, and it needs caller state, so `decide()` stays pure only if the caller passes the timestamp.
3. **Tests.** Add the probe's three-step case and the alternating case to tests/test_engine_policy.py. Assert that no `select` happens inside the hold when the only change is a STALE or UNKNOWN read on the current route. Keep `test_switch_on_hard_failure_current_becomes_ineligible` as it is, since depletion still switches.

Limit of the change: if the current account is really exhausted and the status reads STALE, the route stays until a fresh read arrives. The provider's own error on the in-flight call is then the only signal, and that path is outside `decide()`. This trade is the one the existing docstring already claims to make.

Observation gap for genome: `engine_watch` writes no proposal file on this host now. The live rate of `select` decisions and the rate of STALE reads on a current route are unmeasured. Before step 2 is applied, measure both from the watch cache over a week.
