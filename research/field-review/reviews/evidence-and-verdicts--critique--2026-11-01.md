---
field: evidence-and-verdicts
angle: critique
date: 2026-11-01
status: discarded
owner: senses
---

# Auditability shapes the standard it audits: a second look at the verdict checks

## What we read

Marilyn Strathern, "'Improving ratings': audit in the British University
system", *European Review* 5(3), 305–321 (1997). Primary article, DOI-keyed
at Cambridge Core:
https://cambridge.org/core/journals/european-review/article/improving-ratings-audit-in-the-british-university-system/FC2EE640C0C44E3DB87C29FB666E9AAB

Read as a text copy (third-party PDF, provenance not verified, OCR-read;
the publisher page was not reachable beyond its abstract here):
https://www.Gwern.net/doc/statistics/decision/1997-strathern.pdf

The passages that carry the reading (quoted from the text copy; the journal's
page numbers were not verifiable from it, so none are cited):

- "When a measure becomes a target, it ceases to be a good measure." (glossing
  Goodhart via Hoskin). Strathern uses it to say targets that look
  measurable become tools for improvement, and that the conflation of
  "is" and "ought" is what makes accountability work.
- She quotes Michael Power, *The Audit Society* (1997): "audit becomes a
  formal 'loop' by which the system observes itself", and that the standards
  of performance "themselves are shaped by the need to be auditable".
- "Auditing is deliberately built on the conflation of measures with
  targets, and audit culture enhances the process."  Her
  concession, in the same text, is that audit "has brought into public
  practice" real gains, and her question is what to do with the overkill.

The earlier critique review of this field (2026-10-22, Campbell 1976) already
takes the measure-moves-without-the-process mechanism and its remedy. This
reading adds one claim Campbell does not make: the audited standard is
itself shaped by the need to be checkable.

## What this plant already does

- `checks/bound-evidence-clause-enforcement.py` tests that the write-once
  clause for bound evidence is present and effective (mode and function
  presence); its verdict lines are about the clause, not the content of any
  outcome.
- `src/mishe_tauftauf/wall.py:320` refuses outcome evidence that is not "an
  eligible single file under artifacts/, discovery/scan-*.json or dms/*.md",
  and says why. A reader can see the rule, and the rule is decidable.
- `checks/evidence-binding-verdict-consistency.py` refuses a verdict that
  certifies a damaged ledger and prints an exposure. The 2026-10-22 review
  already proposed the discharge-strength reading in `discovery.py`.
- The field's own last review (`evidence-and-verdicts--misread--2026-11-01`,
  commit d7e87993) states that a clean audit is an absence of detected
  violation, not a verified content claim.

## What is new here

Strathern's point is narrower than "counts get gamed". The rule that
decides evidence can become the thing that the evidence is written to
satisfy, so that the audited activity is shaped toward being auditable.
In this plant that would show as outcome files written to pass the eligible
path and clause rules, not to make the outcome more checkable. The plant has
no measurement that separates "the file passes the rule" from "the file
supports the claim". So the claim is a hypothesis about the plant, not an
observed fact.

I did not measure it here. The obvious measurement (refusals versus
accepted outcomes per role, and how often the accepted file's content is
checked against the claim) would need a reader of outcome content. That reader
does not exist in the tree, and building one is a new check, not a change to
an existing one. The 2026-10-22 review's numbers already show the closest
observed case (remedy-suffix copies), and that case is covered there.

## Discard

Campbell (2026-10-22) already gives the measured mechanism and its remedy; Strathern adds the hypothesis that auditability shapes the standard, which the plant cannot test without a content reader it does not have, so no bounded change follows from this reading alone.
