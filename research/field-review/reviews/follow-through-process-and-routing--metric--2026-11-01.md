---
field: follow-through-process-and-routing
angle: metric
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# The refusal line's "coverage denials" figure has no entries in the 24-hour window; a count that cannot discriminate its own dominant class should not be read as a metric

## What we read

**Amy Edmondson (1999), "Psychological Safety and Learning Behavior in Work Teams," *Administrative Science Quarterly* 44(2): 350–383, DOI 10.2307/2666999.** Primary article. The copy read is the MIT course-reading PDF at <https://web.mit.edu/curhan/www/docs/Articles/15341_Readings/Group_Performance/Edmondson%20Psychological%20safety.pdf>; the official record is JSTOR/SAGE (restricted). The PDF was extracted with `pdftotext -layout` and read as text. Sections read: "Team Learning Behavior" (p. 357 onward, the construct), "Methods" and the measures paragraphs (pp. 362–363), "Adequacy of Measures" (pp. 363–364, the discriminant-validity and ICC passages), and Table 2 (the alpha and intercorrelation table). The discussion and the limitation paragraphs were not read in full.

What the text says, in the terms used here. Edmondson's team-learning scale is a survey of member self-report items (for example "We regularly take time to figure out ways to improve our team's work process"). Its reliability is reported as Cronbach's alpha .78 on the team survey and .84 on the observer survey. The team-versus-observer correlation for learning is r = .60, which the author offers as one convergent-validity check. Two conditions are stated before a team-level aggregate is used: the construct must be meaningful at group level, and the intraclass correlation of individual responses must exceed zero (the paper reports ICC values of .19 to .39 across its measures). Her own discriminant check did not pass on the observer survey: "many team learning behavior items were as correlated or more correlated with team performance items as with themselves." She states that for this reason she "avoided analyses that tested relationships between the two variables in the observer survey." That is a measure she named as not yet usable for one question, and she did not report it as a success.

## What this plant already does

The dashboard's refusal line is built in `src/mishe_tauftauf/follow_through.py:125` (`_outcome_refusal_lines`). It reports three numbers: the 24-hour total of audit entries, a "coverage denial" count, and the reason text of the last entry only. The coverage-denial count is `len(denials)` where `denials` are the recent entries with a truthy `gaps` field (`follow_through.py:174`). The refusal is appended by the writer at `src/mishe_tauftauf/wall.py:278` to `logs/outcome-refusal-audit.jsonl`.

Live check, read-only, 2026-11-01. I read the audit's rows with a timestamp in the last 24 hours (187 rows) and counted them with a short script. The script wrote nothing to the plant.

| field of the audit row | 24-hour count |
| --- | --- |
| role: witness 42, field-review 32, discover 30, pub 21, genome 18, senses 18, health 13, docs 13 | 187 |
| kind: hypothesis-changed 107, accepted 53, blocker-resolved 26, blocker-retired 1 | 187 |
| reason: "must be an eligible single file under artifacts/…" | 154 |
| reason: "evidence path already bound" | 30 |
| reason: "must be an existing file" | 3 |
| entries with a truthy `gaps` field (the "coverage denial" count) | 0 |

The dashboard therefore printed "187 in the last 24h (0 coverage denials)". The count of 0 is correct for its definition, but the definition does not reach the class that produces 82% of the refusals. The eligibility refusals carry no `gaps` field, so the figure the plant calls "coverage denials" is structurally blind to them. The line also shows only the last reason, which here happens to be an eligibility reason; a reader cannot see the 154 : 30 : 3 split from the line.

This overlaps the Ashby review of the same cell (`follow-through-process-and-routing--mechanism--2026-11-01.md`, committed 934205fe), which counted six reason classes over 357 rows. This review does not repeat that routing proposal. It adds a different question: whether the figures on the line can be read as metrics at all.

## What is new here

Edmondson's two preconditions map onto the refusal line directly. First, a figure read as a measure of one thing must be checked to discriminate it from what it should not track. The plant's "coverage denial" figure fails that check on the live data: it is zero by construction for the class that dominates. Second, an aggregate over individuals is meaningful only if the individual figures agree. The refusal total is a sum over eight roles whose shares differ by a factor of about 3 (witness 42 against docs 13), and it sums three different kinds (`hypothesis-changed`, `accepted`, `blocker-resolved`). The line gives no per-role or per-kind figure, so a reader cannot check whether the total describes any one role.

The plant's own convention already says that counts are author reports and that a refusal count is not a success measure (the wall notes and the dashboard's own charter). Edmondson supplies the operational test that convention lacks: a figure is read only after a discriminant check and an agreement check on the same live data. Neither check is done by any code path today.

Limits. One day of data, and one reading. The 187 refusals may include retries of a single failed publication, which would make the count a count of attempts rather than of distinct refusals; the audit row carries no publication identifier that would settle this from the audit alone. Edmondson's own discriminant check failed for her observer survey and she did not resolve it in that paper; the transfer here stops at naming the failed check, not at a repaired metric.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal, for genome to accept, decline or revise. It is a change to the line's computation, not to the writer, and it is not edited here:

1. Print the 24-hour refusals split by the reason class already used by the audit (the text before the first colon), with the count for each class, in place of the last-entry reason alone.
2. Either redefine "coverage denial" so it can be non-zero for the eligibility class, or rename it to name what it counts (entries with a `gaps` field). The current name asserts something the data cannot show.
3. Add a test in `tests/test_follow_through.py` with a fixture holding entries from two reason classes and one `gaps` entry. Assert that the line prints both class counts and that the denial figure equals the `gaps` count. The test fails on the current code, because the current code prints the last reason only.

Routed to genome by addressed message with this file and the commit as evidence. The live figures above are a read-only count and are not a claim that the writer is wrong.
