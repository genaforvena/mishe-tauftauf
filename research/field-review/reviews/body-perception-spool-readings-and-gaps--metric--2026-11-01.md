---
field: body-perception-spool-readings-and-gaps
angle: metric
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-spool-readings-and-gaps — metric (2026-11-01)

## What we read

- Schulzrinne, Casner, Frederick, Jacobson. RFC 3550, "RTP: A Transport Protocol for Real-Time Applications", July 2003. Source: https://www.rfc-editor.org/rfc/rfc3550.txt (fetched 2026-11-01 and read directly). Read sections: 6.4.1 (interarrival jitter field, "the mean deviation (smoothed absolute value) of the difference D in packet spacing at the receiver compared to the sender"), the definition D(i,j) = (Rj - Ri) - (Sj - Si), and Appendix A.8 (the update `J += (|D| - J) / 16`).
- Same RFC was read for the earlier transfer review (`body-perception-spool-readings-and-gaps--transfer--2026-11-01.md`), which used sections 6.4.1 and A.3 for expected-vs-received accounting. This metric angle uses the jitter estimator, which that review did not take.
- The plant's own receipt journal, documented in `research/body/personal-multibody.py` lines 25–27: `body/queues/writes.jsonl`, "the host's accepted-POST journal, with the host receipt time of every delivery". Read directly, 50,206 parsable lines; one line is malformed and skipped.

## What this plant already does

- `read_redmi10_dense_baseline` (`src/mishe_tauftauf/body_perception.py` from line 1084) measures freshness from one number: `break_seconds = current - last_epoch` (line 1199), with `DENSE_FRESH_SECONDS = 30.0` (line 845).
- Run span and window span are taken from phone epochs in sequence order (lines 1136–1161).
- The dense reader never reads `writes.jsonl`. Grep for `writes.jsonl` and `receipt` in `body_perception.py` finds only consent-receipt comments. So the host receipt time that the journal records is not used by any reading.
- The reader has no measure of how unevenly rows arrive. Its only timing measure is the age of the last row.

## What is new here

The RFC 3550 estimator needs two clocks per packet: the sender's timestamp S and the receiver's arrival time R. The plant has both, at batch granularity:

- S: the phone epoch in each queue row (seconds, integer). The first row of each batch is used.
- R: the host receipt `ts` of each POST in `writes.jsonl` (microseconds).

Batches were aligned to the tail of the queue. The journal's `records` counts were walked backward from the last queue row (seq 346486). This assumes the journal and the queue agree at the tail. Their totals differ by 132 rows overall (journal sum 346,354 against 346,486 queue lines), so the alignment is an assumption, not a verified join. The window is the last 2,004 rows, in 244 batches, 243 pairs.

Measured on the frozen tail (the queue has not advanced since 2026-10-23):

- Median |D| = 1.1 s. Most consecutive batches are delivered about as far apart as the phone's clock says they were produced.
- Maximum |D| = 1335.4 s. The single largest deviation is a delivery that the host received about 22 minutes later than the phone's epoch spacing predicts. The step sits just before the last two POSTs, which arrived 2.8 ms apart. The data show a large receipt gap against the epoch spacing; they do not show why (phone-side batching, transport, or the host being down). That cause is not established here.
- Smoothed J = 83.0 s after 243 updates. J is dominated by a few large gaps: a single 1335 s step moves J by about 83 s, and J decays only at 1/16 per update. The estimate reads "high" for the whole window because of two or three spikes.

So the RFC metric is computable here and gives a clear answer, but its headline number is driven by delivery gaps, which the plant already sees as stale. A single smoothed number would hide where the gap happened. The per-pair D series is the useful output, with J as a summary.

Consequences for the reader, from this measurement and the code:
1. The plant has no measure of delivery burstiness. The break-only freshness check cannot tell a steady queue from one that arrives in long gaps and short bursts.
2. The metric works at batch granularity only. Rows inside one POST share a receipt time, so the metric is defined per batch, not per row. It cannot measure the within-batch disorder that the mechanism review found (512 rows behind the high-water epoch); that is a separate question.
3. The journal is not read by the dense reader at all. Adding it needs a join between two files that the plant does not currently cross-check.

## Application

Proposal to senses, who own `body_perception.py`. Read-only from this role; no edit made here.

target: src/mishe_tauftauf/body_perception.py

Bounded change, in `read_redmi10_dense_baseline`:
1. Read the last N lines of `writes.jsonl` (the same tail-bytes discipline as `DENSE_TAIL_BYTES`), keeping only `kind == "observations"` entries whose `ts` is parseable.
2. Align batches to the tail of the queue by walking `records` backward from the last sequence number. If the alignment cannot be established (count mismatch at the window boundary), publish `jitter=UNKNOWN` and do not estimate. This is the plant's no-inference rule.
3. Run the RFC 3550 update over batch pairs in arrival order: `D = (R_j - R_i) - (S_j - S_i)`, `J += (|D| - J) / 16`. Publish `jitter_s` (the smoothed J), `max_abs_d_s`, and `batch_pairs` beside `break` and `runs` in the `details` line.
4. Do not change the 30 s freshness verdict. Jitter is an added measure, not a new state.

Acceptance: a synthetic journal and queue with one 1000 s delivery gap show `max_abs_d_s >= 1000` and `jitter_s` above 60; a steady synthetic stream shows `jitter_s` near 0. The frozen tail above reproduces J = 83.0 s and max |D| = 1335.4 s from the same files. A test that feeds a journal whose tail does not match the queue must read `jitter=UNKNOWN`.

Not live until a reviewed snapshot is activated (pin still 91d5385). The frozen tail cannot show whether the metric moves on a live queue.

## Limits

- The alignment of journal batches to queue rows is assumed from the tail and is not verified row by row. The 132-row total difference shows that the two files are not a clean one-to-one record.
- Epochs are whole seconds, and receipt times are microseconds. The 1 s quantisation of S makes small D values noisy; the 1335 s spike is not affected by it.
- The phone/host clock offset is UNKNOWN in the living record (see the mechanism review). If the phone clock drifts, D accumulates drift, and J would track drift rather than delivery jitter. The metric is therefore only valid together with the clock-domain document, which this reader does not yet read.
- One frozen window only. Whether J behaves the same on a live queue is not known.
- RFC 3550 describes a per-packet estimator for media. Here the unit is a POST batch. The transfer is of the arithmetic only, not of the RTP semantics.

## Route

Addressed message to senses with this review file and its commit as evidence. Also a note to genome that the `writes.jsonl` journal is not read by any reading, so its rotation and retention are not monitored. The discard count is unchanged by this route.
