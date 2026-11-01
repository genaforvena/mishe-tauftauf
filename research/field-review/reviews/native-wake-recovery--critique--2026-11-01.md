---
field: native-wake-recovery
angle: critique
date: 2026-11-01
status: discarded
owner: field-review
---

# native-wake-recovery × critique — discarded

## What we read

- Pat Helland, *The I's Have It: Identity, Immutability, Idempotence, and Interchangeability Form the Backbone of Distributed Computing*, CIDR 2019, https://mail.vldb.org/cidrdb/papers/2019/cidr2019_161.pdf (fetched 2026-11-01; read as the PDF text, CC-BY 3.0). The relevant critique is in the section "Identity and Idempotence": idempotence "frequently depends on having an identity for the work"; "sometimes, the identity used to provide idempotence is a consequence of some connection or session. That works until a new session arrives to retry the failed session." The paper also says idempotence "ignores side effects outside of the plane of abstraction provided by the service", and that a bank bounds its cleared-check list by a one-year clearing window.
- Limit: the ACM Queue original (Helland 2012, "Idempotence is not a medical condition", queue.acm.org) returned a Cloudflare block to this fetch and was not read. The CIDR paper is a later restatement by the same author and is the reading used here.

## What this plant already does

The failure the critique names is a retry in a new session that cannot see the failed session's operation identity. The plant's fresh-wake design runs into that shape directly, so the source was checked against the code:

- A fresh attempt is a new session: `fresh_attempt` (`src/mishe_tauftauf/native_recovery.py:489`) picks a new `wakes/<wake>-retry-<n>` directory, and the wake is started with a new `EffectBoundary` over `attempt / 'effects'` (`src/mishe_tauftauf/native_resident.py:469`). The effect store is per attempt; nothing from the failed attempt's store is loaded into the fresh one. So the operation identity of an earlier attempt does not carry over.
- The plant does not rely on cross-attempt idempotence to avoid duplicates. It fails closed instead. `classify_attempt` (`src/mishe_tauftauf/native_recovery.py:374`) returns RETRYABLE only when the failed attempt's own store shows no dispatch: `dispatched = starts + outcomes` and `proposed = proposal/tool_result rows` (`:414-416`). Any such row returns RECONCILE (`:421-428`). Reading errors, a store still locked, or a mutated store return UNKNOWN before any retry (`:380-413`). A retry therefore only happens when no call was admitted, so the duplicate the critique warns about cannot be created by the retry itself.
- Every call goes through the boundary, including shell `exec`: `dispatch` calls `boundary(call)` (`src/mishe_tauftauf/native_resident.py:501`) and `_execute` routes `exec` to `_exec` (`:322-323`). So the zero-dispatch proof covers shell side effects, not only the tools that wrote to a ledger.
- Reservations (`intents` without a provider call) are not counted as dispatched. This is sound only if a reservation never executes. The boundary's `bind_dispatch` (`src/mishe_tauftauf/inference_effects.py:707`) upgrades a reservation in place and starts it once; that is the only route to execution, and the reserved id has no start row until then.
- The no-replay rule is written in the module header (`native_recovery.py:25-28`): a fresh attempt never reuses an earlier journal, and recovery never reads as absence of effect.

## What is new here

Nothing in the critique is new to this plant. The paper's warning (identity bound to a session fails when a new session retries) is the reason this plant refuses cross-attempt reuse and classifies a retry only on zero dispatched rows. The one place the critique could bite is a future change that keys effects by something that survives the attempt — for example, reusing the failed attempt's `operation_id` in a fresh attempt to resume rather than retry. That is the change the critique argues against, and the plant does not make it.

The paper's bank example (a bounded identity window that expires) is a retention question for an identity store. This plant has no cross-attempt identity store, so there is no retention window to bound. If one is ever added, the bound needs its own review.

## Discard

The critique's mechanism (a retry in a new session cannot see the failed session's identity) is already avoided by fail-closed classification on the failed attempt's own zero-dispatch proof, so no change follows from this reading.
