---
field: native-wake-recovery
angle: mechanism
date: 2026-11-01
status: discarded
owner: field-review
---

# native-wake-recovery × mechanism — discarded

## What we read

- Primary source attempted: Hayashibara, Défago, Yared, Katayama, "The φ accrual failure detector", JAIST research report IS-RR-2004-010 (SRDS 2004). Intended URL: http://www.jaist.ac.jp/~defago/files/pdf/IS_RR_2004_010.pdf (fetched 2026-11-01). Result: the host answered with a 301 redirect to an unrelated site (`coord.c.titech.ac.jp`), which was not followed. Alternate copies were unreachable: `ddg.jaist.ac.jp/pub/HDY+04.pdf` did not resolve, `oneofus.la/HDY04.pdf` returned 404, an IPSJ mirror returned 503, and a studylib copy held only the title page with no paper text.
- Secondary only, not counted as a reading: Akka's `PhiAccrualFailureDetector` API documentation (https://doc.akka.io/api/akka/2.4.1/akka/remote/PhiAccrualFailureDetector.html). It describes an implementation of the mechanism. It is not the paper, and this review does not quote it as the source's wording.

No primary text was read, so the mechanism's definition and limits are not established here.

## What this plant already does

- `src/mishe_tauftauf/native_gate.py:22-29` already separates a mid-turn busy marker from a stale one by age. Its docstring says a busy file "is a mid-turn only while it is fresh", and "a marker older than the live window is a wedged resident". `native_gate.py:241-243` computes `busy_age` for the same reason.
- `src/mishe_tauftauf/native_recovery.py:374` `classify_attempt` classifies one failed attempt from its recorded bytes. It is an evidence reading, not a suspicion level over time.

So the plant already makes a binary, window-based call on liveness: fresh or stale, live or wedged. The question the mechanism would have answered is whether a graded suspicion value, rather than a fixed live window, would classify a wake's silence better.

## What is new here

Nothing is established. The candidate idea was a graded "suspicion" for a held wake, computed from how far its age sits beyond the recent distribution of its own run times. Whether that beats a fixed live window, and what the paper says about its assumptions, depends on the primary text. Without it, any transfer is my own guess and not a reading.

## Discard

Discarded: the primary source (JAIST IS-RR-2004-010) was unreachable on 2026-11-01. The redirect and mirror failures are recorded above, and the mechanism was not read from a substitute. Trigger to revisit: a reachable primary copy of the paper, or a stable archive link, appearing in a later field-review cell attempt. Detector for the present gap: the wake-age classification in `native_gate.py` remains the only liveness reading, and no fresh-wake evidence was observed to need a graded value.
