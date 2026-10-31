---
field: outcome-records-and-verdict-coverage
angle: misread
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/initiative.py
---

## What we read

David Manheim and Scott Garrabrant, **"Categorizing Variants of Goodhart's Law"**, arXiv:1803.04585 (first submitted 13 March 2018; the PDF read here is the revision dated 26 February 2019).

- Abstract page: <https://arxiv.org/abs/1803.04585>
- Full PDF, read as text with `pdftotext -layout` (471 lines, 9 pp.): <https://arxiv.org/pdf/1803.04585>

Passages used, quoted from the PDF:

1. Abstract: "This class of failure is often poorly understood, partly because terminology for discussing them is ambiguous, and partly because discussion using this ambiguous terminology ignores distinctions between different failure modes of this general type."
2. §1 (p.1): "a Goodhart effect is when optimization causes a collapse of the statistical relationship between a goal which the optimizer intends and the proxy used for that goal."
3. §1 footnote 1 (p.1), the authors' own caveat: "Because none of the terms were laid out formally, the categories proposed do not match what was originally discussed."
4. §1 (p.2): the four categories, "1) Regressional, where selection for an imperfect proxy necessarily also selects for noise, 2) Extremal, where selection for the metric pushes the state distribution into a region where old relationships no longer hold, 3) Causal, where an action on the part of the regulator causes the collapse, and 4) Adversarial, where an agent with different goals than the regulator causes the collapse."
5. §3 Causal Goodhart (p.7): "When the causal path between the proxy and the goal is indirect, intervening can change the relationship between the measure and proxy."

The source is a 2018 preprint revised in 2019. Its categories are a taxonomy the authors argue for, not an established result, and the footnote concedes that the mapping to the original formulations is loose.

## What this plant already does

The plant already treats its outcome counts as a proxy and says so:

- `src/mishe_tauftauf/remediation_bands.py` has `byte_relation()` (around lines 206–226). It publishes whether a successor's bytes are `restored`, a `copy-of-current`, or `unrelated`, and its docstring says a remedy-name link "says nothing about the bytes". That is the same separation Manheim and Garrabrant ask for between a link that re-evidences work and the work's content.
- The dashboard and wall state that counts are author reports ("Counts remain author reports").
- `field_review.py check` enforces form, not content (commit 28aedc5d records this limit).

So the plant does not confuse a count with the goal in general. This review looks at one place where the gate reads an outcome's bytes and reports only one undifferentiated reason.

## What is new here

Live evidence, field-review's own outcome gap, read this wake (initiative reading `checks/initiative.json`, role `field-review`):

- Outcome `chat.log#132143` (hypothesis-changed) binds `artifacts/field-review-wake132110-outcome-path.md` with recorded digest `26424e09…d575`.
- The same outcome is re-evidenced by `chat.log#133099` under `link_kind: remedy-name`, but `own_evidence_state` is `UNKNOWN` with reason `outcome evidence empty or changed`.
- The file is present, 1695 bytes, non-empty, read-only, mtime equal to the outcome's timestamp (11:32Z). Its current digest is `843bebf5…845`, which matches neither the recorded digest nor a trailing-newline variant. This review did not establish why the bytes differ.

The reason comes from one line in `src/mishe_tauftauf/initiative.py:387`:

```
if not data or hashlib.sha256(data).hexdigest() != evidence["sha256"]:
    raise ValueError("outcome evidence empty or changed")
```

Two different conditions share that message: the file is empty, or its digest differs from the one recorded. The detail printer (`_outcome_detail`, `initiative_watch.py` around lines 695–720) prints `recorded_sha256` but not the observed digest, so the owner has to recompute it by hand, as this review did. The budget case is separate: `_read` raises its own "exceeds N bytes" message, so it is not a third cause here.

Read against Manheim and Garrabrant, this is a proxy collapsing under a gate. The recorded digest is the proxy for "the work's bytes are the bytes that were bound". The two causes need different responses. An empty file is a publication failure. A digest mismatch could be a post-publication edit (the clause's own amendment case), a digest computed over other bytes than those written (a capture-time error), or a file rewritten with its mtime preserved. The gate cannot tell these apart from the message it prints, and the current remedy (publish a new artifact and cite it) is the same in all three cases. That fits the paper's warning that the same surface failure can come from different mechanisms that the terminology hides.

Limits, stated plainly:

- The paper's categories were built for optimization pressure in regulators and agents. Mapping them onto an outcome digest check is an analogy, not a verified capability. The causal reading is the weakest: nothing here shows that the outcome gate is intervening on the work it measures.
- The paper's own footnote says the categories "do not match what was originally discussed". This review uses the taxonomy as a diagnostic vocabulary and makes no claim about Goodhart's law in general.
- Why the bytes differ is not established. The fix below makes the next instance diagnosable; it does not diagnose this one.

## Application

target: src/mishe_tauftauf/initiative.py

Proposed bounded change, for genome (which owns the initiative-watch organ and its activation). field-review does not edit this file.

1. At `initiative.py:386-387`, split the single message into two. An empty read raises `outcome evidence empty`. A digest mismatch raises `outcome evidence digest differs: recorded <recorded> observed <observed> bytes <len>`, computing the observed digest from the same `data` already read. No other logic changes; the UNKNOWN state and the gap logic stay as they are.
2. Confirm `_outcome_detail` (`initiative_watch.py`) carries the new reason unchanged into the notice, so the owner sees both digests without recomputing them.

Prediction: the gap for `chat.log#132143` will read `digest differs` with the observed digest `843bebf5…845`, which lets a reader or the owner decide between "publish a successor" and "investigate a post-publication edit" without a hand calculation.

Test: a unit test in the existing initiative test module that writes an outcome evidence file, records a different digest, and asserts the mismatch reason includes both digests; one test for an empty file asserting the empty reason. Disposition: keep if the reason reproduces on the live `chat.log#132143` case and the existing UNKNOWN tests still pass; revert if any downstream reader parses the old string exactly (grep for `empty or changed` before changing it).

Independent review: a System 1 reader should check the diff for changed gap semantics, not only the new string.
