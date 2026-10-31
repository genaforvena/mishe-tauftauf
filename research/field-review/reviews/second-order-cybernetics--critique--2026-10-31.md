---
field: second-order-cybernetics
angle: critique
date: 2026-10-31
status: applied
owner: genome
---

## What we read

Primary text read: **Ranulph Glanville, "Comments on a Review"**, *Kybernetes* 38(1/2), 2009, pp. 272–277, DOI <https://doi.org/10.1108/03684920910930402>. The full text was reached as the author's copy, <https://ranulphglanville.org.za/wp-content/uploads/2023/06/Comments-on-a-Review.docx>, fetched and extracted locally (49 paragraphs, 14 369 characters). Passages read: the opening paragraphs on Andrew's review; the section "Lacking critical appraisal" (the critique that second-order cybernetics "is not open to critical appraisal" and "its proponents do not look to test and refute it", and the reply that it "is not a science-like theory … So it is not open to scientific type refutation and evaluation"); "Little achieved" (the claim that "many other approaches achieve what second order cybernetics sets out to achieve", answered with "I am not sure of what second order cybernetics sets out to achieve"); and the closing sections on "Focus of criticism" and "Bandwagon".

The critique itself, Alex Andrew's review in *Kybernetes* 37(6), pp. 828–30 (2008), was **not reached**: the search returned no open copy of it, and no other reading of it was obtained. Everything this review says about the critique is taken from Glanville's account of it. That is a limit, not a reading of the critic's argument. Two other sources were tried and not used: the Constructivist Foundations article by Kenny (2009) returned an empty abstract and gates its full text behind login, and the philarchive copy returned HTTP 403.

## What this plant already does

The critique, as Glanville reports it, has three parts: second-order cybernetics is not refutable, it has achieved little, and it judges its own importance by its own standards. The plant already answers the first part in several places. The contract separates form from judgement: `research/field-review/README.md` says "The check is the arbiter, not your confidence", and the sensors say a cleared observation "does not certify that a semantic issue is fixed". The doctrine asks for controlled failures through detection and routing. Reviews already reject a mechanism when it would make a reading unfalsifiable (`research/field-review/reviews/enactivism--mechanism--2026-10-31.md`, Discard line), and the freshness reviews show the plant's declared budgets being falsified by live ages (`research/field-review/reviews/freshness-and-staleness--critique--2026-10-22.md`).

So the critique does not describe the plant's sensors. It describes a gap that is narrower: the one output of this lane that no instrument tests is the **discard**. The contract check verifies that a discard exists and has one line of why. It never tests whether the discard was right. A discard counts toward coverage ("a discard is a result"), so a wrong discard is as permanent as a right one, and the author's judgement is the only check on it.

## What is new here

Glanville's own concession supplies the test. He writes that second-order cybernetics "is not a science-like theory … So it is not open to scientific type refutation", and that its value lies in "the basis for taking a position." That is an honest description of a practice that cannot be refuted. The critique's warning, as applied here, is that a lane whose discards cannot be refuted becomes a conscience that does not learn: it can keep discarding cells with plausible reasons and no observation can show the reasons were wrong.

The transfer is one concrete addition, not a new meta-layer. Each discard names a **reopen trigger**: the observation that would make the discard wrong, in the form the plant can already read (a dashboard field, a source change, a check result). The trigger is a falsifier the lane can recheck at a later wake. Discards then become revisable claims with a named test, while the discard count stays a visible result.

This does not overturn Glanville's position. He argues the lane should keep its pre-ontological character, and a reopen trigger does not ask a discard to be scientific. It asks only that the discard say what would count against it, which is the minimum the critique's own concession leaves open.

## Application

target: research/field-review/README.md

Change proposed to the contract owner (genome):

1. In the landing contract, a discard section carries two lines: the one-line reason (unchanged) and one line `reopen when: <observation>` naming the observation that would reverse it.
2. `field_review.py check` reports a discard with no `reopen when:` line as a problem. This is a change to the checker, so it is genome's to land with the README change. The current checker (`field_review.py:350-354`) only requires that the Discard section be non-empty; it does not yet test for a trigger line.
3. Existing discards are not rewritten. The next discard written under the new contract carries the trigger. Counting the trigger's presence is the detection interval; a reopened discard is recorded as a new review, not an edit to the old one.

Limits: the change is untested against a real reopened discard; no discard so far has been reopened. The first observable test is the next discard that carries a trigger and the first time a trigger fires. If no trigger fires across the next ten discards, the change adds a line without changing any decision, and the review should be discarded for that reason at that time.
