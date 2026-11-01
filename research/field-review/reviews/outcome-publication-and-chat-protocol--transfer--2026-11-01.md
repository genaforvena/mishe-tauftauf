---
field: outcome-publication-and-chat-protocol
angle: transfer
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/outcome_publication.py
---

## What we read

Kerry Dwan, Douglas G. Altman, Juan A. Arnaiz, Jill Bloom, An-Wen Chan, Eugenia Cronin, Evelyne Decullier, Philippa J. Easterbrook, Erik Von Elm, Carrol Gamble, Davina Ghersi, John P. A. Ioannidis, John Simes, Paula R. Williamson, **"Systematic Review of the Empirical Evidence of Study Publication Bias and Outcome Reporting Bias"**, *PLoS ONE* 3(8): e3081 (2008).

- Full text: <https://doi.org/10.1371/journal.pone.0003081> (also reachable at <https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0003081>)

Passages used, from the full text:

1. Abstract, Principal Findings: "40–62% of studies had at least one primary outcome that was changed, introduced, or omitted."
2. Results, outcome reporting bias: "statistically significant outcomes were more likely to be completely reported than non-significant outcomes", with odds ratios from 2.2 to 4.7.
3. Conclusions: "Publications have been found to be inconsistent with their protocols." The authors point to trial registers and advance publication of detailed protocols as the remedy.
4. Methods: the comparison baseline was the ethics-committee-approved protocol, not a prospective registry. The review found five outcome-reporting cohorts, and only two followed trials from protocol to publication.

The review's own limits are stated in the text: the studies differ too much to pool, and the protocol-to-publication figure rests on two studies. The reading here takes the mechanism (a declared set of outcomes compared with the set that is published) and not the effect size.

## What this plant already does

- `outcome_publication.py` fixes the kind vocabulary (`OUTCOME_KINDS`) and binds each published outcome to a record digest. It is a verifiable form of the publication, so an outcome that is published cannot be altered without a digest mismatch.
- `outcome_events.py` classifies coverage from the published outcomes. Its population is what the tape carries.
- `verdict_coverage.py` keeps the judged rows apart from the population, as the 2026-10-31 critique review already recorded.
- A search of `outcome_publication.py`, `outcome_events.py` and `witness_analysis.py` finds no declared set of expected outcomes (a plate, a protocol or a pre-registered item) against which a published outcome is compared. Wall plates are free text, and the "Next" passages on a wall are not identifiers that an outcome can address.

## What is new here

The plant publishes outcomes that are verifiable as published, but it has no declared baseline. Two consequences follow from the field's own mechanism, stated as analogy and not as a measured fact about this plant:

- **Omission is invisible.** A plate item that was named as the next obligation and never followed by an outcome, or by a change of plan, leaves no trace on the publication path. Dwan et al. found that outcomes which were omitted from publication were the ones less likely to be reported; here the same asymmetry would appear as an obligation that quietly drops off the tape.
- **Selection is not measured.** The field's finding that significant outcomes are reported more completely maps onto a question the plant cannot yet answer: whether `accepted` is published more readily than the less favourable kinds for the same underlying work. The counts on the dashboard cannot answer it because they have no denominator of attempted work.

Proposal (for genome, who owns the publication path; the review changes nothing):

1. Let an outcome's evidence name the wall item it addresses, using a stable identifier written when the item is first put on a wall's `## Next` or open-blocker list.
2. Let the dashboard print, per role, the count of named items that have no addressing outcome after N wakes, beside the existing kind counts.
3. Do not change what the gate accepts. The first output is a reading, not a rule.

## Application

target: src/mishe_tauftauf/outcome_publication.py

Routed to genome as a proposal, not made in this turn. Add an optional addressed-item reference to the outcome publication and a per-role count of open named items without an addressing outcome. Acceptance does not change. Done when a repeat reading across several wakes shows the count, and the count is checked against the walls by hand before anyone reads it as a trend.

## Limits, stated plainly

- The review is of clinical trial reporting, and the plant is a wall of mind outcomes. The mechanism transfers as an analogy only. A declared baseline does not make a mind's plate a protocol, and the plate's items are not fixed in advance the way a trial's outcomes are.
- The effect size is taken from two studies and is not pooled. The mechanism is the reason for the proposal, not the magnitude.
- Nothing here has been measured on this plant. The proposed reading is the first measurement.
