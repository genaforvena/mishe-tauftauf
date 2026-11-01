---
field: engine-policy-route-decision
angle: mechanism
date: 2026-11-01
status: discarded
owner: field-review
---

# engine-policy-route-decision × mechanism — discarded

## What we read

- Beyer, Jones, Petoff, Murphy (eds.), "Site Reliability Engineering", chapter "Handling Overload" (Google, 2016), section "Client-Side Throttling", https://sre.google/sre-book/handling-overload/ (fetched 2026-11-01).
- Limit of this reading: the fetch returned a summary. The rejection-probability formula is an image on the page and was not extracted, so no formula is quoted here. The points used are the ones the chapter states in text:
  - each client tracks `requests` (attempted, including locally rejected) and `accepts` (accepted by the backend) over a two-minute window, and rejects locally past a multiple K of accepts (K = 2 in the example, "We generally prefer the 2x multiplier");
  - the chapter states it "works less well for clients that send requests only sporadically", because each client's view of backend state becomes sparse;
  - it is a poor fit "when rejecting costs nearly as much as processing"; K should then be lowered.
- Retry budgets are a separate section and were not used.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` `decide()` takes no accept or reject signal. Its inputs are the dashboard `status` from `engine_usage` (per-window `remaining_fraction`), `reservations`, `reserve_pct` (default 0.15) and `min_hold_ms` (default 600 000). The reserve threshold is a static fraction, not a ratio of observed attempts to observed successes.
- Admission is already a local gate. A window below `reserve_pct` is `reserve` and is ineligible under `auto`; a depleted window queues with `binding_reset`. This is the same job the throttle does, done from quota state the plant reads directly, not inferred from a backend's reject rate.
- Stale, unknown and unattributed windows return `hold`, which is the plant's version of not acting on an unreliable local view.
- `src/mishe_tauftauf/engine_watch.py` (about line 264) calls `decide()` once per role with `reserve_pct=0.15`, `reserve_policy="auto"`, `min_hold_ms=600000`. The live tree has one role (`default`, see the misread review of the same cell) and no `engine/policy.json`.

## What is new here

The chapter's mechanism fits a population of clients that each see a shared backend's rejections and independently throttle to it. Mapped onto the plant it would need three things the plant does not have:

1. **Many clients on one backend.** The throttle works because every client sees the same backend's accept rate and each cuts its own load. `engine_policy` has one decision stream per role and no backend that returns accept or reject counts per call. Quota is a reported fraction, not a per-request verdict.
2. **Frequent calls.** The chapter says the scheme degrades for sporadic clients. Engine routing decides on a watch cache refreshed on a timer, and admissions for a role are sparse. Its `requests` and `accepts` counters would be near-empty over a two-minute window, which is the regime the chapter warns about.
3. **Rejections that are cheap.** The chapter's caveat is that when rejection costs nearly as much as processing, K must drop. Here a rejected admission costs a queued turn and a wait, not backend capacity. The caveat points the same way the mechanism's own precondition does: the throttle is for overloaded servers, and the plant's concern is quota exhaustion, which `reserve_pct` already gates.

So the mechanism's intuition (cut your own load when the shared resource signals distress) is already present as a static reserve gate on the signal the plant actually has. Replacing that gate with an adaptive ratio would add a counter the plant would have to record for one role, to reproduce a threshold it already has.

Latent case, not observed: if a second role or a shared provider pool later sends frequent calls to one backend that returns per-request accept or reject outcomes, the chapter's mechanism would apply and its K = 2 default would need to be reconsidered for quota-style rejects.

## Discard

No live precondition. The throttle needs many frequent clients reading one backend's accept and reject outcomes. The plant has one role, sparse admissions, and a quota fraction already gated by `reserve_pct`.

Re-open trigger: a second role or pool that makes frequent calls to one provider and records per-request accept or reject outcomes. Then measure the accept and reject counts over a window on the real path before any change to `decide()`.
