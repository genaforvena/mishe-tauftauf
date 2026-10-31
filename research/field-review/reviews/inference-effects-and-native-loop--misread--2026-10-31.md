---
field: inference-effects-and-native-loop
angle: misread
date: 2026-10-31
status: discarded
owner: inference-research
---

## What we read

- Gordon D. Plotkin and Matija Pretnar, "Handling Algebraic Effects", Logical Methods in Computer Science 9(4:23), 2013, DOI 10.2168/LMCS-9(4:23)2013; arXiv:1312.1399: <https://arxiv.org/abs/1312.1399>. Read the full text through the ar5iv rendering <https://ar5iv.arxiv.org/html/1312.1399>, in extracted form (section 2.3 Terms and section 2.5 Abbreviations, quoted in part: an operation application "triggers the operation op with parameter V and then binds the outcome to x"; "the continuation k receives an outcome W, determined by M_op"; the handling term "may use the continuation k any number of times"; "the remaining operations are handled by themselves (so they are 'passed through')"; exception handlers never use k, since the raise operation is nullary). This is an extraction by a fetch summarizer, not a verbatim reading, so the quotations are paraphrase-close and should be checked against the PDF before anyone cites them as exact.
- The plant's own text, read from the working tree: `src/mishe_tauftauf/inference_loop.py` `drive_native` (around lines 222-248: a dispatch outcome other than `completed` makes the loop `stop('unknown')`, and the comment "Exceptions propagate: never retry blindly"), and the `inference_effects.py` module docstring ("leaves an operation `unknown` for a fresh process to reconcile; it is never silently retried").

## What this plant already does

The plant's loop is one-shot in the sense the paper's handler is not. Each tool call reaches `dispatch` once. A `completed` outcome resumes the model with one tool result. Any other outcome (`unknown`, `partial`, `not-started`) stops the loop, and the tests pin that: `test_unresolved_effect_stops_without_retry` (`tests/test_inference_loop.py:362`), `test_interrupted_turn_policy_is_unknown_and_never_reinvoked` (line 282), `test_reconcile_never_dispatches_for_unknown_outcome` (line 1049), and `test_interruption_between_start_and_outcome_leaves_unknown_and_no_rerun` in `tests/test_inference_effects.py:430`. So the plant already makes the continuation effectively single-shot for effects, and it refuses the retry that a multi-shot handler would make by calling `k` again.

## What is new here

The paper's wording shows the misread this cell was looking for. In the algebraic-effects reading, "resume with the continuation" and "retry the operation" are one act, and a handler may call `k` zero, one or many times. A reader who takes the plant's `dispatch` callback as a handler could conclude that re-invoking the callback after `unknown` is the handler's legitimate choice. The paper makes that choice available but does not make it safe. It says nothing about effects being repeated, and the plant's contract is the opposite: whether a call may run again is decided by reconciliation, not by the handler's choice to resume. Two small things the reading makes visible: `drive_native` accepts `not-started` from `dispatch` as a non-completed outcome and labels the stop `unknown`, which is conservative but blurs the paper's distinction between "the operation never ran" and "its effect is unknown"; and `_EFFECT_STATUSES` in `inference_effects.py` omits `not-started`, so the two vocabularies differ. Neither changes what runs. Both only affect how a stop is named.

## Discard

The plant already keeps the effect continuation single-shot and never resumes an unresolved effect, and its tests pin that; the paper's multi-shot handler gives no reason to change `inference_loop.py` or `inference_effects.py`, and the `not-started` naming gap is cosmetic in its effect on execution.
