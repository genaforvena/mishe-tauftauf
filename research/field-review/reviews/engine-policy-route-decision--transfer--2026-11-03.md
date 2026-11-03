---
field: engine-policy-route-decision
angle: transfer
date: 2026-11-03
status: discarded
owner: genome
---

# engine-policy-route-decision × transfer — congestion recovery evidence does not transfer to quota admission

## What we read

IETF RFC 9002, *QUIC Loss Detection and Congestion Control*, May 2021, Sections 7.6.1–7.6.2: <https://www.rfc-editor.org/rfc/rfc9002.html>. The standard defines persistent congestion from at least two acknowledged-as-lost ack-eliciting packets, a minimum time span between their send times, no acknowledged packets between them, and a prior RTT sample. It explicitly avoids using consecutive probe-timeout events because application traffic patterns affect those timeouts. When persistent congestion is established, the congestion window is reduced to its minimum. This reading concerns the QUIC transport's network-loss signal, not provider quota or application admission.

## What this plant already does

`src/mishe_tauftauf/engine_policy.py` classifies fresh account quota windows as healthy, reserve, or depleted; missing or non-fresh account evidence is `hold`. `decide()` keeps a healthy current route and may immediately choose another healthy candidate when the current route is reserve or depleted. Its inputs contain account quota snapshots and reservations, not request outcomes, packet loss, RTT samples, or a congestion-event history.

## What is new here

RFC 9002's persistence condition is not a generic rule to delay every negative decision: it combines repeated loss events, elapsed time, intervening acknowledgments, and a prior RTT sample. Those observations distinguish transient transport loss from persistent network congestion. The route policy has no corresponding event stream, and its fresh `depleted` status is quota evidence rather than a noisy transport-loss observation. Transferring the timeout threshold would conflate different signals and could continue admitting work against a provider-reported exhausted quota.

## Discard

No transfer: `engine_policy.decide()` has no transport-loss or request-outcome history, and fresh quota depletion is not the RFC's repeated network-loss signal; adding an analogous persistence gate would weaken the existing quota boundary without evidence.
