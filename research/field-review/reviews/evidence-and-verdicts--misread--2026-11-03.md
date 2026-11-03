---
field: evidence-and-verdicts
angle: misread
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/discovery.py
---

# Evidence verdicts: settlement of opinion is not convergence on the Real

## What we read

Charles S. Peirce, "The Fixation of Belief", *Popular Science Monthly* 12 (November 1877), 1-15, and its sequel "How to Make Our Ideas Clear", *Popular Science Monthly* 12 (January 1878), 286-302.

- https://www.peirce.org/writings/p107.html — fetched 2026-11-03 as `/tmp/peirce-fixation.html` 42573 B sha256 `a863f3910a6683b904769f48334a32ada22e9be998b1fd6f22ddc9a15df4caac` (HTML, anchors CP5.358-CP5.384). Text verified by tag-stripped extraction.
- https://courses.media.mit.edu/2004spring/mas966/Peirce%201878%20Make%20Ideas%20Clear.pdf — fetched 2026-11-03 as `/tmp/peirce-ideas.pdf` 177482 B sha256 `009ed9f265fc68deb243e861c59b50c54cd280e37eb947a4bbd6a16eedc4a19f` (6 pp. scan of Peirce 1878, matching p107 sequel). Text verified via `pdftotext` at `/tmp/peirce-ideas.txt` 46782 B, containing the pragmatic maxim and III-IV excerpts quoted below. p108 at https://www.peirce.org/writings/p108.html returned 404 at fetch time; the MIT PDF is the same text (Popular Science Monthly 12, 286-302) and is cited instead.

Read cue: complete read of Fixation §I-V (pp. 1-15) plus §§II-III of Ideas Clear (pp. 286-294) covering the maxim, its illustration (hardness, weight, force, reality), and the method taxonomy. Not re-read line-by-line: Fixation's historical survey of scholastic/Roger/Lord Bacon and Ideas Clear's extended force-parallelogram and transubstantiation digressions beyond the quotations below. No claim relied on lies in the skipped scholastic history.

Verbatims used (checked against the live extracts):

1. Doubt vs belief as habits: "Doubt is an uneasy and dissatisfied state from which we struggle to free ourselves and pass into the state of belief; while the latter is a calm and satisfactory state which we do not wish to avoid, or to change to a belief in anything else." (Fixation CP5.372).
2. Inquiry as irritation: "The irritation of doubt causes a struggle to attain a state of belief. I shall term this struggle inquiry" (CP5.374). "The irritation of doubt is the only immediate motive for the struggle to attain belief" (CP5.375).
3. End of inquiry: "That the settlement of opinion is the sole end of inquiry is a very important proposition." (CP5.375). The object of reasoning is to find out "from the consideration of what we already know, something else which we do not know" and validity is whether "if A were B would generally be" — a question of fact, not feeling (CP5.365).
4. Belief as habit: "The essence of belief is the establishment of a habit; and different beliefs are distinguished by the different modes of action to which they give rise." (Ideas Clear §II, p. 290). "The whole function of thought is to produce habits of action; and … whatever there is connected with a thought, but irrelevant to its purpose, is an accretion to it, but no part of it." (Ideas Clear §II, p. 291).
5. Pragmatic maxim (third grade of clearness): "Consider what effects, that might conceivably have practical bearings, we conceive the object of our conception to have. Then, our conception of these effects is the whole of our conception of the object." (Ideas Clear §II, p. 292). Corollary: "what a thing means is simply what habits it involves" (p. 291) and habit depends on "how it might lead us to act, not merely under such circumstances as are likely to arise, but under such as might possibly occur, no matter how improbable they may be" (p. 291).
6. Four methods of fixing belief (Fixation §V): method of tenacity (hold what pleases, reiterate, turn from disturbance), method of authority (institution that "shall have for its object to keep correct doctrines before the attention of the people, to reiterate them perpetually"), the a priori method (agreeable to reason / fashion — "pendulum has swung backward and forward between a more material and a more spiritual philosophy"), and the method of science.
7. Science hypothesis vs other methods: "Such is the method of science. Its fundamental hypothesis, restated in more familiar language, is this: There are Real things, whose characters are entirely independent of our opinions about them; those Reals affect our senses according to regular laws, and, though our sensations are as different as are our relations to the objects, yet, by taking advantage of the laws of perception, we can ascertain by reasoning how things really and truly are; and any man, if he have sufficient experience and he reason enough about it, will be led to the one True conclusion." (CP5.384, as extracted: "There are Real things, whose characters are entirely independent of our opinions about them; those Reals affect our senses according to regular laws… will be led to the one True conclusion.").

