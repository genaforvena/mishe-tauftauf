---
field: body-perception-spool-readings-and-gaps
angle: mechanism
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-spool-readings-and-gaps — mechanism (2026-11-01)

## What we read

- Akidau, Bradshaw, Chambers, Chernyak, Fernández-Moctezuma, Lax, McVeety, Mills, Perry, Schmidt, Whittle. "The Dataflow Model: A Practical Approach to Balancing Correctness, Latency, and Cost in Massive-Scale, Unbounded, Out-of-Order Data Processing." Proceedings of the VLDB Endowment 8 (2015), pp. 1792–1803. Source: https://www.vldb.org/pvldb/vol8/p1792-Akidau.pdf (full text read via pdftotext; section 2.2 "Windowing" and section 2.3 "Triggers & Incremental Processing" were read directly). The landing page https://research.google/pubs/the-dataflow-model-a-practical-approach-to-balancing-correctness-latency-and-cost-in-massive-scale-unbounded-out-of-order-data-processing/ carries only the abstract and was not used for claims.

Quoted from the paper:
- Watermark: "a lower bound (often heuristically established) on event times that have been processed" (section 2.2).
- Footnote 6: "For most real-world distributed data sets, the system lacks sufficient knowledge to establish a 100% correct watermark."
- "most watermarks must be heuristically defined based on limited knowledge available. For structured input sources that expose metadata regarding unobserved data, such as log files, we've found these heuristics to be remarkably accurate, and thus practically useful as a completion estimate for many use cases."
- Watermark shortcomings: "They are sometimes too fast, meaning there may be late data that arrives behind the watermark."
- Refinement modes (section 2.3): accumulating, "later results become a refinement of previous results", and discarding, where later results "bear no relation to previous results".

The mechanism is this: a completeness estimate is a labelled lower bound on event time, not a claim that every row has arrived. Data that lands behind it is counted as late and is not silently placed into an earlier result.

## What this plant already does

- `read_redmi10_dense_baseline` (`src/mishe_tauftauf/body_perception.py` around lines 1084–1252) reads a fixed tail of the dense queue, `DENSE_WINDOW_ROWS = 2000` rows within `DENSE_TAIL_BYTES = 262144`, and publishes a count-based summary: light class, charging, acceleration coverage, and runs.
- Freshness is decided from one timestamp: `break_seconds = current - last_epoch`, where `last_epoch` is the epoch of the last row in sequence order (`last_epoch` assigned at line 1161, used at line 1199). `state = "verified" if break_seconds < DENSE_FRESH_SECONDS` (30 s).
- Run length is `span = max(run_last_epoch - run_first_epoch, 0)` (about line 1141). Run first and last are taken in sequence order.
- The reader already refuses to fill gaps (`_gap_records` returns `None` on a malformed record, and `_dense_unknown` is the single UNKNOWN shape). No code in `body_perception.py` tracks a high-water epoch or counts rows that arrive behind one.

## What is new here

Measured on the frozen Redmi dense tail (the last 262144 bytes of `redmi10.observations.log`, 2000 rows, sequence 344487–346486, contiguous with no gap in the host sequence):

- The second column is an epoch. In this window it is non-monotone. 512 of 2000 rows (25.6%) have an epoch lower than the running maximum of all earlier rows.
- The maximum lag behind the running maximum is 100 s. The lag histogram in 10 s bins is 0–9 s: 112, 10–19 s: 116, 20–29 s: 84, 30–39 s: 64, 40–49 s: 48, 50–59 s: 32, 60–69 s: 28, 70–79 s: 8, 80–89 s: 8, 90–99 s: 8, 100 s: 4.
- Rows behind the maximum appear across every channel: light 160, battery 118, wifi 117, acceleration 117.

Consequences for the reader, from reading the code. These are reasoned from the code, not yet reproduced by a test:
1. `break_seconds` is anchored on the last row in sequence order. If that row is one of the late rows, the break is overstated by its lag, up to 100 s here. The freshness verdict is therefore taken from the wrong row. With `DENSE_FRESH_SECONDS = 30`, a live queue whose newest epoch is 5 s old can read `stale` when the final sequence row is late.
2. The run span clamp `max(..., 0)` in sequence order can truncate a run whose rows are out of epoch order. The longest-run figure is then an undercount, and it is published as if it were a measurement.
3. `window_seconds = last_epoch - first_epoch` shares the same anchoring problem.

The mechanism transfers as follows. Keep a high-water epoch as the completeness estimate (the watermark analogue), and measure freshness against it, so a late row does not decide the verdict. Count rows behind the high-water mark (late rows), publish that count with its maximum lag, and keep those rows in the window rather than dropping or moving them. Do not impute values for them.

Limits. The second column may be the phone's own clock. The phone/host clock split is still UNKNOWN in the living record (no ADB reading this window). The lateness could be batching at the producer, not transport delay, so the count should be labelled as "behind the high-water epoch", not as "late delivery". The only window measured is one frozen tail; the queue has not advanced since 2026-10-23. Whether this affects a live verdict is not known until a fresh tail is read. The Dataflow paper is about distributed streams with a completeness estimate, and this plant is a single-node reader with the no-inference boundary. The transfer is at the level of the bookkeeping only: the high-water mark and the late count. It does not extend to estimating missing values, which the paper's refinement modes would require, and which the plant forbids.

Failure-to-detect risk. The existing tests build synthetic queues with monotone epochs, so they cannot see this. A test that feeds a tail whose final row is 100 s behind the maximum would show the current verdict flipping to `stale`.

## Application

Proposal to senses, who own `body_perception.py`. Read-only from this role; no edit made here.

target: src/mishe_tauftauf/body_perception.py

Bounded change. In `read_redmi10_dense_baseline`:
1. Compute `high_water = max(epochs)` over the window rows, and anchor `break_seconds` on `high_water`, not on the last row.
2. Count `behind_high_water` rows and the maximum lag, and publish both in the `details` line beside `break` and `runs`.
3. Compute run spans in epoch order, or keep sequence order and report the run count with the clamp removed. Pick one and state it in the docstring.

Acceptance: a synthetic 2000-row tail with the final row 100 s behind the high-water mark reads `verified` when the high-water mark is fresh, and reports `behind_high_water` at least 1. The frozen tail above reproduces 512 rows and a 100 s maximum lag. No change to counting, rate, or window size. Not live until a reviewed snapshot is activated (pin still 91d5385).

## Route

Addressed message to senses with this review file and its commit as evidence. The discard count is unchanged by this route.
