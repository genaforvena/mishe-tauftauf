---
field: coordination-delivery-and-release
angle: transfer
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/delivery.py
---

# Coordination, delivery and release — transfer: a delivery is complete only when the target acts, so its acknowledgement must not be written by the sender

## What we read

- J. H. Saltzer, D. P. Reed and D. D. Clark, "End-to-End Arguments in System Design", ACM Transactions on Computer Systems 2(4), November 1984, pp. 277–288. Source read: the MIT Laboratory for Computer Science PDF, https://web.mit.edu/Saltzer/www/publications/endtoend/endtoend.pdf (fetched 2026-10-31; text extracted locally with `pdftotext` from the saved PDF). Passages read: the abstract (p. 1), the statement of the argument (p. 2), the careful-file-transfer threat list and the end-to-end checksum (pp. 2–3), "Performance aspects" (p. 4), and "Other examples", section on delivery acknowledgement (p. 6).
- The paper's own statement of the argument (p. 2): "The function in question can completely and correctly be implemented only with the knowledge and help of the application standing at the end points of the communication system."
- The acknowledgement passage (p. 6), in its words: "knowing for sure that the message was delivered to the target host is not very important. What the application wants to know is whether or not the target host acted on the message … The acknowledgement that is really desired is an end-to-end one, which can be originated only by the target application – 'I did it', or 'I didn't.'"
- Not read: the later formal treatments of the argument, and the two-phase-commit literature the paper cites. This review does not rely on them.

## What this plant already does

- `src/mishe_tauftauf/delivery.py:574` `finish` refuses to complete a delivery unless the exact final main CI passes (`read_ci`, `result.get("sha") != head`). That is a check made from the repository side, at the exact SHA.
- The doctrine already says that a commit or push is not live delivery: `AGENTS.md` and the local doctrine both state "A green test, commit or push is not proof of live delivery."
- `delivery.py:391` and `task_state.py` record an evidence file by path and SHA-256, so the record cannot silently change after it is written.
- `src/mishe_tauftauf/wall_patch.py:378-379` takes an `--observe` argument, described as "checking real live consumers", as a separate step from activation. The plant already has the concept of an observation that is not the author's.

## What is new here

The paper's point maps onto `delivery.finish` in a way the plant has not yet checked. In the paper's terms, `finish` is where the sender's acknowledgement is recorded as the delivery's completion.

1. **The consumer acknowledgement is written by the sender.** `finish` requires a rollout file whose JSON names `sha`, `state == "pass"` and a non-empty `consumers` list. The author writes that file. `task_state._evidence` (`task_state.py:82`) only resolves the path, reads up to 1 MiB and hashes it. The hash proves the file did not change after it was written. It does not show that any consumer loaded the SHA. The record's own reason string says "Author verified final CI and deployed consumers", so the claim and the evidence come from the same party. In the paper's words, this is an "I did it" from the sender, not from the target.
2. **The check that exists is the paper's "delivered to the target host", not "acted on".** Exact-SHA CI proves the code passed in CI. It does not prove any running consumer is executing it. The paper says that acknowledgement of delivery alone is "not very important". For this plant, the consumer's loaded bytes are the acknowledgement the work needs.
3. **The plant already has the right kind of observer and does not use it here.** `wall_patch.py` separates activation from an observation step that checks live consumers. `delivery.finish` does not call any such observation. Its completion depends only on the file the author supplies.

Proposed mechanism: the consumer-side acknowledgement should come from a reader that is not the author. The reader is the live runtime, read through the same route the consumers use: the running pin (`health/runtime-release.json`) and the loaded release of each consumer. That is the check the plant's doctrine already asks for ("check actual loaded bytes, caller and pane").

## Application

target: src/mishe_tauftauf/delivery.py

Proposal for genome (owner of delivery, and the role that checks exact-SHA CI and live consumers). This is a bounded change to `finish` and its evidence check. It is not a change to the CI gate.

1. In `finish`, keep the exact-SHA CI check and the phase check as they are.
2. Replace the author-supplied `consumers` list as the completion evidence. `finish` should read the live consumer observation (the same observation `wall_patch --observe` runs, or the pin plus the loaded SHA of each consumer) and accept completion only if every consumer reports `head`. An author-written file may still be recorded for provenance, but it is not sufficient on its own.
3. Keep the author-written file as provenance. Name in the record which party produced the acknowledgement: `acknowledged_by: observer` or `acknowledged_by: author`. A reader of `delivery show` then sees which claims are the target's and which are the sender's.

Activation and check. This is a running-code change. It needs deterministic tests, an independent reading, and a live check. The test is a controlled failure: point the observer at a consumer that still loads the previous SHA, and `finish` must refuse. The live check is the next real delivery: its record must name `acknowledged_by: observer`. A green test is not the check.

Limits. The paper argues for end-to-end acknowledgement in general, and it is written about network applications. The mapping to a delivery record is an analogy. The paper does not say how often an observer lags the loaded SHA, and this plant has no measured lag yet. I did not trace every caller of `finish`, so the change may affect callers outside the ones read here.
