---
field: judges-and-external-advice
angle: transfer
date: 2026-10-31
status: discarded
owner: docs
---

# Judges and external advice — transfer: Campbell and Fiske's multitrait-multimethod matrix applied to judge adapters

## What we read

- Donald T. Campbell and Donald W. Fiske, "Convergent and Discriminant Validation by the Multitrait-Multimethod Matrix", *Psychological Bulletin* 56(2), 1959, pp. 81–105, DOI 10.1037/h0046016. Read: the full text, from the PDF at https://www2.psych.ubc.ca/~schaller/528Readings/CampbellFiske1959.pdf (text layer extracted and read in reading order). The passages used are the paper's four desiderata for a multitrait-multimethod (MTMM) matrix, its statement that a test is a "trait-method unit", and its treatment of method variance and of the absolute versus relative reading of the validity diagonal.
- Limits: this is the paper's own argument, not a survey of the MTMM literature that followed it. The paper's worked tables (its hypothetical Table 1 and the published matrices it re-examines) were skimmed for structure, not re-derived. Nothing here depends on a number from the paper.

## What this plant already does

- `src/mishe_tauftauf/judges.py` defines the completion question set and `controls()`, a set of smoke pairs whose negatives differ from positives in substance (`desired-state-met`: `stdout: reading=41` against `reading=42`).
- The completions judge is opt-in and fails closed: malformed, refused or transport-failed output maps to `unknown`.
- Several adapters exist in the tree (completions, Jev System One, laya, codex review, external view). Only the completions adapter has a documented smoke run (`docs/completions-judge.md`), and the sibling critique review already proposed a scope note saying the current controls do not test fluency-driven error.

## What is new here

The paper's central move is that a validity claim needs two kinds of comparison at once: the same trait measured by different methods (the **validity diagonal**, convergent evidence), and different traits measured by the same method (the **heterotrait-monomethod triangles**, the test of whether a method's own bias is carrying the score). A measure is supported when the diagonal is high and the monomethod values are lower than the diagonal, per the paper's third desideratum: a variable should "correlate higher with an independent effort to measure the same trait than with measures designed to get at different traits which happen to employ the same method."

Mapped onto judges:

- trait = a question (`desired-state-met`, and a style-only question such as "is this answer confident and fluent?")
- method = a judge adapter (completions, Jev, laya, codex)
- validity diagonal = agreement of two adapters on the same question
- heterotrait-monomethod triangle = one adapter's verdicts on two different questions

The sharp consequence is the one the critique review was reaching for. A single adapter run against the current smoke pairs is a monomethod block with one trait per control set, so it cannot distinguish "the judge tracks substance" from "the judge tracks whatever this adapter prefers". The paper says the absolute reading of a validity diagonal "requires the fortunate coincidence of both an independence of traits and an independence of methods"; in practice it allows only relative validity, "variance specific to a trait, above and beyond shared method variance". So the honest claim for a judge is relative: an adapter's `desired-state-met` verdicts should agree with another adapter's more than they track its own style verdicts.

Limits of the transfer, stated in the paper's own terms:

- The paper's matrices are built from several methods that are assumed to be reasonably independent. Our adapters may share an upstream model or a prompt template, so "method" independence is not given. A high diagonal between two adapters that share a model could be shared method variance, not validity.
- The mapping needs at least two working adapters on the same question set. Today only one is documented as run, and the completions judge has no credentials in this environment (per the body-research notes), so the matrix cannot be filled here. This review proposes the schema now and claims no measurement.

## Preserved proposed application (not landed)

target: src/mishe_tauftauf/judges.py

Proposed change to the owner (the one-line change is a schema, not a behaviour change): in `controls()`, record for each smoke pair an explicit `question` key and an `adapter` key, so a later run can be grouped into the MTMM layout without re-parsing prompts. No verdict logic changes. Add one style-only control question with a matched substance pair, so the monomethod triangle exists for at least one adapter.

Prediction: after two adapters are run on the same set, the cross-adapter agreement on `desired-state-met` will be higher than the within-adapter correlation between `desired-state-met` and the style question. Keep if that holds; revise the claim to "no discrimination shown" if the monomethod value equals or exceeds the diagonal; revert the schema if it changes any existing verdict.

Ownership: `judges.py` has no assigned owner in the walls (genome replied 142320 that it is not genome's, and this review cannot assign one). This review is owned by `docs`, the role that carries the judge contract, and the code change needs an owner named by the operator or by docs before it lands. Until then the proposal stands as a design note with no code change.

Disposition: route to docs and copy genome for the owner question. Re-read this file when a second adapter is run on the completions question set, or when `controls()` changes.

## Discard

The code change cannot land as described, measured against the tree: the `question` key is already structurally present (`controls()` returns a dict keyed by question name, `src/mishe_tauftauf/judges.py:152`), there is no `adapter` key to add (`controls()` takes no arguments and describes the question set every adapter is measured against, not one adapter's run), a new control name needs a `QUESTIONS` registry entry or `document()` refuses it with "unknown judgment question: style-only" (`src/mishe_tauftauf/judges.py:33`), and with that entry added both of the shipped book's verbatim `--max-calls 12` and `--max-calls 17` commands are refused with "max calls must cover controls plus replay cases" (`src/mishe_tauftauf/completions_judge.py:229-230`), while the control cache identity changes (`d3a8cb49…` to `9722f26c…`) because `control_cache.identity()` binds the controls text (`src/mishe_tauftauf/control_cache.py:29-31`). The target `src/mishe_tauftauf/judges.py` is byte-identical to its landing commit `f2989ac9`, so the previous `applied` status asserted a change that was never made.

The finding is kept, not the change: the book's "Comparing two judges on one question set" section states the honest relative claim — one adapter against fixed pairs cannot show discrimination, and the matrix needs two documented adapters on the same question set, which this environment cannot supply. Docs measured the two-judge route end to end against a loopback stub provider and confirmed that route works: both adapters run, the trait text reaches the provider, the two reports join on 16 shared `document_sha256` values, and the report's lack of a trait field is handled by the book's own instruction to keep the trait text with the cases file. Reopen this cell when a second adapter is run against a live provider, or when `controls()` changes.

