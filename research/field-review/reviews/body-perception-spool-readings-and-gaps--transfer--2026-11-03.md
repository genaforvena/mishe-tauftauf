---
field: body-perception-spool-readings-and-gaps
angle: transfer
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-spool-readings-and-gaps — transfer (2026-11-03)

## What we read

- IETF RFC 9002, "QUIC Loss Detection and Congestion Control," J. Iyengar & I. Swett eds., May 2021 (Standards Track), 42 pages. Source: https://www.rfc-editor.org/rfc/rfc9002.txt — read in that file §§1–4, §6 Loss Detection (6.1 Acknowledgment-Based Detection with 6.1.1 Packet Threshold and 6.1.2 Time Threshold; 6.2 Probe Timeout with 6.2.1 Computing PTO), §7.6 Persistent Congestion, and Appendices A.2–A.10 via the text render above (lines 88–350+ fetched, §6 read fully). Canonical DOI via https://www.rfc-editor.org/info/rfc9002.
  - §4.2 Monotonically Increasing Packet Numbers: QUIC packet numbers are strictly increasing and encode transmission order; "Packet numbers never repeat … Packet numbers are sent in monotonically increasing order" (§3) and retransmitted frames use a new packet number, removing retransmission ambiguity present in TCP.
  - §4.3 Clearer Loss Epoch: QUIC loss epoch starts on a loss and ends when any packet sent after the start is acknowledged; TCP waits for the gap to fill, so one window reduction per RTT with loss vs TCP's one across multiple RTTs.
  - §6.1 Acknowledgment-Based Detection: two independent declaration rules for the same packet number space.
    - §6.1.1 Packet Threshold: a packet is declared lost when a later packet number ≥3 ahead (kPacketThreshold=3, Appendix A.2) is acknowledged — packet-count reordering bound.
    - §6.1.2 Time Threshold: a packet is declared lost when it has been sent longer ago than 9/8 × max(smoothed_rtt, latest_rtt) (kTimeThreshold) without acknowledgement — time-based reordering bound after accounting for ack delay (§4.6).
  - §6.2 Probe Timeout: PTO replaces RTO/TLP (§4.7). PTO timer = smoothed_rtt + max(4×rttvar, kGranularity) + max_ack_delay (§6.2.1); on PTO expiry without ack-eliciting packets declared lost, the sender sends probe packets and may temporarily exceed cwnd (§4.7/§6.2.4) without collapsing cwnd — collapse only on persistent congestion (§7.6). Solves "single packet loss at the tail does not indicate persistent congestion" and tail-loss unrecoverable by threshold alone.
  - Appendix A.2 constants: kPacketThreshold=3, kTimeThreshold=9/8, kGranularity, kPersistentCongestionThreshold=3; A.10/A.8 the detection timer ordering (packet/time threshold before PTO).
  - §5 RTT estimation (§5.1–5.3): smoothed_rtt/rttvar/min_rtt from newly-acknowledged largest packet, corrected for peer ack delay (§4.6) — the time threshold's input.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py:767-845` reports freshness as a binary gate `FRESH_SECONDS = 30.0` / `DENSE_FRESH_SECONDS = 30.0` via `_row_status`; no RTT/smoothed_rtt/rttvar accumulation and no per-packet send/ack timestamps — `now - last_epoch` is wall-minus-phone, not RTT.
- `read_redmi10_delivery_gaps` (956–1134) / `_gap_records` (999–1035) counts consumer class-C halt records over `GAP_WINDOW_SECONDS = 86400.0` as a 24 h halt count + `_worst_batch_delay` from `excursion-log-redmi10.txt`; not a per-sequence loss declaration.
- `read_redmi10_dense_baseline` (2013–2181) reads `DENSE_TAIL_BYTES = 262144` (~2500 rows, ~3.5 h at 5 s, `DENSE_WINDOW_ROWS = 2000` ~2.8 h) and `_dense_sequence_accounting` (1977–2010) does RFC 3550 Appendix A.3 expected-vs-received accounting: `expected = max−min+1`, `received = distinct`, `lost = expected−received`, `dup = rows−distinct`, `unparsed`, `steps_back` (seq ≤ previous). No kPacketThreshold/kTimeThreshold split; loss is span minus distinct without separating reorder-vs-loss by count or by elapsed time, and a silent tail after the last sequence is invisible (RFC 3550 caveat cited in the 2026-11-01 transfer).
- `research/body/personal-multibody.py:459-469` documents spool rows as `SEQ <epoch> <kind> …` and `_SPOOL_OVERFLOW` as `spool overflow dropped=N` — the host-assigned sequence cannot see phone-side loss before numbering; both signals are needed.
- Prior reviews for this field complement without duplicating: 2026-11-01 mechanism (Akidau Watermarks — high-water / late-row count, 512/2000 25.6% behind high-water, max lag 100 s), 2026-11-01 transfer (RFC 3550 span accounting now implemented), 2026-11-03 recent (Li et al. AoI Δ(t)=t−S_i endogenous vs exogenous + threshold), 2026-11-03 misread (Lamport → partial vs total, clock condition C1/C2+IR1/IR2, anomalous behavior), 2026-11-03 critique (Dean & Barroso Tail at Scale — 95%-vs-100% p99 decomposition, hedged/tied/probation).

## What is new here

RFC 9002's contribution over the RFC 3550 span count already embodied is that one threshold is not enough — loss declaration needs both a packet-count and a time rule, and tail loss needs a probe timer that does not conflate "no ack yet" with "congested."

