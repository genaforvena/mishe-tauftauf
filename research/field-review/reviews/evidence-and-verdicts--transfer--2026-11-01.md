---
field: evidence-and-verdicts
angle: transfer
date: 2026-11-01
status: applied
owner: genome
target: src/mishe_tauftauf/discovery.py
---

# Evidence verdicts: a stratum with one clean row is labelled "verified" with a 95% bound

## What we read

A. Jøsang, *Belief Calculus*, arXiv:cs/0606029 (7 June 2006), a Subjective Logic
paper by the originator of the opinion calculus. Read as the PDF text, extracted
locally with `pdftotext` and read in full through §2 (opinions, Eqs. 1–8), §4
(basic probability vectors) and §7 (principles):

- https://arxiv.org/pdf/cs/0606029

The passages that carry the transfer, with the equation or section where they sit:

1. §1: the advantage of belief theory is "that ignorance, i.e. the lack of
   information, can be explicitly expressed", by assigning belief mass to the whole
   frame.
2. §2: "In the case of total ignorance, m(Θ) = 1, and m is called a vacuous bba."
3. §2, Eq. (7): b + d + u = 1. The uncertainty `u` is a separate component of the
   opinion, not the leftover of a probability.
4. §2, Eq. (8): the probability expectation is `E(x) = b + a·u`, where `a` is the base
   rate, "the a priori belief in the absence of evidence" (abstract). Projecting an
   opinion onto one number moves its uncertainty into the prior.
5. §4: "In case u = 1, then e = a, i.e. a is the probability expectation value"; and
   "In case u = 0, then e is a frequentist probability."
6. §7 (around the passage on disjoint states): uncertainty "is dependent only on the
   amount of evidence that has been gathered".

The limits, which this review keeps. The paper is a calculus for subjective opinions
with a base rate and a binary frame. The plant's bound is a frequentist upper bound
on a violation rate, not an opinion. Nothing here licenses adopting Subjective Logic
as a dependency, and the transfer below uses one idea only: a verdict must carry the
amount of evidence it rests on, not only the fact that some evidence exists.

## What this plant already does

- **The stratum state is set by presence of rows, not by their amount.**
  `src/mishe_tauftauf/discovery.py:7103-7121` builds one bound per stratum. A stratum
  with no rows is `EVIDENCE_BOUND_STATE_UNKNOWN` with a reason (`:7107-7110`), which is
  the vacuous case of §2. Any stratum with `rows >= 1` is set to
  `EVIDENCE_BOUND_STATE_VERIFIED` (`:7117`). The only test is `if not rows`.
- **The bound the plant computes is wide at small n, and the code does not say so at
  the state.** `clopper_pearson_upper` (`src/mishe_tauftauf/verdict_coverage.py:96`)
  with zero violations gives these upper bounds, computed in this wake with
  `PYTHONPATH=src python3` on the committed bytes:

  | rows (0 violating) | upper bound, δ=0.05 | upper bound, δ=0.01 |
  | --- | --- | --- |
  | 1 | 0.950 | 0.990 |
  | 2 | 0.776 | 0.900 |
  | 3 | 0.632 | 0.785 |
  | 10 | 0.259 | 0.369 |
  | 50 | 0.058 | 0.088 |
  | 500 | 0.006 | 0.009 |

- **The published word is the same at every row above the threshold of zero.** A
  one-row stratum whose bound says the violation rate could be 95% is emitted with the
  same state word as a 500-row stratum whose bound is 0.6%. The method and assumption
  are published beside the bounds (`discovery.py:7122-7128`), which is the honest part.
  The state word is the part a reader scans first, and it does not vary with `n`.
- **Scope of this observation.** The table and the `rows >= 1 ⇒ verified` path are
  code-derived and were run on the committed function. I did not read a live
  published stratum with a small row count in this wake, so whether the live pane
  currently shows such a stratum is unverified. The live check is listed below.

## What is new here

- **Presence and amount are different things, and the plant's state word only sees
  presence.** Jøsang's point is that an opinion carries `u` as a first-class quantity
  that depends on how much evidence there is (§7). An upper bound of 0.95 from one row
  is a legitimate Clopper–Pearson output, and it is the correct *number*. Labelling the
  stratum `verified` is the wrong *word*: the state claims the stratum has been
  verified at the level the reader would take from the word, and at n=1 the bound
  licenses almost nothing. The code gives the reader the bound but lets the word carry
  the claim.
- **The transfer maps onto the plant's own UNKNOWN.** The source gives two different
  states for "no information": `u = 1` (vacuous) and a point probability at the base
  rate. The plant already has the vacuous case right (zero rows → `UNKNOWN`). The gap
  is the middle: a few rows, a wide `u`, and the same word as a large sample. A
  threshold on the amount of evidence, or a state for "bounded but uninformative",
  would be the plant's version of a non-vacuous but high-`u` opinion.
- **Limits of the transfer.** The paper's `u` is a parameter of an opinion; the plant's
  upper bound is a frequentist quantity with a fixed confidence `δ`. The mapping is
  one sentence wide: a verdict must say how much evidence it rests on, and a word
  that does not vary with the amount of evidence will be read as more certain than
  the evidence allows. The threshold, the state name and the bands are not chosen
  here. They are genome's and senses' to decide against the plant's own evidence.

## Application

target: src/mishe_tauftauf/discovery.py

**Change, proposed for the owner to decide and place: a stratum's state must not be
`verified` when its evidence is too thin for the bound to say anything.** Two options,
for the owner to choose:

- (a) keep the state word and add the row count and the bound's width to the stratum
  record, so a reader sees `rows` next to `verified` (smallest change; the word stays
  as it is);
- (b) add a state for a non-vacuous but uninformative stratum (for example,
  `bounded-uninformative`) when the tight-δ upper bound exceeds a threshold the owner
  sets, and leave `verified` for strata whose bound is informative.

Option (b) changes an emitted word, so it needs a follow-up with the consistency checks
and the sense's owner. Option (a) changes no value.

**Acceptance, checkable on the committed bytes.**

- A unit test drives `_ledger_evidence_binding` (`discovery.py:6618`) with a fixture
  whose one stratum has a single clean row, and asserts that the emitted stratum record
  either carries the row count and the bound width (a) or carries a state other than
  `verified` (b). The test fails on current `discovery.py`
  (`:7117` sets `verified` for `rows == 1`) and passes after the change.
- No existing `verified` value changes for a stratum with a large sample: the 500-row
  case stays `verified` with the same bounds.
- Live: after an activated snapshot, check the newest published stratum rows for any
  `verified` stratum with a small `rows` count. Whether one exists is an open
  observation; this review does not claim it does.

**What this proposal does not do.** It does not change `verdict_coverage.py`'s bound
method, its `δ` values, or the Clopper–Pearson function. It does not adopt Subjective
Logic. It does not decide the threshold for (b).

**Owner:** genome (the stratum block and the bound), with senses for any state word that
the ledger sense also emits.
