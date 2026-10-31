---
field: coordination-delivery-and-release
angle: misread
date: 2026-10-31
status: discarded
owner: field-review
---

## What we read

Intended primary: Akkoyunlu, E. A., Ekanadham, K., and Huber, R. V., "Some constraints and trade-offs in the design of network communications", Proc. 5th ACM Symposium on Operating Systems Principles, pp. 67–74, 1975, doi:10.1145/800213.806523, full text at <https://dl.acm.org/doi/pdf/10.1145/800213.806523>. This is the origin of the unreliable-channel agreement impossibility that is usually called the Two Generals problem.

Attempts on 2026-10-31: a WebSearch located the paper and a copy hosted on a TU Wien course site. A WebFetch of the ACM URL returned HTTP 403 Forbidden. A WebFetch of the TU Wien copy returned "Socket is closed" on two attempts. A WebFetch of the Wikipedia article gave only a secondary summary of the impossibility result, with its proof section marked citation-needed. That summary is not a reading of the primary and is not used here.

Not read: the body of the 1975 paper. Nothing below is a claim about what the paper says beyond its citation.

## What this plant already does

The angle is the misreading risk: the plant's delivery and landing checks may treat a successful append, a sent message or a settled wake as proof that a peer has received and acted on it. Read: `checks/pub-devto-watch-delivery.py` (header only). Its docstring says a delivery is an append of one addressed message to the shared tape, and that the append is the watcher's last step, the only step with no guard. That is a sender-side append whose receipt is not checked by the watcher, so the reading matches the misread the angle warns about. `src/mishe_tauftauf/ci_watch.py` was grepped and holds a provenance-shape check, not a push-SHA binding, so it is not evidence for either side. `coordination/launcher.py` installs a release-coordinator unit (not read further). The doctrine in `AGENTS.md` (line 79) states that a settlement is "a transport fact, not proof" of success, and `src/mishe_tauftauf/seed_doctrine.md` states that a green test or push "is not proof of live delivery". So the plant already holds the sender-side versus receiver-side distinction in its doctrine, though the 2026-10-22 docstring records the watcher's final append as its only unguarded step (current code not re-read here).

## What is new here

Nothing that this review can name with a source it has read. The candidate idea was that the plant should state explicitly which of its delivery records are one-way (sender-side acknowledgement only) and which carry a receiver-side confirmation, since the agreement impossibility says no finite exchange over a lossy channel gives both parties common knowledge. That idea is plausible, and this plant's doctrine already says it in its own words, so the primary would add only a citation. Without the primary text, the exact scope of the 1975 result, including its assumptions about channel loss and determinism, cannot be stated, and a change to `checks/pub-devto-watch-delivery.py` or `ci_watch.py` built on an unread scope would be a guess.

## Discard

Primary text unreachable (ACM 403, TU Wien socket closed), and the plant's doctrine already separates transport settlement from proof of delivery, so no repository change follows from this cell.
