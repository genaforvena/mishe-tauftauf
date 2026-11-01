---
field: evidence-and-verdicts
angle: misread
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/discovery.py
---

# Evidence verdicts: a clean audit is absence of detected violation, not absence of violation

## What we read

Douglas G. Altman and J. Martin Bland, "Statistics notes: Absence of evidence is not
evidence of absence", *BMJ* 1995;311:485 (19 August 1995),
doi:10.1136/bmj.311.7003.485:

- https://www.bmj.com/content/311/7003/485

Read through the abstract and the citing excerpts returned by a search pass, not a
full-text reading of the note. The points used below are the ones those excerpts carry:

1. A non-significant result typically shows that evidence of a difference is lacking,
   not that no difference exists; calling such a trial "negative" misleads readers.
2. Power decides how much a null can mean. The note cites a review in which only about
   30% of sampled trials had power to detect a 50% difference. A null from an
   underpowered test says little about the effect.
3. We can never prove the absence of a relation directly; the note suggests seeking
   evidence against the link from a design able to see it.

Re-check the exact wording against the full note before a change lands on its strength.

## What this plant already does

- **The `verified` state is the top of a ladder that already refuses soft labels.**
  `src/mishe_tauftauf/discovery.py:7146` says `verified` is "the standing audit holds no
  violating row at all", and the verdict docstring at `:6679` adds "no violating row and
  no binding-relevant gap". The `settled` branch (`:7180` onward) says it "is not
  `verified`: the ledger's history is not clean, and the word says so". The plant has
  already taken the lesson that a clean word must not borrow a clean ledger's strength.
- **The gap path is real.** `binding = blocking_gaps(coverage)` (`:6939`) turns an
  evidence path the audit cannot bind into `unknown`, not `verified`. An unseen
  evidence path is counted as unknown.
- **What the code does not say is the detector's own reach.** The violation rules the
  standing audit applies decide `violations` (and so `gate_rows`). A row that no rule
  classifies is never counted, and that absence is not recorded in the verdict. The
  verdict basis at `:7180` lists the bands (policy version, acknowledgement, repair,
  citation, recoverable, permanent defect, owed) but not which violation classes were
  checked. Nothing in the published word says a clean audit is clean against those
  classes only.

## What is new here

- **The plant's `unknown` covers unseen evidence paths, not unseen violation classes.**
  That is the first gap, and the code handles it. The second is a null from a test
  of a fixed set of rules. Altman and Bland's point 2 is the analogue: a null from a
  low-power test is not evidence of absence. Here the "power" of the standing audit is
  the set of violation rules it runs. A clean `verified` says no row broke a rule the
  audit has, not that no row broke a rule the audit lacks.
- **The mistake is reading the word as the second claim.** Altman and Bland's point 1
  names the same failure in trials: a negative label that readers take as proof of no
  effect. A reader who sees `verified` on the pane can read it as "the ledger has no
  violations", which the code does not claim.
- **Limits of the transfer.** A clinical trial's power is a sample-size calculation;
  the audit's "power" is a rule set with no measured recall. We have no false-negative
  rate to quote, so the analogy names a gap and gives no number. The proposal does not
  claim the audit misses violations, only that the word does not say what it covers.

## Application

target: src/mishe_tauftauf/discovery.py

**Change: a comment at the `verified` assignment, `discovery.py:7146`, naming the rule
classes the audit checks and stating that a clean result is clean against them only.**
Proposed comment text, for the owner to place and word (comment only, no code path
changes):

> `verified` here means no row violates a rule this audit runs. It does not mean no
> row violates any rule. A rule the audit does not run cannot produce a violating row,
> so a clean result says nothing about it.

**Acceptance, checkable on the committed bytes.**

- `grep -n "a rule this audit runs" src/mishe_tauftauf/discovery.py` finds the sentence
  beside `:7146`.
- No value, state name, threshold, band or emitted word changes. The `verified` string
  stays byte-identical, so the consistency checks see no difference.
- The owner lists the violation classes the audit actually runs. That list, not this
  review, is the authority on coverage; if it is short, the wording should say so.

**What this proposal does not do.** It does not add a detector, change the rule set, or
measure a false-negative rate. Those are the senses owner's choices. A follow-up for a
recall measure (for example, a planted-violation check that must be caught, in the
style of the controlled-failure rule in the doctrine) is a separate proposal, not made
here.

**Owner:** senses (the standing audit and the verdict state derivation in `discovery.py`).
