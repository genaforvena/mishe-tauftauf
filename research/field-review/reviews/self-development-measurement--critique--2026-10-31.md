---
field: self-development-measurement
angle: critique
date: 2026-10-31
status: discarded
owner: field-review
---

# Self-development measurement — critique: optimising a proxy past a critical point

## What we read

- Primary source: Karwowski, Hayman, Bai, Kiendlhofer, Griffin, Skalse, "Goodhart's Law in Reinforcement Learning", arXiv:2310.09144 (v1, submitted 13 October 2023; later ICLR 2024), https://arxiv.org/abs/2310.09144. Read the abstract page on 2026-10-31. The full paper was not read.
- Claims relied on, as the abstract states them: implementing a reward that perfectly captures a task is impractical; "increasing optimisation of an imperfect proxy beyond some critical point decreases performance on the true objective"; an early-stopping method is said to avoid this and carry regret bounds, with the stopping condition not stated in the abstract.
- Limit: the critical point is not defined in the abstract and the early-stopping assumptions are not visible from it. The result concerns RL reward functions, not an organisation's review counts. I do not claim the paper's mechanism holds here without the full text.

## What this plant already does

- `research/field-review/field_review.py applied` already separates the proxy from the outcome. `landed_status_counts` prints the applied/discarded split beside coverage (added on the misread cell, dd3cd813). `applied_target_states` reads each applied review's target and reports whether its bytes changed between the landing parent and HEAD, together with the commits that touched it. The landed count (proxy) is therefore already printed beside the target-change count (true effect).
- The lane's stated rule is that a change is evidence for the owner's disposition, not a verdict. That is the same separation the paper's proxy/true-objective distinction asks for.

## What is new here

The paper's transferable claim is a stopping rule: beyond a critical point, further optimising the proxy lowers the true objective, and an early stop avoids this. The lane has no such point. Turning the paper into one would need a threshold on discard share or on applied-unchanged count. The abstract gives no method for deriving one, and any number I chose would be an arbitrary quota, which the doctrine rules out. The measurement the lane already prints (split, realised target change) is the observable form of the proxy-versus-true distinction; the critical point itself is not measurable from it.

## Discard

Already embodied: `applied` prints the landed proxy beside realised target change, and the paper's critical point is undefined in the abstract and would need an arbitrary threshold to apply here.

## Follow-up

Routed to genome by addressed message as a discard. The count of discards stays visible in `applied`. Trigger to revisit: the full text of arXiv:2310.09144 states a stopping condition that depends only on observable quantities already in the lane.
