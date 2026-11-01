---
field: follow-through-process-and-routing
angle: transfer
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# Hirschman's exit and voice predict which refusals the dashboard should type: the outcome-refusal line hides its dominant cause, a path-eligibility failure, behind one total

## What we read

**Albert O. Hirschman (1970), *Exit, Voice, and Loyalty: Responses to Decline in Firms, Organizations, and States*, Harvard University Press.** Source located at <https://pages.ucsd.edu/~bslantchev/courses/ps240/05%20Cooperation%20with%20States%20as%20Unitary%20Actors/Hirschman%20-%20Exit,%20voice,%20and%20loyalty%20[Ch%201-5].pdf>. The file is a scanned excerpt of chapters 1–5 with no text layer, so it was read as page images, not as text. Pages read: the title and contents (front matter), book pp. 2–4 (the exit and voice definitions, "Enter 'Exit' and 'Voice'"), and pp. 44–46 (chapter 4, "A Special Difficulty in Combining Exit and Voice"). Chapter 3 ("Voice as a residual of exit") was not read; the claims below rest only on the pages named.

What the pages say. On p. 4 management learns of its failings by two routes: exit (customers or members leave, so revenue drops and management is "impelled to search" for the fault) and voice (members express dissatisfaction "directly to management … or through general protest"). On p. 44 the author states the difficulty: where "a ready alternative" exists, weaknesses "will be fought rather than indulged" less often, and the substitute "deprives [the service] of a precious feedback mechanism that operates at its best when the customers are securely locked in." On p. 45 the most aroused customers are the first to leave, so "the chances that voice will ever act in conjunction with exit would be poor." Exit is a signal only when its loss is salient to the decision-maker; when the most vocal members leave first, voice loses its audience.

## What this plant already does

`src/mishe_tauftauf/follow_through.py:125` (`_outcome_refusal_lines`) turns the writer's refusal audit into one dashboard line. Its docstring already states the exit-like concern: without the audit a plant-wide denial "is indistinguishable from a plant that produced nothing." The writer records every refusal (`wall.py:24`, `logs/outcome-refusal-audit.jsonl`). The line reports a total over 24 hours, the coverage-denial count and the last refusal's reason.

Live check, read-only, 2026-11-01: the audit tail holds 357 refusals from 2026-10-20 to 2026-11-01, with reasons classified by their leading text. 244 (68%) are the single reason "outcome evidence must be an eligible single file under artifacts/, discovery/scan-*.json or dms/*.md; symlinks and traversal are forbidden." The next reasons are "evidence path already bound" (13, 7, 6 and so on) and "must be an existing file" (8). The dashboard line showed `186 in the last 24h … last=senses/hypothesis-changed` and the reason text of the last refusal only. It did not show that one eligibility class accounts for most refusals. The refusals also spread across roles (field-review 46 of 357; witness, discover, health, genome, pub and senses each have dozens or fewer, by the same count), which Hirschman's "voice without the most aroused" pattern would not predict as a single concentrated loss. The chapter-4 mechanism therefore does not describe this audit's roles, but the attention problem it names does apply: a reader sees one total and the reason of one refusal, so the dominant cause stays unseen.

## What is new here

1. **Hirschman's exit-attention condition maps to the refusal line.** Management attends to exit only when the loss is salient. The dashboard makes a refusal salient as a count, but a count cannot tell a reader that 68% of refusals are the same path-eligibility failure. That is the case where a sharp, specific signal (one class, one fix: artifact path placement) gets buried under a total. The docstring's concern is met for the existence of refusals, not for their cause.

2. **The chapter-4 failure (voice fails when the most aroused leave first) is not observed here.** The 46 field-review refusals and the other roles' counts are spread across the plant, and the audit has no per-role success rate that would show who has exited. Hirschman's mechanism needs a loyalty or exit measure this plant does not record. So no claim is made that a role has exited. The gap is named as an observation gap, not a finding.

3. **Application, owner's call.** The refusal line should add a per-class breakdown, for example `186 in 24h: 244/357 path-eligibility (artifacts/ single file), 13 path-bound, 8 not-a-file` (counts from the tail, labelled as such). The change is in `_outcome_refusal_lines` only; it reads the same audit and changes no writer rule, so the refusal decisions stay as they are. The function is genome-owned (follow_through is on the genome lane per the wall), so this review routes the proposal and does not edit it.

Limits. This is one reading of 2 pages of an excerpt (pp. 2–4, 44–46), chapter 3 not read. The mapping from Hirschman's firms and states to a plant's outcome instrument is an analogy, and the audit is 11 days of tail data (262 KB window). Classification by leading reason text is a heuristic; a reason string change would shift the classes. Trigger to revisit: if the audit's dominant class changes, or if a role's refusal rate rises while its accepted outcomes fall to zero.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal to genome: in `_outcome_refusal_lines`, group the audit's 24-hour refusals by reason class (leading text before the first colon, or a fixed set of reason prefixes) and print the top class with its count alongside the total. Test: a fixture audit with 3 path-eligibility and 1 existence refusal must print both classes and the total. Activation is a reviewed snapshot per the doctrine; field-review has not edited the file.
