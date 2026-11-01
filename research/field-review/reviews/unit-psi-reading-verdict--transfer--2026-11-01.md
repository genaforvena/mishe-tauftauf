---
field: unit-psi-reading-verdict
angle: transfer
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/unit_psi_reading.py
---

# unit-psi-reading-verdict — transfer (2026-11-01)

## What we read

- Primary source: L. Lamport, "Time, Clocks, and the Ordering of Events in a Distributed System", Communications of the ACM 21(7), July 1978, pp. 558–565. Read as the PDF page images at https://lamport.azurewebsites.net/pubs/time-clocks.pdf (pages 1–2 this turn; the clock-condition section on later pages was not re-read).
  - Abstract: a "bound is derived on how far out of synchrony the clocks can become" for physical clocks that drift.
  - Section "The Partial Ordering": "The relation 'happened before' is therefore only a partial ordering of the events in the system." Two events are concurrent when neither can causally affect the other. Lamport's own example: process P, before its event p4, "could at most know what Q was planning to do" at q4, so the order of P's and Q's events is not fixed by the diagram alone.
  - Lamport's rule for physical clocks is that they are never exactly synchronised, so an ordering claim between events on different processes is valid only inside a stated bound.
- Plant evidence, read from source, not the docstring: `src/mishe_tauftauf/unit_psi_reading.py` `stall_inversions()` (line ~174) compares one interval's `some.total` delta for a child cgroup against its ancestor's delta and reports a "hierarchy break" when the child is larger by more than `STALL_SKEW_TOLERANCE_US` (1 s). The docstring already records the measured limit: 16 of 193 intervals exceed it, and "read-order skew, not a proven break."

## What this plant already does

- The reader stamps one time for the whole record: `attribute()` and `read_unit_psi()` use `latest["at"]` as the single event time. Every cgroup in the record is treated as observed at that one instant.
- `stall_inversions()` compares the cumulative stall deltas of parent and child as if both were read at the same moment. Its docstring already says it is "skew-sensitive" and "not a proven break", and names the missing per-cgroup read timestamp as the cause of the open question.
- The reader keeps unknown separate from zero for counter deltas (`high_delta` is None without an anchor, not 0). That is the same discipline Lamport's partial order needs: an unordered pair should not be reported as ordered.

## What is new here

Lamport's paper gives the transfer in one sentence: two observations on different processes support an ordering claim only when a causal path or a known clock bound connects them. Here the two cgroup reads are on one sampler process, but they are separate reads at separate times, so they are separate events. The reader currently has no clock bound for the pair, so the inversion test asserts an order it has not measured.

The transfer changes the verdict, not the arithmetic. If per-cgroup read times exist, the pair (child read at t_c, parent read at t_p) has a known gap |t_c − t_p|. A child-minus-parent excess is an inversion only when it cannot be explained by that gap; otherwise the pair is concurrent for this measurement. If read times are absent, the honest output is "unordered", not "inverted". The current docstring says this in prose. The transfer asks that the code's return value say it too.

Predicted observation (testable on existing data): the 16 violating intervals in `operator/unit-psi.jsonl` show record-start gaps of about 60 s (senses' replay). The per-record timestamps cannot fix a bound on read-to-read lag inside a record, so the predicted outcome is that these 16 stay unordered, not that they disappear. A sampler that stamps each cgroup read would turn the question into a number: lag ≥ excess means skew; lag < excess means a real break worth a hierarchy check.

Limits:
- Lamport's result is about message-ordered events on separate processes. A single sampler process reading cgroup files is a weaker analogue. The paper supports the separation of "ordered" from "concurrent"; it does not supply a value for the bound here.
- The kernel's parent-charging premise is still unverified (see the critique and misread reviews for this cell). This transfer does not depend on that premise; it only requires that the test say what it did not measure.
- The sampler is outside this repository (`operator/unit_psi_sampler.py` is runtime state). A per-read timestamp needs the sampler owner. This review names the reader's part only.

## Application

target: src/mishe_tauftauf/unit_psi_reading.py

Proposal to senses (owner), not an edit by field-review:

1. `stall_inversions()` returns three classes, not one set: `inverted` (excess beyond the bound with a known read gap), `unordered` (no per-read timestamps, or gap ≥ the excess), and `ordered` (within tolerance). The current function's return set maps onto `inverted` only when read times are present; otherwise its members go to `unordered`.
2. Accept an optional `read_at: dict[str, float]` (per cgroup) in `read_unit_psi()`; when it is absent, state `read_times=absent` in the sense's sample so consumers can see the skew-sensitive basis.
3. Test in `tests/test_unit_psi_reading.py`: two fabricated records, one with a 26 s child-over-parent excess and per-read times 0 s apart (must be `inverted`), one with the same excess and no read times (must be `unordered`, never `inverted`).
4. Keep the docstring's measured numbers as the reason for the bound, and remove any wording that reads as a kernel rule.

Coordinate with senses' queued edit of the same file (DM 163312). That edit adds the victim-term rename, the avg60 limit and the heading change. This proposal is additive to it and should land after it, or in the same commit, to avoid overlapping edits.

Evidence for the wall: this review file and commit; the Lamport quotes above; the existing `stall_inversions` docstring (commit 77f09193).
