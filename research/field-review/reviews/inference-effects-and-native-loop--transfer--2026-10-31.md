---
field: inference-effects-and-native-loop
angle: transfer
date: 2026-10-31
status: discarded
owner: inference-research
---

## What we read

- Pat Helland, "The I's Have It: Identity, Immutability, Idempotence, and Interchangeability Form the Backbone of Distributed Computing", CIDR 2019 (Asilomar), <https://mail.vldb.org/cidrdb/papers/2019/cidr2019_161.pdf>, CC BY 3.0. Read as text extracted locally with `pdftotext -layout`. Passages read, quoted exactly: "Idempotence is the property that says it's OK to do work more than once. If it happens at least once, the behavior is the same as if it happens exactly once." "Idempotence frequently depends on having an identity for the work. You need to understand the identity of the operation to decide if you've done it before." "Sometimes, the identity used to provide idempotence is a consequence of some connection or session. That works until a new session arrives to retry the failed session." The bank example: the transaction's identity is the preassigned check number, and "The check must clear in less than one year after written", which bounds how long the list of cleared checks must be kept.
- Not reached: Helland, "Idempotence Is Not a Medical Condition", ACM Queue 10(4), 2012, <https://dl.acm.org/citation.cfm?id=2187821>. The ACM page returned HTTP 403 to the fetcher. The CIDR paper is Helland's own later restatement of the same argument, and it is the text read here.
- The plant's own text, from the working tree: `src/mishe_tauftauf/inference_effects.py`, the `dispatch` docstring ("an effect that lost its receipt is UNKNOWN to a successor, not permission to try again"), the `CallIdConflict` docstring ("a provider does not echo the caller's id"), and `reconcile` ("never executes"); and `docs/how-it-works.md` around line 146 ("this callback is not an idempotent resend API").

## What this plant already does

The plant has the identity Helland says idempotence needs, but only on its own side of the boundary. `allocate` gives each effect a stable `operation_id`, and `dispatch` checks it against the intent, start and outcome journals before any executor runs. A completed operation returns its stored outcome. A started operation with no outcome returns `unknown` and is never executed again. The loop stops on `unknown` (`inference_loop.py`, around lines 222-248) and tests pin that (`tests/test_inference_effects.py:430`). So the plant treats retry as safe only where it can check identity itself, and it refuses the retry everywhere else. That is the position Helland's text supports: "you need to understand the identity of the operation to decide if you've done it before." The plant also keeps one record per provider call id (`CallIdConflict`), which is Helland's requirement that an identity be unique.

## What is new here

Helland's argument has a second half: idempotence is a property of the target, not of the sender. The check is safe only if the receiver records the identity and consults it on retry, and the sender cannot make a target deduplicate on its behalf. In this plant the target is the provider, and the provider does not echo `operation_id` (the `CallIdConflict` docstring says so). So the plant's `operation_id` dedupes only the plant's own journal. A retry sent to the provider would be a second external effect that the provider cannot recognise, and the plant's refusal to retry is the correct consequence of that, not a missed opportunity.

The one transferable point in the paper is the bank's bounded identity window: a dedupe record may be dropped once the check can no longer arrive. The plant's journals are append-only and are never pruned, so a window would be a retention decision. I did not find evidence that journal size or lookup cost is a problem on this plant, so there is no measured reason to add a window now.

Limits of the reading: the paper is a short essay on identity, and it gives no mechanism for provider-side deduplication. I did not read the 2012 article itself. Whether a given native provider accepts an idempotency key is a question about that provider's API, which this review did not check.

## Discard

The plant's journal already makes a retry safe only where it can check identity, and the provider cannot check the plant's identity, so refusing to retry is correct and no change to `inference_effects.py` or `inference_loop.py` follows from the paper.
