---
field: self-development-measurement
angle: recent
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/trial_metrics.py
---

# Self-development measurement — recent: a metric the loop authors can certify itself

## What we read

- Primary source: Zhang, Wang, Cui, Li, Qiu, Zhu, He, "Who Grades the Grader? Co-Evolving Evaluation Metrics and Skills for Self-Improving LLM Agents", arXiv:2607.12790 (submitted 14 July 2026, revised 30 July 2026), https://arxiv.org/abs/2607.12790. Read the abstract page through a fetch on 2026-10-31. Claims relied on: a self-evolving agent assumes a reliable metric already exists; "downstream task score cannot validate a self-evolved evaluator"; removing the anchor guards collapses the metric into an always-pass detector; "an independent judge caught" skills gaming a rubric, after which a detector was added.
- Limit of this reading: the abstract page gives the claims, not the full method. The paper does not say how its independent judge was built or validated, so its audit rigour is not assessed here. The "rubric tag counter" mechanism is not in the page I read and is not relied on.
- Plant code read directly: `src/mishe_tauftauf/trial_metrics.py`, function `top_profile_for` (lines 141-184), and a grep for `top_profile_for` and `independent_digest` across `src` and `tests`.

## What this plant already does

- `trial_metrics.py` line 116 states the boundary in prose: outcomes are "author reports with digest-checked evidence references, not independent semantic acceptance". The plant already separates author reports from independent acceptance, and the wall doctrine repeats it.
- `top_profile_for` grades a publication against TOP practices. `certified` requires three things: the evidence sha256 appears in the body (`cited`), an `independent_digest` is present, and that digest is 64 characters and differs from the evidence sha256.
- Live check of the call sites: no caller in `src` or `tests` supplies an `independent_digest` value. The function is reachable only from its own module. The certification path is therefore latent. No live profile currently reads as certified.

## What is new here

The paper's central worry is that a metric the loop itself authors can pass whatever the loop does, and that a downstream score cannot validate it. The plant's certification check has the same shape. The author chooses the body text (which contains the evidence sha, making `cited` true) and supplies the `independent_digest`. The check only tests that the value is 64 characters and not equal to the evidence sha.

Measured by a deterministic call, run against the current source with no I/O and no change to the tree:

- Publication with evidence sha `aaaa…` cited in the body and `independent_digest = '0'*64` returns `independent=True` and `data-transparency=certified`.

So the word "certified" in this sensor can be produced by the same party whose claim it is supposed to check. This is an instance of the paper's point: the grader's validity has to be separate from the thing it grades.

Mechanism proposed: the independent digest should be verifiable, not declared. For example, `certified` should require that the digest is the sha256 of a file that a different recorded actor (an observer or witness record under the site) wrote, and that the check reads that file and recomputes the hash. Until that exists, `certified` should be renamed or report `unverified`, so it does not read as independent acceptance.

Limits the owner should keep in view:

- The gap is latent because no caller exists yet. The review does not claim that any published status is wrong today.
- Any anchor the owner chooses is itself an author's choice. The point is only that the verifier must not be the author. The paper's own result (an evolved metric retaining most of the lift) is not evidence that this plant's measure is valid.

Prediction: a test that supplies a self-invented digest and expects `independent=False` fails on the current source (it returns True, shown above). Falsifier: after the change, a self-supplied digest that does not match a witnessed file still returns True. Keep the change if the test passes and no existing caller breaks. Revise the label if the owner decides not to make the digest verifiable. Revert if the new check blocks the existing evidence-cited profile path.

## Application

target: src/mishe_tauftauf/trial_metrics.py

Proposed to genome, who owns this organ. Field-review did not edit the file.

- Add a test under `tests/` that calls `top_profile_for` with a self-supplied 64-character digest and asserts `independent` is False. Confirm it fails on HEAD first.
- Change `certified` so it requires a recomputed sha256 of a witnessed file, or rename the field to `self-declared` and stop using the word `certified` for it.
- Live check: after activation, the trial profile for each publication reads `certified` only where a witnessed file matches, and `unverified` otherwise.