## What this plant already does

- **The verdict vocabulary is a settlement vocabulary.** `src/mishe_tauftauf/discovery.py:6677-7158` defines four words: `verified` ("no violating row and no binding-relevant gap"), `drift` ("gate holds at least one row … measured and known"), `settled` ("gate empty and standing audit still holds rows … not `verified`: the ledger's history is not clean"), `unknown` ("coverage gap can still hide a bound evidence path"). The comment at `:7146-7150` (landed 2026-11-01 predecessor) already adds the Altman corrective: `verified` is "clean only against the violation rules this audit runs." The bands at `:6698-6750` and `verdict_basis` at `:7186-7198` publish the strength of discharges and the partition of the standing audit, so a reader can see what `settled` is settled by.
- **Settlement here is by method: a concrete audit.** The standing audit's method is `binding = blocking_gaps(coverage)` turning unseen evidence paths into `unknown`, `checked` via `_file_digest` / `eligible_evidence_path` / `path_counts`, class-B amendment typing (`append`/`rewrite`/`unknown`), and `classify_remediation_band` for the seven bands. The reading correctly treats `unknown` as "could not be determined" and refuses to borrow `verified`'s strength. The 2026-11-01 `recent` REVISIT (SCITT, `verdict_coverage.py:84-87`) and the standing comment separate receipt/inclusion from truth of statement.
- **What the code does not yet say is the Peircean gap between settlement and Real.** The comments name rule coverage and receipt vs statement, but not method-of-fixation: a settlement can occur by tenacity (repeat the word), by authority (the detector says so), by a priori taste (the ledger looks agreeable), or by science (external constraint). All four settle doubt and establish a habit, but only the last is hypothesized to converge on the Real independently of opinion. The plant's `verified` is a settlement by this audit's method now; it is not a claim that inquiry, pushed far enough by any investigator with sufficient experience, would converge on the same verdict. The pragmatic maxim's consequence — meaning is the conceivable practical bearings (what habit the word licenses) — is also not stated beside the verdict definition, though the code already separates `verified` from `settled` to prevent mis-licensed action.

## What is new here

- **Settlement ≠ truth; method matters, not just rule coverage.** The predecessor misread (Altman & Bland 1995 → `discovery.py:7146`) handles the first dimension: a null can mean low power (few rules), not absence. Peirce handles the second, orthogonal dimension: a settlement can be sincere and stable and still not track the Real, because tenacity, authority and the a priori also settle opinion — by managing irritation rather than by being constrained by something independent of opinion. The sentence we have misread is of that form: "the audit is verified" taken as "no violation is Real" (a description of the world) when Peirce would locate it as "no violation produced irritation under this method" (a habit: act as if no repair is owed, file no new outcome, close the pane as green).
- **The maxim relocates meaning from sentence to habit.** "What a thing means is simply what habits it involves" and "what the habit is depends on when and how it causes us to act, … not merely under such circumstances as are likely to arise, but under such as might possibly occur." The test for whether `verified` and `settled` mean different things is therefore not whether their sentences look different but whether they license different conceivable actions. Today they do: `drift` → repair is owed, `settled` → no repairable debt though history is not clean, `verified` → nothing owed and nothing hidden by a gap. The maxim makes that habit-bearing explicit, and it explains why the 2026-11-01 comment about rule coverage matters: without it, a reader's habit on seeing `verified` ("treat the ledger as clean") would be the same habit a true but unattested clean ledger would license — two different settlements would produce the same mode of action, which by the maxim are one and the same belief, wrongly fixed.
- **Limits of the transfer (so the change stays a comment).**
  - Fixation is a 1877 taxonomy of opinion-settlement, not a protocol for a detector. We do not claim this audit is "unscientific" or that another audit is; we claim only that the vocabulary must not invite the move from settlement-by-this-method to convergence-on-the-Real.
  - Peirce's science hypothesis ("Real things … affect our senses according to regular laws") does not map to a liveness claim about this ledger: the Real for this sense is the bytes on disk, which do affect the audit via re-hash, but there is no long-run convergence theorem for this audit's bands — only a bounded, practiced reading.
  - The maxim does not demand that every concept be operationalized as a counterfactual; it demands that its practical bearings be conceivable. The bearings named here — republish with new artifact, carry forward under remedy name, cite digest — are already the plant's bands; the proposal only names the maxim beside them.
  - No new detection method, threshold, state word, or file-system read is proposed. Signing, recall measurement, and planted-violation tests (149075) remain separate decisions owned by senses.