1. **One span hides two reasons.** §6.1 keeps packet threshold (3 packets beyond) and time threshold (9/8 × RTT) as independent declarations because a gap can be reordering or loss, and reordering has both a count shape (how many later packets arrived) and a time shape (how long has passed for this network's RTT). The plant's `_dense_sequence_accounting` reports `lost = expected − received` and `steps_back` as separate counters but declares nothing by either rule — a window with `lost=1` can be one missing sequence with 1999 later arrivals (threshold met) or one missing at the edge with zero later arrivals (tail, threshold not met). The current summary does not distinguish them, reproducing TCP's conflation that RFC 9002 §4.2/§6.1 explicitly splits.

2. **Wall clock is not RTT.** §6.1.2's time threshold is max(smoothed_rtt, latest_rtt) scaled by 9/8 after subtracting peer ack delay (§4.6/§5) — a path RTT with variance. The plant's `break_seconds = now − last_epoch` (phone clock vs host wall) and `window_seconds = last_epoch − first_epoch` are compared as epoch order in host sequence order (Lamport misread 2026-11-03: up to 100 s max lag, 512/2000 rows out of order). Without a measured RTT/ack-delay, a time threshold built from `now − epoch` repeats that cross-clock error and would declare loss/lateness on clock skew rather than delivery delay.

3. **Tail invisibility needs a probe, not a larger window.** §4.7/§6.2 states the PTO problem verbatim: "a single packet loss at the tail does not indicate persistent congestion" but threshold detection cannot fire there (no later packets to cross the threshold). QUIC therefore arms PTO = smoothed_rtt + max(4×rttvar, granularity) + max_ack_delay and on expiry sends probes that may exceed cwnd (§6.2.4) without collapsing it (§7.6). The plant's dense baseline has the same tail problem (RFC 3550 §6 caveat + Lamport 100 s high-water lag + Tail at Scale 95%-vs-100%): a silent tail after the last sequence is invisible to span counts and to `steps_back`, and widening `DENSE_TAIL_BYTES` does not fix it — only a bounded probe/re-read after `max_ack_delay + RTT` would distinguish "quiet" from "stalled." The plant currently has no PTO analogue and no probe vocabulary.

4. **Loss epoch clarity.** §4.3's epoch (starts on loss, ends when any packet sent after the start is acknowledged) gives one cwnd reduction per RTT with loss. The plant has no epoch — `_dense_sequence_accounting` is per-window, stateless, and the gap count resets per 24 h — so a single reordering episode spanning two windows would be counted twice or not at all.

## Application

target: src/mishe_tauftauf/body_perception.py

Proposal for `senses` (owner of `body_perception.py`). Docstring-only, no behaviour, revertible by `git revert`. Complement, not edit, to the existing RFC 3550/Akidau/Lamport/Li/Tail notes.

- Add a boundary paragraph beside `DENSE_TAIL_BYTES` / `DENSE_WINDOW_ROWS` / `DENSE_FRESH_SECONDS` / `FRESH_SECONDS` and `read_redmi10_dense_baseline` / `_dense_sequence_accounting` / `read_redmi10_delivery_gaps` (around lines 115–155, 1038–1046, 1977–2011, 2013–2035), citing RFC 9002 §§6.1.1/6.1.2/6.2/4.7 and Appendix A.2/A.10 and §§4.2–4.3/§5, stating:
  - RFC 9002 declares loss by two independent rules: packet threshold (kPacketThreshold=3 packets beyond, §6.1.1) and time threshold (9/8×max(smoothed_rtt,latest_rtt) after ack-delay correction, §§6.1.2/4.6/5) — packet-count vs time-based reordering bounds. This module reports `expected/received/lost/dup/steps_back` per RFC 3550 span without either threshold; a `lost=1` with many later arrivals vs a tail `lost=1` with none are not distinguished, and `steps_back` alone does not encode either threshold.
  - Wall-minus-epoch (`now − last_epoch`, `last_epoch − first_epoch`) is not RTT (§5); without a measured smoothed_rtt/rttvar/ack-delay, a time threshold built from these clocks would reproduce the Lamport C1/C2 100 s / 25.6% high-water divergence already typed beside `FRESH_SECONDS`.
  - Silent tail after the last sequence is invisible to both thresholds (§4.7/§6.2: "single packet loss at the tail does not indicate persistent congestion" and needs PTO, not a window widening). No PTO/probe analogue exists here; `_gap_records`/`_worst_batch_delay` observe halts exogenously after the fact, and enlarging `DENSE_TAIL_BYTES` does not make a tail loss detectable — it would require a bounded re-poll after `RTT+max_ack_delay` as a probe with explicit non-collapse semantics (§§6.2.4/7.6). Not wired.
  - Loss-epoch discipline (§4.3) absent: per-window, stateless accounting with no epoch start/end, so multi-window reordering/loss has no single-epoch suppression analogue. If a re-read probe or a threshold declaration is ever wired, name the kPacketThreshold/kTimeThreshold/PTO values, the RTT source, and the caller here and re-check this note.
- Keep verdict unchanged; loss stays a labelled count, not an imputation and not a reason to reweight rows (gap-stays-gap, 2026-11-01 discard).
- Tests (proposed to `senses`; owner writes, not this review): `python -m py_compile src/mishe_tauftauf/body_perception.py` exits 0; `python3 research/field-review/field_review.py check` passes; `rg -n "RFC 9002|kPacketThreshold|PTO" src/mishe_tauftauf/body_perception.py` hits the new note and nowhere else in `src/`; existing `body_perception` tests unchanged.

## Route

Addressed to `senses` with this file and the commit as evidence. Activation is docstring change only; no new `sense.*` verdict, no I/O, no sampling knob.

