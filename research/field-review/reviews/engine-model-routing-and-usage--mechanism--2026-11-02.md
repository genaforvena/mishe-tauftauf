---
field: engine-model-routing-and-usage
angle: mechanism
date: 2026-11-02
status: applied
owner: genome
---

# Truncated exponential backoff with full/decorrelated jitter for depleted-route retries

## What we read

- Brooker, Marc, "Exponential Backoff And Jitter", AWS Architecture Blog, published 2015-03-04 10:48:56-08:00, modified 2026-10-22 16:04:34-07:00 (fetched 2026-11-02), https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/ -> /tmp/aws-backoff.html HTML 1437372 B sha256 9e4f9ad79fde23267f5adca8725f56632591712655267d081ecaea141dd7e390 (reader strip 7646 B plain-text after tag strip; figures 3,6,10,11 are images, formulas transcribed from blog's alt/caption and companion Builders' Library page). Update note (May 2023) inside: "After 8 years, this solution continues to serve as a pillar for how Amazon builds remote client libraries for resilient systems. Most AWS SDKs now support exponential backoff and jitter as part of their retry behavior when using standard or adaptive modes."
- Builders' Library companion cited inside: "Timeouts, Retries and Backoff with Jitter" https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/ (not separately fetched; blog is primary).
- Simulator code referenced: https://github.com/awslabs/aws-arch-backoff-simulator (OCC simulation with mean network delay 10 ms, variance 4 ms, N contending clients).

