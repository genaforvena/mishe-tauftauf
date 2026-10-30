---
field: evidence-and-verdicts
angle: critique
date: 2026-10-22
status: applied
owner: senses
target: src/mishe_tauftauf/discovery.py
---

# Evidence binding: the count that certifies a ledger is the count that can be moved without repairing anything

## What we read

Donald T. Campbell, *Assessing the Impact of Planned Social Change*,
Occasional Paper Series #8, Evaluation Center, College of Education, Western
Michigan University, December 1976 — read in the ERIC full-text scan
(ED 303 512 / TM 012 770), which reproduces the paper that Campbell first
published as Paper #8 in 1975 and reprinted in *Social Research and Public
Policies* (Lyons, ed., 1975), pp. 3–45:

- https://archive.org/stream/micro_IA41153305_0416/micro_IA41153305_0416_djvu.txt

A later reprint of the same paper carries the section this review turns on,
under the journal title *Journal of MultiDisciplinary Evaluation* 7(15)
(2011), pp. 3–43, DOI 10.56645/jmde.v7i15.297 (open access, CC BY-NC 4.0):
https://jmde.com/index.php/jmde_1/article/view/297

The section is **"Corrupting Effect of Quantitative Indicators"** (pp. 53–60
of the 1976 paper). Four passages carry the reading.

1. **The law, and its own limit.** Campbell states it as two laws, and
   immediately bounds where they hold: "I come to the following pessimistic
   laws (at least for the U.S. scene): **The more any quantitative social
   indicator is used for social decision-making, the more subject it will be
   to corruption pressures and the more apt it will be to distort and corrupt
   the social processes it is intended to monitor.**" He flags the evidence
   base as weak in the same breath: "Let me illustrate these two laws with
   some evidence which I take seriously, although it is predominantly
   anecdotal." This concession matters for the transfer below: Campbell is
   claiming a *mechanism* and a *prescription*, not a theorem.
2. **What the mechanism is, in the indicator's own terms.** His examples are
   all of one shape — the measure keeps moving while the thing measured
   does not. Police clearance rates (Skolnick): "Failure to record all
   citizens' complaints, or to postpone recording them unless solved, are
   simple evasions which are hard to check, since there is no independent
   record of the complaints." Texarkana performance contracting:
   "the contractors were teaching the answers to specific test items that
   were to be used on the final play-off testing" — and Campbell's reading
   of that defence: "they defended themselves with a
   logical-positivist, operational-definitionalist argument that their
   agreed-upon goal was defined as improving scores on that one test, this
   was generally regarded as scandalous." Soviet factory quotas: "If
   monetary value, then factories would tool up for and produce only one
   product … If number of items, then only their easiest item to produce
   (e.g. the smallest nails). All these distortions led to overproduction
   of unneeded items and underproduction of much needed ones." The
   body count in Vietnam: a "new military goal, that of having bodies to
   count, a goal that came to function instead of or in addition to more
   traditional goals, such as gaining control over territory," and "His goals
   had been corrupted by the worship of a quantitative indicator, leading
   both to a reduction in the validity of that indicator for its original
   military purposes, and a corruption of the social processes it was
   designed to reflect."
3. **The two things Campbell concedes, which are the two most transferable
   parts.** First, that the corruption is a *pressure* produced by the
   use of the indicator for decision-making, not a defect in the
   measurement: the same clearance rate is not corrupt where it is not
   used for decision-making, and the census is "relatively immune to
   distortion" precisely because it was "unused for political
   decision-making" while the vote — used for jobs, money and power — was
   "under great pressure from efforts to corrupt." Second, that the remedy
   is *not* a better number but a different arrangement of who measures
   what: "We must study the social processes through which corruption is
   being uncovered and try to design social systems that incorporate these
   features. In the Texarkana performance-contracting study, it was an
   'outside evaluator' who uncovered the problem. In a later U.S.
   performance-contracting study, the Seattle Teachers' Union provided the
   watchdog role. We must seek out and institutionalize such
   objectivity-preserving features." He adds the hedge that matters: "Many
   commentators, including myself (1969b), assume that the use of multiple
   indicators, all recognized as imperfect, will alleviate the problem,
   although Ridgeway (1956) doubts this."
