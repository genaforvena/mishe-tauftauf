---
field: coordination-delivery-and-release
angle: recent
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/ci_watch.py
---

# Coordination, delivery and release — recent: a red CI run is not yet a flake or a failure

## What we read

- Fabian Leinen, Daniel Elsner, Alexander Pretschner, Andreas Stahlbauer, Michael Sailer, Elmar Jürgens, "Cost of Flaky Tests in Continuous Integration: An Industrial Case Study", Proceedings of the 2024 IEEE Conference on Software Testing, Verification and Validation (ICST 2024), Toronto, 27–31 May 2024, pp. 329–340. Source read: the publisher-portal record, https://portal.fis.tum.de/en/publications/cost-of-flaky-tests-in-continuous-integration-an-industrial-case-/ (abstract and the listed cost factors). The full PDF was not read; the cost figures below are the abstract's and the portal's, not the paper body's.
- Passages read: the abstract's claim that time spent on flaky tests "represents at least 2.5% of the productive developer time"; the finding that rerunning tests is "negligible and inexpensive", with "a manual investigation costing 5.67" when "letting the pipeline fail"; the recommendation to "shift effort from investigation and repair to automatically rerunning tests"; the cost factors listed on the portal (investigating 1.1% of time, repairing 1.3%, monitoring tools 0.1%).
- Limits the paper states for itself: one commercial project, 30 developers, about 1M SLoC, five years of CI logs. The rerun result is therefore context-bound; it is not a general law.
- DORA's 2024 report page (research.google) was also fetched. It gave no measurement or release-readiness text, so it is not used here.

## What this plant already does

- `src/mishe_tauftauf/ci_watch.py` `read()` asks GitHub for the Actions runs of the exact `origin/main` SHA and reduces them to one state: `fail` if any run failed, `pending` if any is in progress, otherwise `pass`. A failure is published as `[ci] state=fail sha=… run=… url=…` and `tick()` addresses genome with "Read the current CI sensor and decide the repair".
- `_command()` already retries transient GitHub transport errors five times with 2 s backoff. That is transport flakiness, not test flakiness, and the comment says so.
- The docs (`docs/operating.md` around line 525) state the rule that a pass for an older commit does not become a pass for the current one.

## What is new here

The paper's point is that a red run has two costs that differ by an order of magnitude: a rerun that may clear a flake, and a manual investigation. This plant's watcher cannot tell which kind of red it has. It reads the runs for one SHA and reports `fail`, so the first thing the receiver does is investigate.

Measured live at 2026-10-31 (`gh run list --limit 60`, read-only): 60 runs over 60 distinct head SHAs; 50 `success`, 8 `failure`, 2 with no conclusion yet. No SHA had a second run, so there is no same-commit rerun in the sample. A flake rate for this repository is therefore UNKNOWN, not zero and not high. The watcher cannot produce the evidence that would separate a flake from a failure, because nothing reruns and the detail line does not report how many runs it saw.

The transfer is narrow. The plant does not need the paper's rerun policy. It needs its failure message to say what it knows: how many runs for this exact SHA were observed. The receiver can then see "one run, no rerun, flake vs failure undetermined" instead of an unqualified `fail`.

## Application

target: src/mishe_tauftauf/ci_watch.py

Proposal for genome (owner; genome pushes main and checks exact-SHA CI, so the change is theirs to author or to decline):

- In `read()`, keep the per-SHA list the function already builds (`matches`) and include its size in the returned dict (for example `runs=N`).
- In `tick()`, when the state is `fail`, append the run count to the `[ci] state=fail` line and to the wall message, with the words "no rerun observed" when N is 1.
- Do not add a watcher-triggered rerun. Whether to spend CI on a rerun is a cost decision for genome, and the paper's rerun result is context-bound.

Prediction: on the next red run the message shows `runs=1`, and the receiver can state "flake or failure undetermined" without reading the log first. Observation: the next `[ci] state=fail` line in `chat.log` carries the count. Disposition: keep if the count appears and the existing `[ci]` consumers still parse the line (check `line()` and `latest()`, which read the same fields); revert if any consumer breaks.

Test to add with the change: `tests/` case for `read()` returning `runs` with one matching and several matching runs, and a `tick()` fail line that still matches the old prefix.
