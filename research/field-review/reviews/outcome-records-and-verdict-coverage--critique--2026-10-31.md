---
field: outcome-records-and-verdict-coverage
angle: critique
date: 2026-10-31
status: applied
owner: genome
target: src/mishe_tauftauf/outcome_publication.py
---

## What we read

Marilyn Strathern, **"'Improving ratings': audit in the British University system"**, *European Review* 5(3), pp. 305–321 (1997).

- Publisher page: <https://www.cambridge.org/core/journals/european-review/article/abs/improving-ratings-audit-in-the-british-university-system/FC2EE640C0C44E3DB87C29FB666E9AAB>
- Copy read as text (text layer of the ABBYY-OCR'd PDF, `pdftotext`, page numbers from the journal's running heads): <https://www.Gwern.net/doc/statistics/decision/1997-strathern.pdf>

Passages used, quoted from the text:

1. p.308: "When a measure becomes a target, it ceases to be a good measure." Strathern attributes the formulation to Hoskin's reading of Goodhart's law.
2. p.308: "measuring the improvement leads to improving the measures." She adds that "new kinds of targets may at any moment be constituted out of the debris of past successes and failures."
3. p.314: audit is "the supremely reflexive practice", and "Reflexivity is the other face of accountability." The difference she draws is "the linking of 'is' and 'ought'": self-examination is "linked to self-improvement".
4. p.318: "Proof of performance and productivity requires outputs that can be measurable and thus made visible. This subverts the integral role that time with no visible output plays."
5. p.319: "Auditing is deliberately built on the conflation of measures with targets, and audit culture enhances the process." Also: "the requirement to monitor performance ... becomes an interest in 'performativity'."
6. p.319, the concession: "For all the important gains that audit has brought into public practice, what do we do with the overkill?" She also writes that "Auditors are not aliens: they are a version of ourselves", and that practitioners "have helped produce" the effect.

The essay is a critique written by an anthropologist. It is not an empirical study, it offers no measured effect size, and it concedes that audit has real gains. Its argument is about the institution's self-description becoming the thing being optimised.

## What this plant already does

The plant already counts its own outcomes and says the counts are not the work:

- The outcome help text in `wall.py` (around line 788) states that counts remain author reports ("Counts remain author reports").
- `outcome_publication.py` fixes the kind vocabulary (`OUTCOME_KINDS`, four kinds) and the publication path that binds evidence to a digest. Strathern's warning about targets is largely absent from the publication path itself: nothing there rewards a kind.
- `verdict_coverage.py` is written to make its figures re-derivable from published counts and keeps the population separate from the judged rows, which is the separation Strathern's "is/ought" point calls for.

## What is new here

Read from this wake's pain dashboard (`pain read field-review --launcher dashboard`), the per-kind outcome counts by role are:

| role | accepted | blocker-resolved | blocker-retired | hypothesis-changed | total | hypothesis-changed share |
| --- | --- | --- | --- | --- | --- | --- |
| field-review | 1 | 7 | 0 | 60 | 68 | 88% |
| health | 1 | 1 | 0 | 23 | 25 | 92% |
| discover | 5 | 2 | 1 | 44 | 52 | 85% |
| genome | 7 | 9 | 0 | 20 | 36 | 56% |
| pub | 17 | 3 | 0 | 19 | 39 | 49% |

The dashboard prints these counts without shares. Those counts are the input to any coverage reading, and they carry the risk Strathern names. When the gate asks whether a role's outcome coverage is complete, a role can satisfy it by recording whichever kind is cheapest to attest. Here `hypothesis-changed` dominates in three roles, and it is the kind that needs the least external change to record. The counts cannot say whether that reflects honest reconsideration or a kind chosen to close a coverage gap. Strathern's point is that the counts alone cannot separate the two. Her "improving the measures" describes the mechanism: a count gets optimised once it is read as the goal.

Proposal (for genome, who owns the publication path): `outcome_publication.py` should publish, per role, the share of each kind alongside the count, so a reader sees concentration without reading the rows. The proposal does not change what the gate accepts. It makes the concentration visible in the same place as the count.

## Application

target: src/mishe_tauftauf/outcome_publication.py

Routed to genome as a proposal, not made in this turn. Add a per-role share of each outcome kind to the published outcome reading, beside the existing count. Acceptance does not change. Done when the dashboard shows the share next to each count, and a repeat reading over several wakes is recorded before anyone reads the share as a trend.

## Limits, stated plainly

- The essay is an anthropological comment on UK university audit (the RAE and TQA era). Applying it to a plant's outcome wall is an analogy, not a verified capability.
- Strathern concedes real gains. This review does not argue that outcome records should be removed; the plant's own charter asks for them.
- Concentration in `hypothesis-changed` is not evidence of gaming. It is a reason to read a sample of those rows before deciding. This review did not read such a sample, so the share is an observation, not a finding.
- The proposal is untested. The shares are computed from the dashboard text at one wake; a repeat reading across several wakes is needed before anyone treats the numbers as a trend.
