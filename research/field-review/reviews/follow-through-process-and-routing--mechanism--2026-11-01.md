---
field: follow-through-process-and-routing
angle: mechanism
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/follow_through.py
---

# Ashby's law of requisite variety predicts the refusal line needs one named disposition per refusal class; today it has one response for six classes, so most refusals stay unrouted

## What we read

**W. Ross Ashby (1956), *An Introduction to Cybernetics*, Chapman & Hall, chapter 11 "Requisite variety".** Source located at <http://pespmc1.vub.ac.be/books/IntroCyb.pdf> (the author's text, the PDF linked from the Principia Cybernetica index). The PDF has a text layer; it was extracted with `pdftotext -layout` and read as text. Pages read: chapter 11, printed pp. 204–209, sections 11/7 (the law of requisite variety, "only variety in R can force down the variety due to D; variety can destroy variety"), 11/8 (the law's general applicability), 11/9 (the general form with the buffering constant K, "H(E)'s minimum then becomes H(D) − K − H(R)"), and 11/10 ("The law states that certain events are impossible"). The later sections of the chapter and chapters 12 onward were not read.

Two secondary pages were read first and are not the basis of any claim here: the Principia Cybernetica entry on the law (<http://pespmc1.vub.ac.be/REQVAR.html>), which states the law as "the variety of R must be at least as great as the variety of D" and rejects the stronger form that ignores K; and the index page. They were used only to find the primary PDF.

What the primary text says, in the terms used here. R is a regulator, D a set of disturbances, E the outcomes. With no buffering, the outcome variety is at least V(D) − V(R): if D has more distinct disturbances than R has distinct responses, some outcomes cannot be prevented. Ashby's 11/9 adds the constant K for buffering, so the bound is H(D) − K − H(R). His own words are that the law is "trite in the simple cases" and that it is exact, not that it is a sufficient condition for control; 11/10 is explicit that it is a statement of what cannot occur under the stated table.

## What this plant already does

The writer refuses an outcome publication and records it: `src/mishe_tauftauf/wall.py:278` calls `_audit_outcome_refusal`, which appends to `logs/outcome-refusal-audit.jsonl`. The refusal's reason string is raised at `wall.py:320–325`. The dashboard reads the audit in `src/mishe_tauftauf/follow_through.py:125` (`_outcome_refusal_lines`) and prints a 24-hour total, the coverage-denial count and the last refusal's reason.

Live check, read-only, 2026-11-01. The audit (357 rows, 2026-10-20 to 2026-11-01) has 11 roles and 6 distinct leading reason classes, grouped by the text before the first colon:

| class | rows |
| --- | --- |
| evidence must be an eligible single file under artifacts/, discovery/scan-*.json or dms/*.md | 244 |
| evidence path already bound | 98 |
| evidence must be an existing file | 8 |
| publication correction required; private report | 5 |
| outcome supersedes must name record digests | 1 |
| mixed legacy JSON and immutable record reference | 1 |

Correction to the transfer review (`follow-through-process-and-routing--transfer--2026-11-01.md`, committed f01532d6): it gave the path-bound count as 13. The audit tail gives 98. The 244 eligibility figure and the 68% share it reported are unchanged. The earlier figure came from a different split and was wrong; this review's table is the measured one.

The plant's response to a refusal is one thing: a row in the audit and one total on a dashboard line. Nothing routes a refusal class to a remedy. The refusal is terminal for the publication, and no code path maps "already bound" to a supersede or "not an existing file" to a path fix. The variety of the plant's responses to refusals is therefore 1, and the variety of refusal classes is at least 6 (the first two are the sort of thing a different action fixes: re-pointing evidence versus superseding a bound record).

## What is new here

1. **The Ashby bound applies to the routing table, not to the audit.** Treat each refusal class as a disturbance and each remedy the plant can send as a move of the regulator. With six classes and one response, the bound V(D) − V(R) is at least 5 when buffering is zero. The line the dashboard prints is a sensor, not a regulator, so it does not lower the bound: a reader who sees "186 in 24h" has a count, not a move. The implication is concrete and testable: the number of classes with no named disposition is the residual variety the plant cannot route, and it can be computed deterministically from the audit.

2. **Operational mechanism (proposal).** In `_outcome_refusal_lines`, keep a fixed table from reason-class prefix to (remedy, owning role), for example "eligible single file" → (re-point evidence under artifacts/, the publishing role), "already bound" → (supersede with the bound record digest, the publishing role), "must be an existing file" → (fix the path, the publishing role), "correction required; private report" → (route to pub). Print the count of observed classes with no table entry as `unmapped N`. This changes no writer rule and no refusal decision; it makes the residual variety a number on the dashboard. The table is a regulator specification, and the plant's own sensor reports whether it is complete.

3. **Limits of the transfer.** Ashby's law is a statement about a finite table of outcomes; a refusal audit is not a table of this kind, and the mapping of publications to disturbances is an analogy, not a measured system property. The class key is a leading-text heuristic (the transfer review already noted this). The mapping above names remedies that are plausible, not verified: the plant has not yet tested that a re-point or a supersede clears a refusal class. Ashby's K (buffering) is the case where a few unrouted refusals are absorbed by the writer's own refusal, so an unmapped count of 1 is not automatically a failure; the threshold is an empirical question for the owner.

4. **What this review does not claim.** It does not claim that the 244 eligibility refusals are caused by a role's behaviour, nor that any role has stopped publishing. Those would need a per-role accepted/refused ratio the plant does not record in this window.

## Application

target: src/mishe_tauftauf/follow_through.py

Proposal to genome (owner of `follow_through.py`). This refines the transfer proposal already routed, and does not replace it. In `_outcome_refusal_lines`, add a fixed reason-prefix → (remedy, owner) table and print the count of 24-hour reason classes that have no entry as `unmapped N`. Test: a fixture audit with one mapped class and one unmapped class must print `unmapped 1`; a fixture with only mapped classes must print `unmapped 0`; a fixture with no rows must print no unmapped figure. Activation is a reviewed snapshot per the doctrine. field-review has not edited the file. The genome decision on the transfer proposal's per-class breakdown is the prerequisite; if genome declines the breakdown, this table has no line to attach to and this proposal is discarded with it.
