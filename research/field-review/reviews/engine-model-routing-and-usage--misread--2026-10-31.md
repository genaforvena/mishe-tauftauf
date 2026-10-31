---
field: engine-model-routing-and-usage
angle: misread
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/engine_policy.py
---

# "Hysteresis" in engine_policy is a sticky route with a timer that gates nothing

## What we read

- Hespanha & Morse, "Stability of Switched Systems with Average Dwell-Time", Proc. 38th IEEE Conference on Decision and Control, Dec. 1999. Primary text read as the author-hosted PDF, https://web.ece.ucsb.edu/~hespanha/published/avedwell.pdf (the CDC 1999 header is in the file; the text was extracted with `pdftotext -layout`).
- Points used: the two switching concepts are separate. A fixed or average dwell-time bounds how often switching happens (§2, definitions of S[τD] and S_ave[τD, N0]); hysteresis is a comparative rule. In the scale-independent hysteresis logic the supervisor takes q = argmin_p π_p(t) and switches to q only when (1+h)·π_q ≤ π_σ, with h the hysteresis constant (§5, Figure 3 and the text following it). The paper's point is that this margin, not a timer, is what is analysed, and that hysteresis-switching gives slow-on-average switching whose stability bounds come from a chatter bound N0 and an average dwell-time τD = log(1+h)/(2λm) (eq. 24 and the N0 expression after it). The text also states that prior hysteresis analyses relied on switching stopping in finite time.
- Limit taken from the same text: the result is for a supervisor that compares performance signals with a margin, and the bounds are for exponentially stable subsystems under the stated uniformity assumptions. Nothing in it covers a discrete account-quota admission, so it does not license any particular threshold here.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py:32-36` (module docstring) says: "Hysteresis: while the current compatible route remains eligible, `stay`. A switch occurs only on hard failure (current becomes ineligible) or when the hold expires. The hold interval is `min_hold_ms` (default 600 000 ms)".
- The code at `engine_policy.py:401-448` implements a stay branch: if the current route's alias is `healthy`, it returns `stay` with `next_reconsider_ms = now + min_hold_ms`. The comment there says "stay: prefer current while eligible, irrespective of alternative headroom". No comparison with any alternative is made on the stay path.
- Measured, not inferred: calling `policy.decide` with current = go-a (remaining 0.30), go-b available at 0.80, `min_hold_ms=600000`, returns `stay` at now+1 s, now+600 s, now+700 s and now+10 000 000 s. The switch-on-expiry the docstring promises never happens in the pure function.
- `engine_watch.py:264` passes the current route read from disk into the same `decide` call, and `engine_watch.py:269-292` only uses `next_reconsider_ms` to compute the cache's `next_reconsider` value. Nothing in the watch switches on expiry either. The hold is a scheduling hint for when to look again, and the look again returns `stay` for a healthy route.
- `tests/test_engine_policy.py:253-265` (`test_hysteresis_no_churn_within_min_hold`) checks stay at +1 s only. There is no test for the hold-expiry case the docstring names, so the claim is unpinned.
- The switch on hard failure is covered by `test_switch_on_hard_failure_current_becomes_ineligible`.

## What is new here

The name, not the behaviour, is what we misread. The plant calls its stay rule "hysteresis" and its `min_hold_ms` a hold, and the docstring reads as if the hold were a dwell-time that eventually permits a switch. Against the primary text the two things are different:

1. The plant has no comparative margin at all. A healthy current route is kept "irrespective of alternative headroom", which is the opposite of the (1+h)·π_q ≤ π_σ test that makes hysteresis a margin. So the stay rule is stickiness, not hysteresis.
2. The plant's `min_hold_ms` is a reconsideration timestamp, not a dwell gate. Hespanha & Morse's dwell-time is a bound on switching frequency. Here the hold bounds nothing, because nothing switches on it.

The concrete consequence is a behavioural gap between the docstring and the code: a route that fell back to `go-b` after a hard failure on `go-a` never returns to the preferred `go-a` after `go-a` recovers, because `go-b` stays healthy and the stay branch never compares. The docstring says the hold expiry is a switch point. The code says it is not. Which one is intended is the owner's call. I am not deciding it here.

## Application

target: src/mishe_tauftauf/engine_policy.py

Proposed to genome, as the owner of the engine path. Two options, and I recommend the first because it is the smaller and honest change:

1. Make the docstring match the code. Replace lines 32-36 with: "Stickiness: while the current compatible route remains eligible it is kept (`stay`), regardless of alternative headroom. A switch occurs only on hard failure (the current route becomes ineligible). `min_hold_ms` (default 600 000 ms) sets `next_reconsider_ms`, the time at which the caller should ask again; it does not by itself permit a switch." Add a regression test next to `test_hysteresis_no_churn_within_min_hold` that asserts `stay` at `NOW + 10_000_000` with the same status, so a future change that makes expiry switch must do so on purpose.
2. Only if genome decides the preferred alias should be reclaimed after recovery: that is a policy change, not a docstring fix. It would need a stated rule (for example, switch back to a preferred-order alias when it is healthy and the current route is not the first preference), a test for the return path, and a live observation through `engine_watch` decisions. Hespanha & Morse's margin (1+h) would be one candidate for a return rule, but it would need its own justification in this plant's terms.

Deterministic checks for option 1: `python3 -m pytest tests/test_engine_policy.py -q` (existing tests plus the new expiry case). The probe above is the reproduction. Detection of the gap in production: the `engine_watch` cache records `decisions[role].decision` and `next_reconsider`; a sustained `stay` on a non-preferred alias across several expiries is the observable signature. This is a read-only count, not yet measured live.
