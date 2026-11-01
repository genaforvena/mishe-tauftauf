---
field: body-perception-spool-readings-and-gaps
angle: transfer
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-spool-readings-and-gaps — transfer (2026-11-01)

## What we read

- RFC 3550, RTP: A Transport Protocol for Real-Time Applications (Schulzrinne, Casner, Frederick, Jacobson, 2003), section 6.4.1 (reception report fields "fraction lost", "cumulative number of packets lost", "extended highest sequence number received") and Appendix A.3 "Determining Number of Packets Expected and Lost". Source: https://www.rfc-editor.org/rfc/rfc3550.txt (the sections named above were read in that file; quoted text is from it).
  - The receiver computes `expected = extended_max - base_seq + 1`, `lost = expected - received`, and counts late and duplicate packets as received. Loss can therefore be negative; the spec sets the reported fraction to zero when the interval loss is negative, and says a receiver cannot tell whether packets after its last received one were lost.
  - The loss denominator is the sequence span the producer advanced, not the number of packets that happened to arrive in a window.

## What this plant already does

- The consumer queue rows carry a host sequence number. `research/body/personal-multibody.py:459-464` documents the row as `SEQ <phone clock> <kind> <fields>`, and `_QUEUE_ROW` parses it.
- The app's own drop record is read: `_SPOOL_OVERFLOW` in `research/body/personal-multibody.py:469` counts `spool overflow dropped=N`.
- The chat feed already enforces sequence continuity: `src/mishe_tauftauf/feed.py:180-191` raises `FeedError` on any non-contiguous sequence.
- The body reader already refuses to fill gaps: `_gap_records` returns `None` on a malformed record, and `_dense_unknown` is the single UNKNOWN shape (`body_perception.py` around lines 701 and 1033). Freshness is judged by the last row's age, so a silent tail is reported as stale.
- The dense baseline (`read_redmi10_dense_baseline`, `body_perception.py` from line 1048) counts rows in the last `DENSE_WINDOW_ROWS = 2000` lines. It reads the epoch from `parts[1]` and never reads `parts[0]`. It has no expected-count denominator; a window's "coverage" is rows-present over rows-in-window.

## What is new here

The dense baseline has the sequence it needs and does not use it. Its denominator is "rows in this window", which is placement-dependent. The RTP method gives a second, independent accounting per window:

- `seq_expected = last_seq - first_seq + 1` over the window,
- `seq_received = distinct sequence numbers seen`,
- `seq_lost = seq_expected - seq_received`, reported as a count and not clamped,
- `seq_dup = rows - distinct`, so that a negative loss is named as duplicates rather than hidden, as RFC 3550 requires.

Measured on the real frozen queue, read as bytes (not remembered):

- File `/home/mesh-home/android-body/.mishe-tauftauf/body/queues/redmi10.observations.log`, 34,515,199 bytes, mtime 2026-10-23 21:13. Tail of 262,144 bytes, first partial line dropped, last 2,000 rows (seq 344487 to 346486).
- Parse failures 0. Expected by seq span 2000, distinct received 2000, lost 0, duplicates 0, non-increasing steps 0.
- `grep -c 'spool overflow'` over the whole file: 0. So the app's own drop record is absent in this file.

Limits, stated plainly:

- The queue's sequence is host-assigned as rows are appended. A gap can therefore only show host-side loss after numbering, which is rare by construction. The check does not see phone-side loss before the host numbered a row. That loss is what the app's `spool overflow` record is for, and it reads 0 here. Both checks are needed, and neither proves the phone observed a light value that it never sent.
- RFC 3550's own caveat applies: a silent tail after the last sequence number is invisible to a span count. The plant already handles that case with the row-age freshness check, so the new count should not be read as "no loss" while the queue is stale.
- Nothing in the frozen window was lost, so the check cannot be shown to detect loss from a live window. The detector must be exercised on a synthetic window with a removed row, and on a duplicate row, before it is trusted. This review does not run that test; it is the acceptance measure for senses.
- Mapping RTP's packet loss onto body rows is an analogy. The mechanism (expected from a producer counter, received from arrivals, duplicates clamped and named) transfers. The meaning of a lost row for a human body reading does not, and this review does not claim it does.

## Application

target: src/mishe_tauftauf/body_perception.py

Proposal for senses (the owner of `body_perception.py`). Not a change made in this turn, because the organ is senses' and is dirty in the checkout.

- In `read_redmi10_dense_baseline`, parse `parts[0]` as an integer sequence for each row in the window. Add `seq_expected`, `seq_received`, `seq_lost` and `seq_dup` to the returned summary, and to the `details` line as labelled counts beside the existing `window=` and `rows=` fields.
- Keep the verdict unchanged. A sequence gap is a labelled count, not an inferred value and not a reason to fill or reweight rows. Rows that fail to parse keep the existing skip behaviour and are counted separately as `seq_unparsed`.
- Negative loss (duplicates) is reported as `seq_dup` and not clamped to hide it.
- Acceptance: a unit test with a synthetic window that has one removed sequence number reports `seq_lost=1`; one with one repeated row reports `seq_dup=1` and `seq_lost=0`; a contiguous window reports 0 and 0. Then `py_compile`, the existing body_perception tests, and the dense-baseline read against the frozen queue, with the frozen-window numbers above (`seq_lost=0 seq_dup=0`) as the expected output.
- Trigger for the live read: none needed. The frozen window is the regression fixture.

## Route

Addressed to senses, with this file and the commit as evidence. Genome does not need to act: the change lives in senses' organ, and it goes live only through a reviewed snapshot activation, which is not claimed here.
