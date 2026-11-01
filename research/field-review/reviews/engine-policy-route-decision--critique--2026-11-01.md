---
field: engine-policy-route-decision
angle: critique
date: 2026-11-01
status: discarded
owner: field-review
---

# engine-policy-route-decision × critique — discarded

## What we read

- Huang, Magnusson, Bangalore Muralikrishna, Estyak, Isaacs, Aghayev, Zhu, Charapko, "Metastable Failures in the Wild", OSDI 2022, pp. 73–90. Primary PDF: https://www.usenix.org/system/files/osdi22-huang-lexiang.pdf (fetched 2026-11-01, read as text with pdftotext). Presentation page: https://www.usenix.org/conference/osdi22/presentation/huang-lexiang.
- The critique's own argument, quoted from the paper's introduction: "the distinguishing characteristic of a metastable failure is that the sustaining effect keeps the system in the metastable failure state even after the trigger is removed."
- What it concedes, quoted from Section 2: "By far, the most common sustaining effect is due to the retry policy, affecting more than 50% of the studied incidents." And on recovery: "Recovery from a metastable failure is challenging and often requires reducing load. Direct load shedding, such as throttling, dropping requests, or changing workload parameters, was used in over 55% of the cases."
- A caveat the paper states against its own remedy, Section 6.1: "Unlimited retries, however, can put a lot more workload amplification on the system and make the sustaining effect more severe." Remedies applied without understanding the sustaining loop can extend it (Section 1: "code and configuration changes without truly understanding the metastable failure can exacerbate the problem").

The critique under test: failover to a fallback that absorbs the whole load, plus a retry or re-admission loop against the depleted pool, can hold a system in a degraded state after the trigger is gone. Blind adoption of "fail over to the next healthy target" as a recovery rule would reproduce the paper's sustaining shape on a shared target.

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py` `decide()` (about lines 338–500) moves a role off its current route only when that route is not healthy. The switch path takes a same-provider healthy alias first, then the first healthy candidate in allowlist order. The whole role's decision moves at once; the policy does not split load.
- `engine_policy.py` has no retry loop. It is a pure admission decision that returns `stay`, a switch, `hold` or `queue` with `next_reconsider_ms`. Its header says `usageAwareFallback` is `false` in the upstream OMP settings, so the plant's preemption is admission-time, not a retry on failure. A search of `src/mishe_tauftauf/engine*.py` finds no retry or backoff in the policy path (the only "retry" hits are the settings comments and `engine_rotation.py`'s idempotent-retry wording).
- Staleness and unknown state are held, not treated as capacity (`_classify_account`, about lines 119–172): non-FRESH windows classify as `hold`, and an `hold` current route keeps in-flight work with no reassignment.
- The stay path is hysteretic: a healthy current route returns `next_reconsider_ms = now + min_hold_ms` (default 600 000 ms; `engine_watch.py` passes 600 000 explicitly).
- Overload of the target is not a signal the policy reads. Its health is a quota window fraction. The quota fraction is consumed by the plant's own calls (the routing critique review `engine-model-routing-and-usage--critique--2026-10-31.md` says this).

## What is new here

The paper's sustaining mechanism needs two things the policy does not have:

1. **A retry or re-admission loop against a depleted target.** The paper's most common sustainer is retries (over 50% of incidents). `decide()` is called once per tick by `engine_watch.py` and does not retry a failed admission; a caller that does retry would be the amplifier, and that caller is not in this repository's engine path.
2. **Load moved onto a target whose capacity is what is being exhausted, with no cooldown on the way back.** The switch path does not record when the previous route became unhealthy, and it does not hold the fallback after taking the load. So the plant could in principle flip between two aliases as each window resets. That is a sawtooth, not an amplification loop with work rising: the quota windows reset on the provider's clock, not from the plant's own retries. I did not measure a flip in the decision log; the switch history is not in the reviewed tree, so this stays a possibility, not a finding.

The paper's real lesson for this plant is narrower than "do not fail over". It says the remedy for a sustained loop is to reduce load at the source, and that adding retries or logging to a failing path extends the loop (SPF2 case, Section 6.1). The policy already reduces load at the source by stopping admission on reserve. The thing a blind adopter would reproduce is an added retry on the `queue` or `hold` branch, or a retry in the caller when `decide()` returns a route that then fails. The policy has no such branch today.

## Discard

No retry loop exists in the policy path, and the only amplification the paper names (retry) has no receiver here. The flip-flop risk is real in principle but unobserved: no decision log in the reviewed tree shows a switch-back cycle. Nothing to change in `engine_policy.py` now.

Re-open trigger: any caller of `decide()` that retries a route the policy returned (retry-on-failure), or any decision-log evidence of two or more switches for one role inside one `min_hold_ms` window. On either trigger, apply the paper's rule: bound retries with a budget and shed load at the source before adding a hold, and add a test that a role does not switch back and forth inside one hold window.
