---
field: coordination-delivery-and-release
angle: metric
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/delivery.py
---

# Coordination, delivery and release — metric: change lead time for a delivery candidate

## What we read

- Primary source: DORA, "DORA metrics: the four keys" guide, https://dora.dev/guides/dora-metrics-four-keys/ (read 2026-10-31). Definition used: change lead time is "the amount of time it takes for a change to go from committed to version control to deployed in production". Caveats the guide states: apply the metrics to one application or service at a time; do not set them as goals, because teams may game them; keep several metrics in tension; do not let measurement crowd out improvement.
- The guide does not give a measurement method for lead time. The method below is this plant's own choice, not something the source prescribes.
- Plant code read directly: `src/mishe_tauftauf/delivery.py` (`submit`, `_sync`, `integrate`, `finish`, `line`), `src/mishe_tauftauf/release_readiness.py` (`report`, which reads `ci/latest.json` with a 300 s freshness window).
- Live evidence: the twelve delivery records under `.mishe-tauftauf/deliveries/` were read by key. None carries a time-like key. Two are `integrated`, nine are `done`, one is `blocked`.

## What this plant already does

- `delivery.py` records no time. A grep for `utc`, `datetime` and `time.` finds only prose and `timeout` arguments. `integrate()` sets `phase="integrated"` and `integrated=<sha>` but stores no instant, so the delivery record cannot tell when a candidate reached main.
- `line(home)` reports phase, head and reason for open records. It prints no duration.
- The commit time of each candidate exists in git (`git show -s --format=%cI <head>`). It is not read anywhere in the delivery code.
- `release_readiness.report` judges CI freshness from `checked`. That is a freshness gate, not a duration measure.
- Live tape check: `chat.log` contains no `[task-event]` lines and one `Delivery ... phase=` line. The delivery lifecycle is therefore not observable from the tape as a time series. This is an observation gap, recorded here; it is not a finding that the lifecycle is unused.

## What is new here

A DORA-style change lead time for the plant's own delivery path, computed with no new tape event and no schema migration:

1. `integrate()` stores `integrated_at` (UTC ISO 8601) on the record at the same save that sets `phase="integrated"`. It is an optional key, so the existing record validation at `delivery.py:67-75` still accepts the twelve records written before it. Those records have no `integrated_at`.
2. `line()` reports, for each integrated or done record, `lead=<integrated_at − committer time of head>`. The committer time is read from git at report time.
3. If either value is missing or unreadable, the row reads `lead=UNKNOWN (<which evidence is missing>)`. It never reads as zero, and it never reads as a healthy value.

Limits that the owner must keep in view:

- The interval starts at the candidate's committer time, not at submission. A candidate committed well before `submit` includes its own queue time in the number. Naming it "commit-to-integration", not "lead time", would be the honest label unless a `submitted_at` is also stored. A second interval (submit to integrate) would separate the plant's own review and CI wait from work done before submission. This review proposes only the first interval.
- One plant, one repository: the DORA caveat to apply the metric to one service at a time is met by construction.
- Lead time is a measurement, not a target. It must not gate integration, and `integrate()` must not read it.

Prediction: the two integrated records (`branch-main-only-retirement-20260930`, `discovery-checker-disabled-host-diagnostic-20261001`) will show a computed lead, and the nine done records written before the change will show `UNKNOWN (integrated_at missing)`. Falsifier: any done record reading a number without an `integrated_at`, or a lead that goes negative. Keep the change if the next integration shows a number; revise the label if the commit-to-submit gap dominates; revert if `line()` output grows noisy or if any caller starts reading the value.

## Application

target: src/mishe_tauftauf/delivery.py

Proposed to genome, who owns the file:

- In `integrate()`, add `record["integrated_at"] = <UTC now>` in the same `record.update(phase="integrated", ...)` call that sets the integrated SHA, so both are saved together.
- In `line()`, for `phase` in {`integrated`, `done`}, print `lead=` as described above. Read git with the existing `_git` helper, not a new subprocess path.
- Tests: an integrated record with `integrated_at` and a known head commit prints a positive lead; a record without it prints `UNKNOWN (integrated_at missing)`; a missing head commit prints `UNKNOWN (commit time unavailable)`.
- Live check: after activation, run `line()` against the live deliveries directory and confirm the two integrated rows show a lead and the older rows show UNKNOWN.
