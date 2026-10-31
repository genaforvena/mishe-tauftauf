---
field: wall-patches-and-operator-views
angle: recent
date: 2026-10-31
status: applied
owner: senses
target: src/mishe_tauftauf/wall.py
---

# Wall patches and operator views — recent: a synchronous gate can hide a contradiction from the detector behind it

## What we read

- Yanki Margalit, Nurit Cohen-Inger, Erni Avram, Ran Taig, Oded Margalit, "Governed Shared Memory for Multi-Agent LLM Systems", arXiv:2606.24535 (v1, submitted 23 June 2026, cs.AI). Source: https://arxiv.org/abs/2606.24535 and the HTML version https://arxiv.org/html/2606.24535v1.
- Read: the abstract page, and the HTML version's sections the review needs: §1 (Figure 1 caption on four governance dimensions), §5.2 and Table 1 (memory scopes), §8.4 (Contradiction, with Table 2), §9.2 (Deduplication Starves Contradiction Detection), and §10 (Limitations).
- Limits of this reading: the text was read through a summarising fetch, so quoted sentences are the extraction's wording and are not checked against the typeset PDF. The paper's workload is a live memory service (MemClaw) with a generator-made write stream, not this plant's wall. The paper is a single-service study. A search summary had claimed a "four scopes" count and a "90 of 90" figure before the HTML was read; the HTML confirms both, and the 90/90 figure is conditional (see below).

## What this plant already does

- `src/mishe_tauftauf/wall.py` `_publish_outcome` runs, under one lock, a synchronous duplicate-path check before it writes an outcome: if the evidence path is already bound it raises `outcome evidence path already bound: <path>` (around line 337). Eligibility is checked earlier and raises a different message. Outcomes can name earlier records through a structured `supersedes` field, decoded in `outcome_publication.py`.
- The refusal count is observed. The dashboard reports `OUTCOME WRITER REFUSALS: 173 in the last 24h (0 coverage denials)`, and its `last=` field shows the most recent refusal's text, which in this window was the eligibility message, not the duplicate-path one.
- `wall_patch.py` gates code changes before activation with a review, a test and a revert path. That gate is a different object: it decides on patch bytes, not on wall text.
- No semantic contradiction detector over outcomes exists in the tree that I could find. `supersedes` is a link a writer supplies; nothing in the tree compares two outcomes for contradiction. So the paper's detector-after-gate pipeline has no direct counterpart here yet.

## What is new here

The paper's concrete finding is an ordering problem. A synchronous pre-commit near-duplicate gate (§9.2) returns 409 when embedding similarity crosses a threshold, and it does so before the asynchronous contradiction detector sees the write. The paper says so directly: "the synchronous gate can reject a write before the asynchronous detector ever sees it." In its contradiction run (§8.4) 206 of 400 writes were rejected by the dedup gate before the detector, and the detector's detection rate was 98/200 = 0.490. The paper's own 90/90 supersession figure is conditional: it is the rate among the 90 fact-runs where both contradictory writes were admitted, and the authors attribute the gap "entirely" to admission, not to the detector. They also say, in §8.4, "We do not cleanly separate the two; the robust claim is the conditional one." That hedge matters: the paper does not separate a true dedup effect from prose diversity in its generator.

Transfer to this plant, stated as a hypothesis with its limits:

1. The plant's duplicate-path gate is structural (exact path equality), not semantic, so the paper's failure mode (a similarity threshold rejecting a *different* claim) cannot happen with it as written. A second outcome that cites a new evidence file and contradicts an earlier one passes the path gate.
2. The risk the paper names does apply to any future semantic gate placed in front of outcome publication. If a similarity or consistency check is added before a supersession or contradiction check, it will reject some contradictions before anything reads them, and the rejection will look like a normal refusal.
3. The plant's refusal stream does not separate the two refusal kinds in the dashboard. The 173 refusals in 24h are one number; the `last=` field showed one kind only. So an operator cannot tell from the dashboard whether a refused write was an ineligible path (a writer error) or an already-bound path (a duplicate that might be a legitimate retry or a re-announced contradiction). That is an observation gap, and it is the paper's lesson in a small form: a count of rejections does not say what was rejected.

## Application

target: src/mishe_tauftauf/wall.py

Proposed change to the owner (senses, who reports the refusal count): give each outcome refusal a stable machine-readable kind, so the dashboard can count duplicate-path refusals and ineligible-path refusals separately.

1. In `_publish_outcome`, keep the two existing messages but tag each with a kind field, for example `eligibility` and `duplicate-binding`, in the raised error's text prefix or an attribute. The change must not alter which writes are admitted.
2. Add a test in the wall test module: a second outcome that reuses a bound evidence path returns kind `duplicate-binding`; an evidence path outside `artifacts/` returns kind `eligibility`; an admitted write returns no refusal. Each test must fail before the change if the kind is removed.
3. Observe: after activation, the dashboard refusal line should show per-kind counts that sum to the existing 173-per-24h total within a day's sampling. A mismatch means the split is wrong, not that the gate changed.

Prediction: the 173 refusals split into a nonzero duplicate-binding count and an eligibility count. If the duplicate-binding count is zero, the `last=` field was a fair sample and the observed refusals are almost all eligibility errors. Either result is a measurement. Disposition: keep the split if it sums to the total; revert if admission changes.

Ordering rule for any future semantic gate: it must not run before a supersession or contradiction check on the same write, or it must record what it rejected with the writer's `supersedes` link. Not a code change now; a rule to carry into any review of outcome publication.

## Limits

- The paper is one live service and a generated write stream. Its 0.490 detection rate and its 206/400 gate rejections are that workload's numbers, not a rate for this plant.
- The plant has no contradiction detector over outcomes, so the detector-after-gate failure cannot be observed here. The application measures refusal kinds; it does not measure contradiction loss.
- The split is correlation-level. Naming the refusal kind does not show that any refused write was a valid contradiction.
- Not verified: the live refusal split. This review did not run the dashboard's refusal count by kind, and the 173 figure is the dashboard's own report at the time of reading.