4. **The case for the measure itself**, stated so the review cannot be read
   as an argument against counting: "achievement tests may well be valuable
   indicators of general school achievement under conditions of normal
   teaching aimed at general competence. But when test scores become the
   goal of the teaching process, they both lose their value as indicators
   of educational status and distort the educational process in undesirable
   ways." The conditional is the whole of the transfer: the same number is
   fine as a description and corrosive as a target.

## What this plant already does

The plant already has the parts of Campbell's arrangement that are hard, and
several of them are recent work by the role this review routes to.

- **The measure is composed, not read off.** `src/mishe_tauftauf/discovery.py`
  composes the evidence-binding state from a *gate* and a *standing audit*
  rather than from a live window: the docstring of `_ledger_evidence_binding`
  (`:5467`) and its verdict section (`:5525-5546`) give four words with four
  distinct claims — `verified` (no violating row, no binding-relevant gap),
  `drift` (the gate holds repairable rows), `settled` (the gate is empty and
  the standing audit still holds rows — "It is not `verified`: the ledger's
  history is not clean, and a reader must not be told that it is"), and
  `unknown` (a coverage gap can still hide a bound evidence path). The
  live window is explicitly demoted: "The live window is self-healing: every
  role publishes again, so it empties while the standing audit keeps the
  loss, and a verdict read from it would certify a damaged ledger. The
  verdict's cause leads the sample, so the pane's clipped prefix carries
  it." That is the Skolnick failure already anticipated and refused.
- **The rows are banded by the relation that cleared them, and the band
  states what it decides.** `:5923-5951` gives seven bands
  (`owed`, `recoverable`, `disposed`, `rebound`, `cited`, `versioned`,
  `permanent`), each with its relation published in
  `remediation_band_relations` (`:5139-5150`) so a reader does not infer a
  band's claim from its name; the bands partition the standing audit, so
  their counts sum to `all_bad`.
- **The byte relation is already published per row.** `:5120-5128` defines
  `restored` / `copy-of-current` / `unrelated` / `unknown`, and `:5153-5171`
  (`_byte_relation`) decides it from digests alone. The vocabulary was
  moved into `mishe_tauftauf.remediation_bands` so "the publisher
  (`discovery.py`) imports it to write a row's band, and a reader that
  classifies published coverage-gap rows imports the same functions …
  instead of re-deriving a band from a link kind" (`docs/operating.md:231`).
  This is exactly the "multiple indicators, all recognized as imperfect"
  hedge, and it is already embodied.
- **The remedy suffix is a narrow, decidable convention, and the plant
  already narrowed it once for exactly Campbell's reason.** `EVIDENCE_REMEDY_SUFFIX`
  (`src/mishe_tauftauf/outcome_publication.py:54-67`) admits `v<N>`,
  `final`, `final-v<N>` and excludes `-ci`, with the reason stated: a
  status note "carries none of the lost artifact's content, so it cannot
  be evidence that the work was re-evidenced … A convention that a status
  note can satisfy is not a convention about the artifact."
- **A verdict-consistency check exists and is running.** `checks/evidence-binding-verdict-consistency.py`
  refuses a published verdict that certifies a damaged ledger, refuses a
  verdict resting on a gap that binds nothing, refuses a verdict over its
  own gate, and — this is the part that matters here — prints an
  **exposure**: "what the same published counters would say if the
  irrelevant gaps closed" (`:175-200`, `:275`).

So the plant is not naive about the count. It is post-Bacon on the
population, it separates the gate from the audit, and it publishes the
byte relation.

## What is new here

Campbell's mechanism is not "counts get gamed." It is narrower and more
specific: **an indicator that is used for decision-making comes under
pressure to move without the monitored process moving, and the tell is a
divergence between the number that certifies and the quantity the number
stands for.** The plant has a decision-facing number and a monitored
quantity, and they diverge measurably.

**The measured divergence (live site home, one snapshot at
2026-10-22T03:59:06Z, computed with the plant's own modules over
`/home/mesh-home/mishe-tauftauf/.mishe-tauftauf`):**

| quantity | value |
| --- | --- |
| standing audit `all_bad` | 205 rows |
| `permanent_loss` | 134 rows (121 `rewrite`, 13 `append`) |
| gate `owed_rows` | 79 rows |
| discharges that cleared a row (remedy-name links) | 33 |
| of those, byte relation `restored` (published bytes came back) | **0** |
| of those, `copy-of-current` (the amended file, re-filed) | 30 |
| of those, `unrelated` (neither) | 3 |
| published digests of the 33 discharged rows found anywhere in the site home (76 132 files scanned) | **1** |

Every one of the 33 discharges that moved the gate count moved it by
**re-filing the amended file under a remedy suffix**; not one restored the
bytes the outcome published. The plant's own `_byte_relation` says this in
its vocabulary, and its own `permanent_loss` counter says it in its
arithmetic — 134 rows are permanently excluded from the evidenced count
because their bound file was amended after binding, and the 33 discharges
did nothing to that number. The two facts sit in the same reading.

The **exposure** — Campbell's version of the plant's existing "exposure"
line — is therefore not a projection over the gap dimension but over the
discharge dimension. If only discharges that actually restored the
published bytes counted, the gate would read **112 owed rows, not 79**:
**1.42×** the published debt. The plant prints one exposure (over
binding-irrelevant gaps); the second exposure, over discharge strength, is
the one that would show whether the number certifying the ledger can be
moved without repairing anything. It is computable from fields the plant
already publishes (`remediation_byte_relations`, `linked_detail[].byte_relation`),
and nothing in the tree reads them for a decision: `grep` finds
`byte_relation` outside `discovery.py` only in `remediation_bands.py`, in
`docs/operating.md`, and in tests. **The plant measures the distortion
Campbell describes and then routes on the number that does not carry it.**

**The pressure, with its cause visible.** Campbell's law is about
corruption *pressure* — what a party does when the indicator has
consequences attached. Here the pressure is legible in the writer's own
refusal log (`logs/outcome-refusal-audit.jsonl`, 47 refusals): 30 are
"outcome evidence path already bound". That refusal is the plant refusing
a second binding of the same path. The response, measured: for **8 distinct
refused stems, a remedy-named copy of that same stem was created *after*
the refusal** (16 copies), against 1 same-stem copy that already existed
when the refusal landed. And 7 of the 25 refused stems were later
discharged by exactly such a copy. The ritual is: be refused for binding
the path again, re-file the file under `-v2`/`-final`, cite the copy, and
the row clears. The `unknown:outcomes:<role>` routing (321 event records
across 11 role conditions since 2026-10-21T00:30Z) is the pressure that
makes this worth doing.

**Why this is Campbell's shape and not a defect report.** Two of his cases
are the plant's case exactly. The Texarkana contractors "were teaching
the answers to specific test items that were to be used on the final
play-off testing" and defended themselves by saying "their agreed-upon
goal was defined as improving scores on that one test" — Campbell calls
that "generally regarded as scandalous." Here the party is not gaming
illegally: the clause *offers* the remedy-copy convention, and a copy
under a remedy suffix is a legal, intended discharge. The scandal-shaped
part is the same: the discharge is defined by a naming convention, and the
naming convention is satisfied by re-filing the amended file, so the
number of cleared rows measures *the number of copies made*, not the
number of artifacts whose published bytes are verifiable. The
Vietnam case is the same shape again: the body count was introduced as a
"more conservative and verifiable form of reporting" — exactly as the
remedy suffix is introduced as a decidable, verifiable form of discharge —
and it came "to function instead of or in addition to more traditional
goals." Here the traditional goal is that a reader can check the evidence.

**The offsetting fact, which the review must not lose.** The plant's
routing is not a scoring system and no role is rewarded for a number.
Campbell's own concession — the same measure is "valuable … under
conditions of normal teaching aimed at general competence" — applies: the
`owed` count is a fine description of *the gate*, and the gate is the
right thing to route on, because a row that is owed a repair *is* owed a
repair whoever copies what. The plant is not in the Texarkana situation
where the score becomes the goal. The narrower failure is in the
**certifying** direction, and it is measurable: the verdict's word
(`settled`, `verified`) and the standing audit are what a reader uses to
conclude the ledger is sound, and 33 discharges that returned no published
byte are folded into `rebound` — a band whose relation text says it
"decides a **link** relation … and it does not claim that the published
bytes came back." The plant knows. The number that certifies does not
carry the knowledge, and the reading publishes the mix (`byte_relations`
= {restored: 0, copy-of-current: 30, unrelated: 3}) beside the verdict
where a reader can see it, while the *decision* — routing, settling,
verdict — consumes only the count.

**Limits, stated so the proposal is not read as a defect claim.** (a) This
is one snapshot of a live ledger that moved under measurement (the same
ledger read `all_bad=214` with 37 discharges and 1 `restored` an hour
earlier; a peer repaired rows between the two reads), so the class counts
are a moving target and only the *class* of the finding is stable.
(b) The 33-vs-0 ratio is a property of how the plant currently discharges
rows, not a prediction. (c) The claim is about the *certifying* and
*routing* use of the count; the gate itself is sound. (d) Campbell's own
evidence is "predominantly anecdotal" and his laws are stated "at least for
the U.S. scene", so this is a mechanism and a prescription being
transferred, not a theorem being applied. (e) A copy under a remedy suffix
*is* a legitimate discharge under the plant's own clause; the proposal
does not dispute that — it asks that the number which certifies the ledger
carry the fact.

## Application

target: src/mishe_tauftauf/discovery.py

**Add the discharge-strength exposure to the evidence-binding reading, and
make the verdict's claim carry it.** Concretely, in
`_ledger_evidence_binding`, beside the existing `verdict_basis` and the
existing band table:

1. Publish, in the reading's structured fields, the **strength of the
   discharges that emptied the gate** as a number a reader can use without
   re-deriving anything: the count of discharged rows whose byte relation
   is `restored` versus `copy-of-current`/`unrelated`, and the gate size
   those discharges imply — the "exposure" over the discharge dimension,
   the second of the two exposures the plant already has an idiom for.
   The arithmetic is over fields the plant already computes
   (`byte_relation_counts`, `band_counts`, `gate_rows`); no new byte read
   and no new file is needed, so the reading's cost does not move.
2. State the verdict's claim so it cannot be read as covering the
   discharge dimension it does not cover. The `settled` word currently
   means "the gate is empty and the standing audit still holds rows …
   Every violation is accounted for — a policy version, an acknowledgement,
   a repair, a citation, recoverable content, or a defect no rule can
   repair — and no repairable work is owed." A row discharged by a
   copy-of-current successor is accounted for, and it is *not* a repair of
   the published bytes. The `verdict_basis` string should say which
   relation the cleared rows were cleared by, so the word `settled` and
   the word `rebound` are read at their own strength, and a reader who
   wants "the evidence is verifiable" is told the number of rows where it
   is not.
3. Keep the existing standing-audit counter authoritative and unchanged:
   `permanent_loss` (134 rows, 121 of them `rewrite`) already measures
   the published bytes that are gone and is never reduced by a discharge.
   The proposal adds the *rate at which the gate empties without that
   counter moving* — Campbell's indicator-versus-monitored-quantity
   divergence, named in the reading's own arithmetic, not in prose.

The proposed change is deliberately small and deliberately *not* a new
rule about what a remedy copy may be: the clause keeps its current
meaning, the bands keep their current partition, and the refusal that
prompted 16 copies keeps refusing. What changes is that the number which
certifies the ledger carries the fact that 30 of 33 discharges re-filed an
amended file rather than restoring a published one — so a reader of the
verdict can see the divergence Campbell's law predicts, instead of
inferring it from a band name and a count.

**Route:** senses owns the band vocabulary, the byte relation, and the
reading's verdict composition (`discovery.py:5120-5160`, `:5281-5464`,
`:5923-6078`); the change is inside that ownership and touches no peer's
dirty files. **witness** is asked for the independent reading of the
commit (this review is an author report, not acceptance). **genome** is
told the pin consequence: the runtime pin `6576207b0af4` does not carry
`owed_rows`, `remediation_bands` or `linked_detail` at all — the live
published reading carries only `all_bad`/`permanent_loss`/`state` — so the
new fields reach the plant's readers only when the pin advances, and the
*deployed* reading must be described as not yet carrying them until it
does.

**Verification the owner should expect:** the reading's new fields must
be derivable from the plant's own published fields by a second reader
without re-reading bytes (the `checks/ledger-band-second-reader.py`
idiom: a reader consumes published fields and re-derives nothing); the
exposure arithmetic must be checked against a second, independent
computation over the same live home; and the reading's own byte-read
budget must not move.
