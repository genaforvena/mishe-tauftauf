---
field: inference-effects-and-native-loop
angle: mechanism
date: 2026-10-31
status: discarded
owner: inference-research
---

## What we read

- Stripe, "Idempotent requests", API reference, <https://docs.stripe.com/api/idempotent_requests>. Read via the vendor's published page (WebFetch, extracted text). Passages read, quoted: "A client generates an idempotency key, which is a unique key that the server uses to recognize subsequent retries of the same request." "For API v1, we save results only after the execution of an endpoint begins. If incoming parameters fail validation, or the request conflicts with another request that's executing concurrently, we don't save the idempotent result because no API endpoint initiates the execution. You can retry these requests." "For API v1, Stripe saves the status code and body of the first request made with a given idempotency key after endpoint execution begins." The mechanism named: the server's record of an effect begins at the point of execution, so a refusal that happens before execution leaves nothing to dedupe and may be retried.
- The plant's own text, read from the working tree: `src/mishe_tauftauf/inference_effects.py` `EffectLedger.dispatch` (around lines 770-830: the start claim is appended before `executor(call)`; an `except ValueError` branch that reaches the caller as `StoreUnavailable`; a generic `except Exception` that records `unknown` with `failure="capability-failed"`), and `EffectBoundary._runner` (around line 1456: "The execute callback MUST recheck current authority against its own state").
- The plant's pinning test, `tests/test_inference_effects.py:117-146`: "A started-but-unrecorded outcome must not be re-executed: its effect is unknown." It raises `RuntimeError("lost the receipt after the effect")` and asserts `started-without-outcome` on the successor.
- Live evidence checked: effect journals under the site, grepped for `started-without-outcome` and `capability-failed`. The matches are `wall-outcome` records in `records/`, a different store, not inference-effect journal entries. No inference-effect record of a pre-effect authority refusal was found.

## What this plant already does

The plant has the dispatch-before-execution order Stripe describes, and it is stricter than Stripe in one place. `dispatch` writes the start claim before the executor runs, so any failure after that point, including one that happened before the capability did anything, reads back as `unknown` to a successor. Retry is refused. The test above pins this on purpose: an exception from the executor does not prove that no effect happened, so the plant will not retry it. That is the position the paper of record this cell covers would also take for an effect whose outcome is unknown.

## What is new here

Stripe's rule has a second clause that the plant lacks: a refusal before execution is not saved and may be retried. The plant's `_runner` documents that the caller's `execute` must recheck authority. If that recheck refuses before any side effect, the refusal reaches `dispatch` as a generic exception. The record is then `unknown` and a retry is refused forever, although the effect is provably absent. The plant has no typed pre-effect refusal that dispatch could treat as "not produced", so the gap is real in code. I checked it by reading, not by running a case.

The gap is not observed. The effect journals show no such refusal, and the only `started-without-outcome` records found are outcome records from another store. A fix would add a new exception class and a new dispatch branch to a boundary that the tests pin as "exception is unknown". Without a live case, that change would be built for a failure nobody has seen, and it weakens a rule that is correct for the cases that do happen.

Limits of the reading: the Stripe page describes Stripe's API, not the plant's provider. The plant's native provider does not echo `operation_id` (the `CallIdConflict` docstring), so no provider-side key is available to use in any case. This review did not read Stripe's full retry-policy section or any second source on pre-effect refusal.

## Discard

No live inference-effect journal shows a pre-effect authority refusal, so the unknown-on-exception rule stays and no change to `inference_effects.py` follows from this reading. Trigger to reopen: a journal entry with `started-without-outcome` whose executor can show no side effect happened.
