---
field: outcome-records-and-verdict-coverage
angle: recent
date: 2026-10-31
status: discarded
owner: field-review
---

## What we read

Canciani, A., Felicioli, C., Severino, F., Tortola, D., "Auditable data structures: theory and applications", arXiv:2306.01886 (cs.DC), submitted 2 June 2023, <https://arxiv.org/abs/2306.01886>, read 2026-10-31 via the arXiv abstract page only. Read: the abstract's claim that authenticated data structures prove authenticity and integrity of data but not the consistency of its history, the framing of an external auditor with no prior knowledge of the data, and the proposed audit framework. The full text was not read; the abstract does not discuss verifiable logs, verifiable maps, costs or limits, so the reading is incomplete and is not a summary of the paper's body.

## What this plant already does

Outcome records carry a sha256 of each bound evidence file and a structured `supersedes` link to earlier record digests (`src/mishe_tauftauf/wall.py:261-344`), and the bound-evidence clause refuses re-use of a bound path (`checks/bound-evidence-clause-enforcement.py`). Per-record integrity and explicit replacement are therefore already present; the open question is whether the whole outcome history is checkable.

## What is new here

Only the abstract's framing that per-item proofs do not establish history consistency. That gap is real for this plant as a question, but deciding whether the plant's outcome history can be rewritten or truncated unseen needs the full paper and a check against the actual chain, neither of which this reading provides. No change to a tracked file follows from the abstract alone.

## Discard

Discarded: the abstract-only reading gives a history-consistency question but no mechanism or check to apply; re-read the full paper or the RFC 9162 consistency-proof section in a later cell before proposing a change.
