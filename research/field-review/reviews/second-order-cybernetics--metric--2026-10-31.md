---
field: second-order-cybernetics
angle: metric
date: 2026-10-31
status: discarded
owner: genome
---

## What we read

Primary source: **W. Ross Ashby, "An Introduction to Cybernetics"** (Chapman & Hall, London, 1956; second impression 1957; copyright estate 1999), chapter 11 "Requisite Variety", read at <http://pespmc1.vub.ac.be/books/IntroCyb.pdf> (Principia Cybernetica Web copy). The PDF was read page by page as page images, not as extracted text. The passages used are §11/3 and §11/4 (the play-and-outcome setup, book pp. 202–203), §11/5 (the table with no repeated outcome in a column, book p. 204–205), §11/6 and §11/7 (the logarithmic form of the variety bound, book pp. 206–207), and the statement "variety can destroy variety" (book p. 207).

Ashby's own statement of the measure, §11/7: "If the varieties are measured logarithmically … let V_D be the variety of D, V_R that of R, and V_O that of the outcome. … V_O ≥ V_D − V_R", with the minimal outcome variety being V_D − V_R. The bound holds under the stated conditions: no column of the table contains a repeated outcome, R knows D's move, and R's response is single-valued.

Limits of the reading: Ashby's variety is defined over a fixed table of moves and outcomes. It is a counting bound for a regulator of a game, not a diagnostic for a regulator that is also part of the game it observes. Ashby does not treat an observer measuring its own state; that is the second-order step the field would add, and the chapter does not supply it.

## What this plant already does

The plant already reports observer and regulator state as a small fixed vocabulary. The dashboard headline and the substrate lines print verdicts from the set GREEN, RED and UNKNOWN, and `checks/observer-verdict-agreement.py` compares a fixed set of ten verdict fields (`VERDICT_FIELDS`, `checks/observer-verdict-agreement.py:50-52`). The earlier `mechanism` review in this cell family already reads the same agreement check against Pask and proposes a basis disclosure on its AGREE line. The plant therefore already measures the outcome side of the channel, and the dashboard prints what each check covers when it disagrees.

What the plant does not do is count the disturbance side: it has no enumerated set of failure or disturbance classes that its checks are meant to absorb. Without that count, V_D in Ashby's bound is not defined, so the bound cannot be evaluated here.

## What is new here

The new piece is the second-order reading of the bound: when the observer and the observed system share one variety count, the observer's own states enter V_O and V_R, not only the environment's. That reading would make the check self-referential. It would be a sound refinement only if the plant first defined V_D as a finite enumerated set of disturbance classes.

It is not new enough to change a file. The bound is a count over an enumerated table, and the plant's disturbances are not enumerated anywhere in the tree. Measuring V_O or V_R directly from the verdict strings would produce a number, but that number would describe the vocabulary the checks use, not the disturbances they must absorb, so it would not test Ashby's bound.

## Discard

The metric needs an enumerated disturbance set this plant does not have; the Ashby bound is a count over a table, and computing it from the verdict vocabulary would measure the reporting channel rather than regulation. Reopen when a check enumerates its failure classes as a finite list in the repository (for example, the `services_state` and `renderers_state` values), at which point V_D and V_O can be counted and the bound checked against the recorded outcomes.