## Application

target: src/mishe_tauftauf/discovery.py

**Change: additive comment beside the verdict's own basis (`discovery.py:6677-7158`, at the `verified`/`settled`/`drift`/`unknown` word definitions and at `verdict_basis` at ~:7186), naming Peirce's four methods and the pragmatic maxim as the reason the words differ — no code path, value, threshold, or emitted word changes.**

Proposed comment text, for the owner to place and word (comment only):

> Fixation vs Real (Peirce 1877/1878): settlement of opinion is the sole end of inquiry, but settlement can occur by tenacity, authority, the a priori, or science — all settle doubt, only the last is hypothesized to converge on the Real independently of what anybody thinks. `verified`/`settled`/`drift`/`unknown` name settlements by this audit's method now, not convergence in the long run. By Peirce's maxim — "Consider what effects, that might conceivably have practical bearings, we conceive the object of our conception to have. Then, our conception of these effects is the whole of our conception of the object" (Ideas Clear, Jan 1878) — their meaning is the habits they license: `drift` licenses repair, `settled` licenses no repairable debt though history remains, `verified` licenses acting as if no repair is owed and nothing is hidden by a gap. A different settlement that licenses the same habit is not a distinct belief.
>
> Predecessor: Altman & Bland 1995 misread (`discovery.py:7146` "clean only against the violation rules this audit runs") handles coverage; this note handles method — Altman says what the null is clean against, Peirce says what kind of settlement a clean null is.
>
> Sources: Peirce, Fixation (Popular Science Monthly 12, Nov 1877) https://www.peirce.org/writings/p107.html (irritation of doubt / settlement of opinion / four methods / Real hypothesis) and Ideas Clear (Popular Science Monthly 12, Jan 1878) https://courses.media.mit.edu/2004spring/mas966/Peirce%201878%20Make%20Ideas%20Clear.pdf (pragmatic maxim, habit).

**Acceptance, checkable on the committed bytes (no behavior change):**

- `grep -n "Consider what effects, that might conceivably have practical bearings" src/mishe_tauftauf/discovery.py` finds the maxim sentence quoted verbatim beside the verdict definitions (with the Ideas Clear URL on a nearby line).
- `grep -n "settlement of opinion is the sole end of inquiry" src/mishe_tauftauf/discovery.py` finds the Fixation line beside `verdict_basis`, with URL `https://www.peirce.org/writings/p107.html` on a nearby line.
- `grep -n "tenacity.*authority.*a priori.*science" src/mishe_tauftauf/discovery.py` (case-insensitive) finds the four methods named together in the same comment block.
- No value, state name, threshold, band or emitted word changes; the strings `verified`, `settled`, `drift`, `unknown` stay byte-identical, so consistency checks see no difference.
- `python3 -m py_compile src/mishe_tauftauf/discovery.py` and `python3 tools/check_parsing.py` (if present) still pass.

**What this proposal does not do.** It does not add a detector, change the rule set, measure recall, or claim this audit is (or is not) the scientific method. Those are the senses owner's choices. A follow-up for an independent convergence check (e.g., a second reader re-deriving bands from `row_bands`/`linked_detail`, as `discovery.py:6679` already enables) stays separate and is not made here.

**Owner:** senses (the standing audit and the verdict state derivation in `discovery.py`).

**Prediction:** Naming Peirce's methods and the maxim beside `verified`/`settled` makes the habit licensed by each word auditable at the definition site, without changing any published bytes, and reduces the misreading of a method-local settlement as a ledger-wide truth claim.

**Falsifier:** If a second reader can correctly state "verified is settlement by this audit's method, not convergence on the Real, and its meaning is the repair habit it licenses" from the current `discovery.py` comments without this note — i.e., the file already separates fixation from Real by quotation or docstring — then the gap is already closed and this proposal is a duplicate.

**Predecessor:** `evidence-and-verdicts--misread--2026-11-01` (Altman & Bland 1995 → `discovery.py:7146` rule-coverage comment) and `evidence-and-verdicts--recent--2026-11-03` (SLSA Provenance v1.1 → `outcome_publication.py` external-parameter vs resolved-digest split). This REVISIT keeps both words and field limits intact and adds the method-of-fixation limit beside the verdict words.
