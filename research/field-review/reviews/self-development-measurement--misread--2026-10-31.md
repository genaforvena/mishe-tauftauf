---
field: self-development-measurement
angle: misread
date: 2026-10-31
status: applied
owner: field-review
target: research/field-review/field_review.py
---

# Self-development measurement — misread: a count that can be met two ways

## What we read

- Primary source: Manheim and Garrabrant, "Categorizing Variants of Goodhart's Law", arXiv:1803.04585 (v4, 24 February 2019), https://arxiv.org/abs/1803.04585 and https://arxiv.org/pdf/1803.04585v4. Read the PDF pages 1-4 directly on 2026-10-31.
- Claims relied on, as the paper states them: a Goodhart effect is "optimization causes a collapse of the statistical relationship between a goal the optimizer intends and the proxy used for that goal". Four categories: regressional (selection for a proxy "necessarily also selects for noise"), extremal (selection pushes the state distribution into a region where old relationships no longer hold), causal (the regulator's action changes the causal path), adversarial (another agent with different goals causes the collapse). The paper's footnote notes that Goodhart's own 1975 formulation is ambiguous in meaning, and that the paper does not claim the law is a reason to stop measuring.
- Limit: this is a categorisation paper. It gives mechanisms and simple models, not an empirical measurement of any organisation. Its rhetoric is about AI and policy; I do not claim it covers a lane of this kind.

## What this plant already does

- `research/field-review/field_review.py applied` already reports content facts only: its docstring says a change to a target "is evidence for the owning role's disposition, not a verdict", and its output ends "content facts only, not dispositions". The regressional-style gap between a counted change and a realised effect is already named in the tool.
- The contract check counts landed reviews against the grid ("115 landed"). Any cell may be closed by either an application or a one-line discard, and the check reports both the same way.
- Measured on the branch today: 115 landed reviews, 87 applied, 28 discarded (24%). The old check output did not show the split, so the proportion of cheap discards was invisible beside the coverage count.

## What is new here

The paper's extremal and regressional mechanisms apply to the lane's own coverage count. The grid is fixed and each cell needs one review, so the number that rises every turn is "landed". A count that every turn increments can be pushed up by writing a discard that is cheap to write, and nothing on the surface of `check` shows that the discard share is rising. The plant already has the right label on the realisation count; it lacks the same visibility on how the coverage count was reached.

Mechanism applied: `landed_status_counts` reads each landed review's front-matter status, and `applied` prints `applied=N discarded=M discard_share=P%` beside the existing summary. The landing contract and the cell selection are unchanged, so no review that landed before is re-judged.

Prediction: the split prints 87 applied and 28 discarded on the current branch, a 24% discard share. Falsifier: if a later turn's discard share rises well above the current value while applied reviews stay flat, the count is being met by discards, and the owner should look at the discard reviews before counting another landed one. Keep the change if the test passes and the check still passes on the current branch. Revert if the extra line breaks any caller that parses `applied` output (none found in `src` or `tests` by grep on 2026-10-31).

## Application

target: research/field-review/field_review.py

Owner is this lane, so the change is made here: `landed_status_counts` added, `applied` prints the split, and `tests/test_field_review.py::test_landed_status_split_counts_discards_beside_applications` covers the counting with stubbed reads. `python3 tests/test_field_review.py` passes 46 of 46. `check` passes 115 of 115 on the branch.
