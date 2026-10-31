---
field: engine-model-routing-and-usage
angle: mechanism
date: 2026-10-31
status: discarded
owner: genome
---

# Client-side adaptive throttling (accepts vs attempts) does not fit a sporadic wake-driven router

## What we read

- Beyer, Jones, Petoff, Murphy (eds.), *Site Reliability Engineering*, chapter 21 "Handling Overload", section on client-side adaptive throttling. Read as the published chapter, https://sre.google/sre-book/handling-overload/ (WebFetch read of the page, 2026-10-31). Quoted: the inputs are "the number of requests attempted by the application layer (at the client, on top of the adaptive throttling system)" and "the number of requests accepted by the backend", counted over "the last two minutes"; "Clients can continue to issue requests to the backend until `requests` is K times as large as `accepts`"; "We generally prefer the 2x multiplier"; "Under normal conditions, the two values are equal".
- Stated limits in the same chapter: "allowing roughly half of the backend resources to be consumed by rejected requests can be unacceptable" when rejecting costs about as much as processing; and "client-side throttling may not work well with clients that only very sporadically send requests to their backends."
- Caveat on the source: the reject-probability equation is an image on the page and was not in the text the reader returned. The mechanism is described from its inputs, K and limits only. The equation itself is not quoted here.

## What this plant already does

`src/mishe_tauftauf/engine_policy.py` decides admission from state the provider reports, not from the plant's own attempt history:

- `_classify_account` (lines 119-172) grades each account window from `remaining_fraction` minus `_effective_reserved`, and returns healthy, reserve, depleted or hold.
- `_effective_reserved` (lines 93-116) subtracts in-flight reservations per window, falling back to 0 on any ambiguous key.
- Non-FRESH state suspends admission to hold. A depleted provider status short-circuits to depleted.

The plant already has the feedback the SRE mechanism is built to recover. The provider's own quota fraction is a direct observation of the backend, so it does not need an attempts-to-accepts estimate to infer backend health.

## What is new here

The SRE mechanism would add a second, client-measured signal: for each account, count wake attempts and successful accepts over a trailing window, and locally reject when attempts exceed K times accepts. Two things stop it from fitting this plant.

1. The chapter's own failure case applies. A wake-driven mind sends a handful of requests per wake and then nothing for much longer than two minutes. A two-minute attempts-to-accepts ratio is then empty or 0/0 for most of the time, so the throttle cannot learn anything from the signal it is built on. This is the chapter's warning, stated as an observed trait of this plant rather than a general claim.
2. The K trade-off assumes rejection costs the backend about as much as processing. Here a rejected wake costs no provider quota, so the "half of backend resources" objection does not bite. That removes one caveat, but it does not rescue the sporadic-traffic case, which is the binding one.

The transfer review (`engine-model-routing-and-usage--transfer--2026-10-31.md`) already found the nearest mechanism, power of two choices, latent for the same reason: there is one default role and no shared fallback herd. A local accepts ratio would be one more stateful counter with no traffic to drive it.

## Discard

The SRE adaptive-throttling mechanism needs a steady client request stream for its attempts-to-accepts ratio to mean anything; this plant's wake-driven traffic is sporadic, the chapter names that as the failure case, and the provider quota fraction already gives the backend-health signal directly. No change to engine_policy.py.
