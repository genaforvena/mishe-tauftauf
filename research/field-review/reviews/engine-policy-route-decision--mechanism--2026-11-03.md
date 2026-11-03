---
field: engine-policy-route-decision
angle: mechanism
date: 2026-11-03
status: discarded
owner: genome
---

# engine-policy-route-decision × mechanism — discarded

## What we read

Google SRE, *Site Reliability Engineering*, Chapter 21, “Handling Overload,” “Client-Side Throttling” and “Utilization Signals” sections: https://sre.google/sre-book/handling-overload/ (read 2026-11-03). Adaptive throttling uses each client's recent attempted and backend-accepted requests to cap outgoing traffic when quota rejections rise; the chapter cautions that it works poorly for sporadic clients. It also describes task-local utilization signals (such as CPU/load) for protecting overloaded backends.

## What this plant already does

`src/mishe_tauftauf/engine_policy.py` bases admission on fresh account quota windows and reservations, holds on stale or unknown evidence, and applies a reserve threshold. Its inputs do not contain per-request accepted/rejected outcomes or backend utilization.

## What is new here

The source's operational throttles require observed overload signals. They do not provide a generic persistence rule for this policy: its reported quota depletion is authoritative admission evidence, not a noisy backend rejection or utilization sample.

## Discard

No mechanism transfers safely: `decide()` lacks the request-outcome or utilization observations this mechanism requires, and its existing quota gate already acts on the relevant provider signal.
