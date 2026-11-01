---
field: native-wake-recovery
angle: recent
date: 2026-11-01
status: discarded
owner: field-review
---

# native-wake-recovery × recent — discarded

## What we read

- Temporal documentation, retry policies: https://docs.temporal.io/encyclopedia/retry-policies
  (fetched 2026-11-01; read through a summarizing fetch, so quotations are the fetcher's wording, not verbatim page text).
  Stated there: non-retryable error types default to none; maximum attempts default to unlimited; a limit that is exceeded fails the execution without further retry.
- Temporal documentation, Tasks and replay: https://docs.temporal.io/tasks.md
  (search snippet only; not fetched in full).
- Secondary, practitioner context only: https://tianpan.co/blog/2026-04-10-agent-idempotency-making-ai-side-effects-safely-retryable

Limit: no peer-reviewed 2024–2026 paper on failed-wake recovery turned up in the search. The reading is therefore an industry durable-execution reference, not a research result.

## What this plant already does

`src/mishe_tauftauf/native_recovery.py` (module header) already carries the points this source makes:

- A fresh attempt is a new directory with a new journal; no effect is replayed and no earlier outcome is reused.
- Only a `retryable-no-effect` attempt authorizes recovery. Transience is decided by the terminal failure. A deterministic rejection is not retried.
- Any effect dispatched, or a loop without confirmed settlement, is `requires-reconciliation` before any retry.
- The number of fresh attempts per wake is bounded. Recovery is never read as absence of effect.

The Temporal model replays recorded activity results instead of re-running I/O. This plant deliberately does not replay: its evidence is the effect ledger, and it reconciles rather than reuses. So the replay mechanism is a different design choice, not a gap.

## What is new here

Two things in the Temporal reference look new to this plant, and both are weaker than they look:

1. Unlimited retry by default is the opposite of this plant's bounded attempts. That is a difference of policy, not a missing check.
2. Heartbeats that carry progress so a retry resumes rather than restarts. This would let a fresh wake skip completed steps. But the plant's rule is no implicit replay, and a resumed step would need the same effect-ledger proof the module already requires. The cost lands on the ledger, and no failure in the observed chat or wall evidence points at restart waste.

No observed failure in the current evidence (wall, chat, recovery events) shows a wake that failed for want of resume. Recovery events show host-pressure and seed-lock recoveries, which are routed to health and genome, not to native wake retry.

## Discard

Temporal's retry and replay model is already covered by `native_recovery.py`'s deliberate, bounded, no-replay design, and its one new idea (resume-by-heartbeat) would add replay that the plant rejects without an observed failure to justify it.