Verbatim mechanism (headings and quotes as printed, figures' formulas as captioned):

- Framing (OCC): "Optimistic concurrency control (OCC) is a time-honored way for multiple writers to safely modify a single object without losing writes. OCC has three nice properties: it will always make progress as long as the underlying store is available, it's easy to understand, and it's easy to implement." Contention model: "With one client guaranteed to succeed every round, the time to complete all the updates grows linearly with contention." / "With N clients contending, the total amount of work done by the system increases with N2."
- Adding Backoff: "The classic way to slow clients down is capped exponential backoff. Capped exponential backoff means that clients multiply their backoff by a constant after each attempt, up to some maximum value. In our case, after each unsuccessful attempt, clients sleep for:" [Figure 3: `sleep = min(cap, base * 2^attempt)`]. Observation after: "backoff helps a small amount, but doesn't solve the problem. Client work has only been reduced slightly." "there are still clusters of calls. Instead of reducing the number of clients competing in every round, we've just introduced times when no client is competing."
- Adding Jitter: "The solution isn't to remove backoff. It's to add jitter. Initially, jitter may appear to be a counter-intuitive idea: trying to improve the performance of a system by adding randomness." / "we want to spread out the spikes to an approximately constant rate. Adding jitter is a small change to the sleep function:" [Figure 6: `temp = min(cap, base * 2^attempt)`; `sleep = random_between(0, temp)` labelled Full Jitter; Figure 7 time series]. Result: "That time series looks a whole lot better. The gaps are gone, and beyond the initial spike, there's an approximately constant rate of calls." / "In the case with 100 contending clients, we've reduced our call count by more than half. We've also significantly improved the time to completion, when compared to un-jittered exponential backoff."
- Variants (named as printed):
  - Full Jitter (Figure 6): `sleep = random_between(0, min(cap, base * 2^attempt))`
  - Equal Jitter (Figure 10): "where we always keep some of the backoff and jitter by a smaller amount" -> `sleep = min(cap, base * 2^attempt)/2 + random_between(0, min(cap, base * 2^attempt)/2)`
  - Decorrelated Jitter (Figure 11): "similar to Full Jitter, but we also increase the maximum jitter based on the last random value." -> `sleep = min(cap, random_between(base, sleep_prev * 3))` (blog caption; Builders' Library gives `sleep = min(cap, rand_between(base, sleep*3))` with base as floor).
- Comparison (Figures 12-13): "The no-jitter exponential backoff approach is the clear loser. It not only takes more work, but also takes more time than the jittered approaches." / "Of the jittered approaches, Equal Jitter is the loser. It does slightly more work than Full Jitter, and takes much longer. The decision between Decorrelated Jitter and Full Jitter is less clear. The Full Jitter approach uses less work, but slightly more time." / "It's worth noting that none of these approaches fundamentally change the N2 nature of the work to be done, but do substantially reduce work at reasonable levels of contention. The return on implementation complexity of using jittered backoff is huge, and it should be considered a standard approach for remote clients."

Limits stated in source: backoff does not change N2 asymptotics; gains are at reasonable contention; base/cap values must be chosen per client/backend; network delay variance already spreads calls slightly even without jitter.

## What this plant already does

`src/mishe_tauftauf/engine_policy.py:338-558` `decide()` is a pure per-wake classifier from provider-reported state: `_classify_account` on `remaining_fraction - _effective_reserved` -> healthy/reserve/depleted/hold, provider allowlist check, and a fixed `next_reconsider_ms = now_ms + 600_000` (10 min) for depleted/hold reconvergence. No attempt count, no exponential growth, no cap, no per-alias backoff persistence across wakes.

`src/mishe_tauftauf/engine_watch.py:27-29,355-368` `serve()` polls with a fixed interval plus uniform jitter only: `DEFAULT_INTERVAL=60`, `JITTER=10`, `delta = interval + random.uniform(-jitter, jitter)`. This is `±10 s` around a constant, not `base * 2^attempt` capped. No correlation with prior sleep, no attempt-indexed growth, no distinction between healthy and depleted retry cadence. Proposal re-emission in `_maybe_propose` is gated by generation check, not by backoff.

`src/mishe_tauftauf/engine_rotation.py` persists the last stored route for idempotent retry but does not back off retries after a failed activation; it leaves state unchanged so the next wake re-evaluates at the same fixed cadence.

The OCC contention model maps to this plant only loosely: multiple wakes (or multiple roles) retrying a depleted provider at the same fixed 600 s boundary recreate the clustered-spikes figure the blog warns about, even though the provider quota fraction itself is authoritative. The plant's current fixed reconsider creates synchronized thundering-herd reconsiderations; the blog's jitter is precisely the remedy for that clustering, independent of any attempts/accepts ratio.

## What is new here

The blog's mechanism is distinct from the predecessor's SRE adaptive throttling and from the plant's current jitter:

1. Attempt-indexed exponential vs fixed interval. The blog multiplies sleep by 2 each failure up to cap (`min(cap, base * 2^attempt)`); the plant uses constant 60 s +/-10 s and constant 600 s reconsider, so repeated failures do not slow retries and a quota-healthy-but-erroring alias is retried at the same cadence as a healthy one, preserving the N2 work the blog measures.

2. Full vs equal vs decorrelated jitter variants, each with a named trade-off. The plant has only one uniform jitter shape (symmetric around interval). The blog shows equal jitter "does slightly more work and takes much longer" than full jitter, while decorrelated jitter (sleep-dependent randomization with floor `base`) trades slightly more work for slightly less time than full jitter — a choice that depends on whether the cost of an extra retry (hurting provider quota) outweighs wall-clock delay.

3. Jitter as de-clustering, not as noise. Without jitter, "we've just introduced times when no client is competing" — clusters remain; adding `random_between(0, temp)` spreads the spike to "an approximately constant rate" and for 100 clients "reduced our call count by more than half." The plant's fixed 600 s reconsider for depleted aliases recreates the no-jitter clustered figure when multiple wakes bootstrap together.

This is disjoint from the 2026-10-31 mechanism predecessor (Beyer et al., SRE Book ch.21 "Handling Overload", client-side adaptive throttling via `attempts` vs `accepts` over last two minutes, `requests is K times as large as accepts`, K=2, failure case "sporadically send requests"): that mechanism needs steady traffic to estimate accepts ratio and was discarded because wake-driven traffic is sporadic per the chapter's own warning. This mechanism needs only an attempt counter persisted across wakes (e.g., `engine/watch-cache.json` or `engine/proposals` generation), not a traffic rate, and the blog explicitly calls sporadic/bursty OCC the motivating case.

## Application

target: src/mishe_tauftauf/engine_watch.py

Additive header note (no behavior change) citing Brooker 2015 AWS Architecture Blog HTML 1437372 B sha256 9e4f9ad7... (figures 3/6/10/11) and companion Builders' Library page, carrying three gated claims into the module docstring: (a) fixed `DEFAULT_INTERVAL +/- JITTER` and `next_reconsider_ms = now+600s` create the clustered-spike figure that capped exponential backoff + jitter is designed to de-cluster (cite Figure 5 vs Figure 7); (b) the three jitter shapes (Full: `rand(0, min(cap, base*2^attempt))`, Equal: `cap/2 + rand(0, cap/2)`, Decorrelated: `min(cap, rand(base, sleep_prev*3))`) with the blog's measured trade-offs for 100 clients (full fewest calls, decorrelated slightly faster, equal worst, no-jitter worst), so future depleted-route reconsideration can persist an attempt counter and choose Full vs Decorrelated based on whether provider-side retry cost dominates latency; (c) the mechanism does not change N2 asymptotics, only substantially reduces work at reasonable contention, and base/cap remain sizing parameters not set by this note.

Limits retained: illustrative `base*2^attempt` numbers in the blog (OCC with 10 ms mean delay) are not sized for LLM provider quota windows; translating to `remaining_fraction` reconvergence needs a separate sizing pass. No change to `engine_policy.decide()` purity or to `serve()` cadence in this note; the header only documents the de-clustering mechanism and its variant selection, not an activation schedule. The OCC-to-quota analogy is partial: provider quota is authoritative unlike OCC optimism, so backoff applies to retry cadence, not to commit conflict rate.
