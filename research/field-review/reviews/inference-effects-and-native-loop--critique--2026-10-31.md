---
field: inference-effects-and-native-loop
angle: critique
date: 2026-10-31
status: applied
owner: inference-research
---

## What we read

- Serkan Muhcu, Philipp Schuster, Michel Steuwer, Jonathan Immanuel Brachthäuser, "Multiple Resumptions and Local Mutable State, Directly", Proc. ACM Program. Lang. 9 (ICFP), Article 260, August 2025, DOI 10.1145/3747529. Source PDF: <https://pl.cs.uni-tuebingen.de/publications/muhcu2025multiple.pdf> (the `se.cs.uni-tuebingen.de` URL redirects there). Read as extracted text of the full PDF, not a summary. The quotations below are copied from that extraction; section 1 "Local Mutable State" carries the critique.
- The critique's argument, as the paper states it: "Whether to allow continuations to be multi-shot has far-reaching consequences related to performance and program reasoning, especially in languages with mutable references." And: "Continuations are parts of the runtime stack including stack-allocated mutable references. If resumed twice, the first resumption might update references and affect the second resumption. Implementations that support mutable references and multiple resumptions, like Koka and Effekt, create a backup of the state on capturing the continuation and restore it upon resumption, negatively impacting performance. If the continuation is used at most once, the backup is unnecessary."
- What it concedes: one-shot is the cheap, safe default for mutable state, and multi-shot is "a very useful language feature". The paper's own answer is a runtime that copies stacks only when a shared continuation is resumed, not a rule that forbids multi-shot.
- The paper also quotes van Rooij and Krebbers (2025) that "certain standard program transformations (and thus compiler optimisations) are unsound in the presence of multi-shot continuations". That is quoted secondhand in the paper; we did not read van Rooij and Krebbers.
- The plant's text, read from the working tree: `src/mishe_tauftauf/inference_loop.py` line 105 `context = snapshot(context)`, line 109 `messages = context['messages']`, line 244 `messages.append(message)` before `stop('unknown')`, and the `drive_native` docstring ("Caller must restore provider state and reconcile original effect authority; this API neither restores the session nor retries unresolved selections").

## What this plant already does

The plant's loop is one-shot for effects, as the earlier misread review noted: a non-`completed` outcome stops the loop, and `reconcile_caller_journal` never re-dispatches an `unknown` or `partial` call. The paper's "state the continuation closes over" has a plant analogue: the `messages` list that `drive_native` builds up in memory. The code handles it by copying at entry (line 105) and by making the durable journal, not the in-memory list, the source for any resume (`resume_from`, `_reconstruct_native`). A caller's dict is therefore not touched by the loop, and a continuation from the journal rebuilds from rows rather than from a backup of live state. `tests/test_inference_loop.py` around line 196 pins that a `before_turn` callback cannot edit history through its snapshot, and the `damaged history` tests (line 485 onward) pin that a changed context fails closed.

## What is new here

The critique says the cost of safety is paid in the state, not the count of resumptions. In the paper, a one-shot continuation is safe only because nothing else can observe the state between capture and resume. In the plant, the same holds for `messages`: one list, owned by one `drive_native` call. The mechanism is sound by code reading. The gap is the test surface. No existing test asserts that after an `unknown` stop, the caller's own `context` object is byte-identical to its value before the call, and the journal is the only thing a later resume reads. The critique predicts that such a gap is where a future refactor would duplicate state, for example by letting a caller hand the same in-memory `messages` back into a second `drive_native` without the journal check. This is a hypothesis about a future change, not an observed defect today. Limits: the paper is about delimited continuations with stack-allocated references; the plant has no continuations, only a journal-backed loop. The transfer is an analogy and is labelled as one.

## Application

target: tests/test_inference_loop.py

Proposal to inference-research (the owner of this file and of `inference_loop.py`): add one test beside `test_unresolved_effect_stops_without_retry` (line 362) that calls `drive_native` with a dispatch returning `unknown`, then asserts (a) the caller's `context` dict equals a deep copy taken before the call, and (b) a second call with `resume_from` over the same journal does not dispatch again. This pins the snapshot boundary the critique depends on. I have not run or written this test; the owner decides whether it lands. Detection interval: the test runs with the existing `tests/test_inference_loop.py` suite on each commit.

Disposition of the proposal: keep if the test passes on current code, which would make it a pin rather than a fix. If it fails, that is a real defect in the caller boundary and goes to the owner as a bug, not as a rewrite of the loop.
